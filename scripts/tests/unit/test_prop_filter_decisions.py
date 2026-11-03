"""Model-based property test for the Filter_Chain decision pipeline.

Property 8: Filter decisions match the decision table, are total, and are
deterministic.

For any Job_Record built from random title fragments (Excluded_Seniority_Terms,
"senior"/"sr"/level-III, primary role phrases, maybe phrases, the word
"analyst", tier words, junk words), random company slugs (blocklisted,
recruiter-token, ordinary), a random tier, a random location verdict
(primary-eligible, ``location_new_england``, ``remote_unspecified``,
``location_unknown``, ``non_us``, ``outside_radius``), and random
post-detail descriptions (PhD-required, a life-sciences signal, or neither),
the Filter_Chain SHALL:

- return the same Decision as an independent reference implementation of the
  precedence order and the title/location decision table;
- return exactly one Match_Type and one Match_Reason;
- set an Exclusion_Reason if and only if the Match_Type is ``excluded``;
- return an identical Decision when the pipeline is run twice on the same input.

The PhD check takes precedence over the industry/Category gate (the order of
Requirement 7.2), and both run between ``classify_pre_location`` and
``finalize_decision``.

Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10,
7.13, 7.14, 7.15, 7.18, 7.21, 7.22, 18.3, 18.4
"""

import copy

from hypothesis import given, settings
from hypothesis import strategies as st

import personal_config
from filters import (
    Decision,
    build_recruiter_detector,
    build_title_matcher,
    classify_post_detail,
    classify_pre_location,
    finalize_decision,
    requires_phd,
    life_sciences_signal,
    _is_research_title,
)


# ---------------------------------------------------------------------------
# Shared, deterministic filter inputs
# ---------------------------------------------------------------------------

_CFG = personal_config
_MATCHER = build_title_matcher(_CFG)
_RECRUITER = build_recruiter_detector(_CFG)


# ---------------------------------------------------------------------------
# Title fragment pools, chosen so each pool lands in a known title class.
#
# The TitleMatcher order is: senior_excluded -> title_excluded -> primary ->
# maybe -> generic_analyst -> no_match. The reference below classifies titles
# with the same helpers, so these pools only need to be *reachable*; the
# precedence is proven by running the real matcher, not by the pool labels.
# ---------------------------------------------------------------------------

# Excluded_Seniority_Terms (SENIOR_EXCLUDE_PATTERN). These win first.
_SENIOR_EXCLUDE_WORDS = [
    "Principal", "Director", "VP", "Vice President", "Head of",
    "Chief", "Distinguished", "Staff", "Senior Manager", "Group Manager",
]

# TITLE_EXCLUDE_PATTERNS fragments (but not also senior).
_TITLE_EXCLUDE_WORDS = [
    "Intern", "Internship", "Co-op", "Apprentice",
    "Security Analyst", "Cyber Analyst", "Credit Analyst",
    "Help Desk", "Service Desk", "m/w/d",
]

# Primary (non-research) role phrases. These match a non-research
# TITLE_PATTERNS entry so they are never industry-gated.
_PRIMARY_WORDS = [
    "Data Scientist", "Data Analyst", "Biostatistician", "Epidemiologist",
    "Analytics Engineer", "Data Engineer", "HEOR Analyst",
    "Health Economist", "Decision Scientist", "Machine Learning Engineer",
]

# Gated Research_Title phrases: match a research pattern and no other
# primary pattern, so classify_post_detail may industry-gate them.
_RESEARCH_WORDS = [
    "Research Data Analyst", "Clinical Research Analyst",
    "Market Research Analyst", "Commercial Insights", "Insights Analyst",
]

# MAYBE_PATTERNS phrases (and not primary).
_MAYBE_WORDS = [
    "Clinical Data Manager", "Data Manager", "Operations Research",
    "Data Strategist", "Clinical Research Coordinator",
]

# Generic-analyst: contains the whole word "analyst" but no primary/maybe.
_GENERIC_ANALYST_WORDS = [
    "Marketing Analyst", "Business Analyst", "Policy Analyst",
    "Supply Chain Analyst", "Analyst",
]

# No-match junk titles.
_JUNK_WORDS = [
    "Chef", "Janitor", "Barista", "Welder", "Plumber", "Nurse",
    "Account Executive", "Sales Representative",
]

# "senior"/"sr"/level-III prefixes that are NOT Excluded_Seniority_Terms.
# They should pass the title step and reach the tier rule as a maybe.
_SENIOR_SIGNAL_PREFIXES = ["Senior ", "Sr. ", "Sr ", ""]
_LEVEL_SUFFIXES = ["", " III", " II", " I"]

_ALL_TITLE_WORDS = (
    _SENIOR_EXCLUDE_WORDS
    + _TITLE_EXCLUDE_WORDS
    + _PRIMARY_WORDS
    + _RESEARCH_WORDS
    + _MAYBE_WORDS
    + _GENERIC_ANALYST_WORDS
    + _JUNK_WORDS
)


@st.composite
def _titles(draw):
    """Build a title from a role word plus an optional seniority prefix/level.

    Also yields empty / whitespace titles so the invalid-record branch and the
    totality claim (every input gets a Decision) are exercised.
    """
    if draw(st.integers(min_value=0, max_value=19)) == 0:
        return draw(st.sampled_from(["", "   ", "\t"]))
    word = draw(st.sampled_from(_ALL_TITLE_WORDS))
    prefix = draw(st.sampled_from(_SENIOR_SIGNAL_PREFIXES))
    suffix = draw(st.sampled_from(_LEVEL_SUFFIXES))
    return f"{prefix}{word}{suffix}".strip()


# ---------------------------------------------------------------------------
# Company slug pools: blocklisted, recruiter-token, and ordinary.
# ---------------------------------------------------------------------------

_BLOCKLIST_SLUGS = sorted(_CFG.COMPANY_BLOCKLIST) or ["lensa"]
_RECRUITER_SLUGS = [
    "acme-staffing", "talent-partners", "global-recruiting",
    "search-group", "people-solutions", "tech-consulting",
    "staff", "talent", "elite-placement-agency",
]
# Ordinary slugs chosen to contain no whole recruiter token. "research" is a
# deliberate near-miss: it contains "search" only as a substring.
_ORDINARY_SLUGS = [
    "acme-health", "biotechcorp", "iqvia", "research-triangle",
    "iqviaresearch", "globex", "initech", "pfizer", "moderna",
]


@st.composite
def _slugs(draw):
    pool = draw(
        st.sampled_from(["block", "recruiter", "ordinary", "ordinary", "ordinary"])
    )
    if pool == "block":
        return draw(st.sampled_from(_BLOCKLIST_SLUGS))
    if pool == "recruiter":
        return draw(st.sampled_from(_RECRUITER_SLUGS))
    return draw(st.sampled_from(_ORDINARY_SLUGS))


# ---------------------------------------------------------------------------
# Descriptions for the post-detail PhD and industry gates.
# ---------------------------------------------------------------------------

_PHD_REQUIRED_DESCS = [
    "A PhD is required for this role.",
    "This position requires a PhD in a quantitative field.",
    "You must have a PhD to apply.",
    "Doctorate required.",
]
_PHD_SOFT_DESCS = [
    "A PhD is preferred but not required.",
    "Master's or PhD in statistics.",
    "MS/PhD welcome; a PhD is a plus.",
]
_LIFE_SCI_DESCS = [
    "Work with clinical trial and pharma data.",
    "Oncology and real-world evidence analytics for a biotech.",
    "Support healthcare payer and provider analytics.",
]
_NEUTRAL_DESCS = [
    "",
    "Build dashboards and reports for the business team.",
    "Analyze marketing funnels and e-commerce metrics.",
]

_DESCRIPTIONS = st.sampled_from(
    _PHD_REQUIRED_DESCS + _PHD_SOFT_DESCS + _LIFE_SCI_DESCS + _NEUTRAL_DESCS
)


# ---------------------------------------------------------------------------
# Location verdicts (plain dicts, as finalize_decision reads via _loc_get).
# The six shapes named in Property 8.
# ---------------------------------------------------------------------------


@st.composite
def _loc_verdicts(draw):
    shape = draw(
        st.sampled_from(
            [
                "primary",          # clean, kept primary-eligible
                "new_england",      # Secondary_Region -> location_new_england
                "remote_unspec",    # remote_scope unspecified -> remote_unspecified
                "unknown",          # ambiguous/unknown status -> location_unknown
                "non_us",           # rejected non_us
                "outside_radius",   # rejected outside_radius
            ]
        )
    )
    if shape == "primary":
        return {
            "kept": True,
            "exclusion_reason": None,
            "secondary_region": False,
            "remote_scope": draw(st.sampled_from([None, "us"])),
            "location_status": "resolved",
        }
    if shape == "new_england":
        return {
            "kept": True,
            "exclusion_reason": None,
            "secondary_region": True,
            "remote_scope": None,
            "location_status": draw(st.sampled_from(["resolved", "unknown"])),
        }
    if shape == "remote_unspec":
        return {
            "kept": True,
            "exclusion_reason": None,
            "secondary_region": False,
            "remote_scope": "unspecified",
            "location_status": "resolved",
        }
    if shape == "unknown":
        return {
            "kept": True,
            "exclusion_reason": None,
            "secondary_region": False,
            "remote_scope": None,
            "location_status": draw(st.sampled_from(["ambiguous", "unknown"])),
        }
    if shape == "non_us":
        return {
            "kept": False,
            "exclusion_reason": "non_us",
            "secondary_region": False,
            "remote_scope": None,
            "location_status": "resolved",
        }
    # outside_radius
    return {
        "kept": False,
        "exclusion_reason": "outside_radius",
        "secondary_region": False,
        "remote_scope": None,
        "location_status": "resolved",
    }


@st.composite
def _jobs(draw):
    return {
        "title": draw(_titles()),
        "company_slug": draw(_slugs()),
        "company": draw(st.sampled_from(["", "Example Co", "Staffing Firm"])),
        "description": draw(_DESCRIPTIONS),
    }


@st.composite
def _tiers(draw):
    return draw(st.sampled_from(["senior", "mid", "junior", None]))


# ---------------------------------------------------------------------------
# Reference implementation of the precedence order and decision table.
#
# Independent of the production control flow: it re-derives the Decision from
# the same pure helpers (matcher.classify / .priority, recruiter, requires_phd,
# life_sciences_signal, _is_research_title) that the real chain uses, but
# sequences them here explicitly so a wiring bug in filters.py would diverge.
# ---------------------------------------------------------------------------


def _loc(d, key, default=None):
    return d.get(key, default) if d is not None else default


def _reference_decision(job, tier, loc):
    title = (job.get("title") or "").strip()
    slug = (
        job.get("company_slug") or job.get("company") or job.get("slug") or ""
    )
    slug_lower = slug.lower()

    # --- Pre-location precedence (Requirement 7.2). ---
    if not title:  # invalid record
        return Decision("excluded", "invalid_record", "invalid_record")

    blocklist = {b.lower() for b in getattr(_CFG, "COMPANY_BLOCKLIST", set())}
    if slug_lower in blocklist:  # 7.10
        return Decision("excluded", "blocklisted", "blocklisted")

    if _RECRUITER.is_recruiter(slug, job.get("company")):  # 7.11-7.13
        return Decision("excluded", "recruiter", "recruiter")

    title_class = _MATCHER.classify(title)
    if title_class == "senior_excluded":  # 7.4
        return Decision("excluded", "senior_excluded", "senior_excluded")
    if title_class == "title_excluded":  # 7.5
        return Decision("excluded", "title_excluded", "title_excluded")
    if title_class == "no_match":  # 7.3
        return Decision("excluded", "title_no_match", "title_no_match")

    # title_class in {primary, maybe, generic_analyst}: a Candidate_Job.

    # --- Post-detail: PhD gate then industry gate (7.2 order). ---
    description = job.get("description") or ""
    if requires_phd(description, _CFG):  # PhD wins over industry gate
        return Decision("excluded", "phd_required", "phd_required")
    if _is_research_title(title, _MATCHER):
        if not life_sciences_signal(title, description, _CFG):
            return Decision(
                "excluded", "non_target_industry", "non_target_industry"
            )

    # --- Location rejection wins over any kept outcome (7.14). ---
    priority = _MATCHER.priority(title)
    if not _loc(loc, "kept", True):
        reason = _loc(loc, "exclusion_reason") or "outside_radius"
        return Decision("excluded", reason, reason)

    def kept(mt, reason):
        return Decision(mt, reason, None, priority, None)

    # --- Title-based maybe reasons take precedence (design table). ---
    if title_class == "maybe":  # 7.8
        return kept("maybe", "title_maybe")
    if title_class == "generic_analyst":  # 7.9
        return kept("maybe", "title_generic_analyst")

    # title_class == "primary" from here.
    if tier == "senior":  # 7.7, 18.4
        return kept("maybe", "tier_senior_signal")

    # --- Location-based maybe reasons (primary title, not senior). ---
    if _loc(loc, "secondary_region", False):  # 7.21
        return kept("maybe", "location_new_england")
    if _loc(loc, "remote_scope") == "unspecified":  # 7.22
        return kept("maybe", "remote_unspecified")
    if _loc(loc, "location_status") in ("ambiguous", "unknown"):  # 7.15
        return kept("maybe", "location_unknown")

    return kept("primary", "title_primary")  # 7.6


# ---------------------------------------------------------------------------
# The production pipeline under test.
# ---------------------------------------------------------------------------


def _run_pipeline(job, tier, loc):
    """classify_pre_location -> classify_post_detail -> finalize_decision."""
    pre = classify_pre_location(job, _CFG, _MATCHER, _RECRUITER)
    if pre is not None:
        return pre

    title_class = _MATCHER.classify((job.get("title") or "").strip())
    post = classify_post_detail(job, title_class, _CFG, _MATCHER)
    if post is not None:
        return post

    return finalize_decision(job, title_class, tier, loc, matcher=_MATCHER)


# ---------------------------------------------------------------------------
# Property 8
# ---------------------------------------------------------------------------

_VALID_MATCH_TYPES = {"primary", "maybe", "excluded"}


@settings(max_examples=600)
@given(job=_jobs(), tier=_tiers(), loc=_loc_verdicts())
def test_pipeline_matches_reference(job, tier, loc):
    """The chain reproduces the reference Decision for every input (7.1-7.22)."""
    actual = _run_pipeline(copy.deepcopy(job), tier, copy.deepcopy(loc))
    expected = _reference_decision(copy.deepcopy(job), tier, copy.deepcopy(loc))
    assert actual == expected


@settings(max_examples=600)
@given(job=_jobs(), tier=_tiers(), loc=_loc_verdicts())
def test_exactly_one_match_type_and_reason(job, tier, loc):
    """Every Decision carries exactly one valid Match_Type and a Match_Reason."""
    d = _run_pipeline(job, tier, loc)
    assert d.match_type in _VALID_MATCH_TYPES
    assert isinstance(d.match_reason, str) and d.match_reason


@settings(max_examples=600)
@given(job=_jobs(), tier=_tiers(), loc=_loc_verdicts())
def test_exclusion_reason_iff_excluded(job, tier, loc):
    """Exclusion_Reason is set exactly when Match_Type is excluded (7.1)."""
    d = _run_pipeline(job, tier, loc)
    if d.match_type == "excluded":
        assert d.exclusion_reason is not None
        # For an excluded Decision, the reason mirrors the exclusion code.
        assert d.exclusion_reason == d.match_reason
        assert d.title_priority is None
    else:
        assert d.exclusion_reason is None
        assert d.title_priority in (1, 2)


@settings(max_examples=600)
@given(job=_jobs(), tier=_tiers(), loc=_loc_verdicts())
def test_pipeline_is_deterministic(job, tier, loc):
    """Running the pipeline twice on the same input yields an equal Decision."""
    first = _run_pipeline(copy.deepcopy(job), tier, copy.deepcopy(loc))
    second = _run_pipeline(copy.deepcopy(job), tier, copy.deepcopy(loc))
    assert first == second


@settings(max_examples=400)
@given(
    job=_jobs(),
    tier=_tiers(),
    loc=_loc_verdicts(),
    desc=st.sampled_from(_PHD_REQUIRED_DESCS),
)
def test_phd_required_takes_precedence_over_industry_gate(job, tier, loc, desc):
    """A PhD-required description excludes phd_required before the industry gate.

    Even for a gated Research_Title with no life-sciences signal (which would
    otherwise be non_target_industry), the PhD check wins (the order of
    Requirement 7.2).
    """
    job = dict(job)
    job["title"] = "Market Research Analyst"  # gated Research_Title
    # An ordinary slug so the pre-location steps pass and the job reaches
    # the post-detail PhD gate (otherwise a blocklist/recruiter slug wins).
    job["company_slug"] = "acme-health"
    job["company"] = "Acme Health"
    job["description"] = desc
    d = _run_pipeline(job, tier, loc)
    assert d.match_type == "excluded"
    assert d.match_reason == "phd_required"
    assert d.exclusion_reason == "phd_required"
