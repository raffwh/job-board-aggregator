# ============================================================
# FILTER CHAIN (Component 4)
#
# Pure, side-effect-free filter logic for the aggregator. This
# module classifies Job_Records into a Match_Type (primary | maybe |
# excluded) with a Match_Reason and, for excluded jobs, an
# Exclusion_Reason. It also computes Title_Priority.
#
# Decision order (Requirement 7.2):
#   invalid record -> blocklist -> recruiter -> senior exclusion ->
#   title exclusion -> title match -> (detail enrichment, PhD, and
#   the industry/Category gate -- added in task 7.2a) -> location.
#
# `classify_pre_location` performs the pre-location steps and returns
# None ("candidate, continue") when a Job_Record survives them.
# `finalize_decision` applies the title/location decision table once a
# location verdict is available.
#
# Task 7.2a will add `requires_phd`, `life_sciences_signal`,
# `assign_category`, and `classify_post_detail` to this module. They
# slot in between `classify_pre_location` and `finalize_decision`; the
# section marker at the bottom of this file reserves their place.
# ============================================================

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import ats_helpers
import personal_config


# ============================================================
# DECISION
# ============================================================


@dataclass(frozen=True)
class Decision:
    """The outcome of the Filter_Chain for a single Job_Record.

    `match_type` is one of ``primary``, ``maybe``, or ``excluded``.
    `match_reason` is a short machine-readable code (e.g.
    ``title_primary``, ``title_maybe``, ``title_generic_analyst``,
    ``tier_senior_signal``, ``location_unknown``,
    ``location_new_england``, ``remote_unspecified``) and, for excluded
    jobs, mirrors the exclusion code. `exclusion_reason` is set exactly
    when `match_type` is ``excluded`` (Requirement 7.1). `title_priority`
    is 1 for a Data_Scientist_Title and 2 otherwise on kept jobs, and
    None on excluded jobs (Requirements 18.5, 18.6). `category` is filled
    by task 7.2a for kept jobs and left None here.
    """

    match_type: str
    match_reason: str
    exclusion_reason: Optional[str] = None
    title_priority: Optional[int] = None
    category: Optional[str] = None


# ============================================================
# TITLE MATCHER
# ============================================================

# Tokens that make a title a "generic analyst" when nothing else
# matches (Requirement 7.9). Matched as a whole word, case-insensitive.
_ANALYST_WORD = re.compile(r"\banalyst\b", re.IGNORECASE)


class TitleMatcher:
    """Classifies a job title and computes its Title_Priority.

    The matcher reads only the title, so both `classify` and `priority`
    are deterministic (Requirements 7.18, 18.7).
    """

    def __init__(
        self,
        primary,
        maybe,
        exclude,
        senior_exclude,
        data_scientist=None,
        data_qualifier=None,
    ):
        self.primary = list(primary or [])
        self.maybe = list(maybe or [])
        self.exclude = list(exclude or [])
        self.senior_exclude = senior_exclude
        self.data_scientist = list(
            data_scientist
            if data_scientist is not None
            else getattr(personal_config, "DATA_SCIENTIST_PATTERNS", [])
        )
        self.qualified_scientist = getattr(
            personal_config, "QUALIFIED_SCIENTIST_PATTERN", None
        )
        self.data_qualifier = (
            data_qualifier
            if data_qualifier is not None
            else getattr(personal_config, "DATA_QUALIFIER_PATTERN", None)
        )

    def classify(self, title: str) -> str:
        """Return the title's classification.

        One of ``senior_excluded``, ``title_excluded``, ``primary``,
        ``maybe``, ``generic_analyst``, or ``no_match``. The order
        follows Requirement 7.2: senior exclusion, then title exclusion,
        then primary, then maybe, then the generic-analyst fallback.
        """

        text = title or ""

        if self.senior_exclude is not None and self.senior_exclude.search(text):
            return "senior_excluded"

        if any(p.search(text) for p in self.exclude):
            return "title_excluded"

        if any(p.search(text) for p in self.primary):
            return "primary"

        if any(p.search(text) for p in self.maybe):
            return "maybe"

        if _ANALYST_WORD.search(text):
            return "generic_analyst"

        return "no_match"

    def priority(self, title: str) -> int:
        """Return Title_Priority: 1 for a Data_Scientist_Title, else 2.

        A title is a Data_Scientist_Title when it matches any
        `DATA_SCIENTIST_PATTERNS` entry, or when it matches
        `QUALIFIED_SCIENTIST_PATTERN` (applied/research scientist) *and*
        also carries a `DATA_QUALIFIER_PATTERN` term (Requirements 18.5,
        18.6).
        """

        text = title or ""

        if any(p.search(text) for p in self.data_scientist):
            return 1

        if (
            self.qualified_scientist is not None
            and self.data_qualifier is not None
            and self.qualified_scientist.search(text)
            and self.data_qualifier.search(text)
        ):
            return 1

        return 2


def build_title_matcher(cfg=personal_config) -> TitleMatcher:
    """Construct a TitleMatcher from the project configuration."""

    return TitleMatcher(
        primary=cfg.TITLE_PATTERNS,
        maybe=cfg.MAYBE_PATTERNS,
        exclude=cfg.TITLE_EXCLUDE_PATTERNS,
        senior_exclude=cfg.SENIOR_EXCLUDE_PATTERN,
        data_scientist=cfg.DATA_SCIENTIST_PATTERNS,
        data_qualifier=cfg.DATA_QUALIFIER_PATTERN,
    )


# ============================================================
# RECRUITER DETECTOR
# ============================================================

# Split a slug or company name into lowercase alphanumeric tokens. Any
# run of characters that is not [a-z0-9] is a separator, so recruiter
# terms match only as whole tokens (Requirement 7.11).
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _tokenize(text: Optional[str]) -> set:
    if not text:
        return set()
    return {tok for tok in _TOKEN_SPLIT.split(text.lower()) if tok}


class RecruiterDetector:
    """Flags staffing and recruiting companies by whole-token match."""

    def __init__(self, terms, allowlist):
        self.terms = {t.lower() for t in (terms or [])}
        self.allowlist = {a.lower() for a in (allowlist or set())}

    def is_recruiter(self, slug: str, name: Optional[str] = None) -> bool:
        """True when a recruiter term is a whole token of slug or name.

        A slug in the allowlist is never a recruiter (Requirement 7.12).
        """

        if slug and slug.lower() in self.allowlist:
            return False

        tokens = _tokenize(slug) | _tokenize(name)
        return bool(tokens & self.terms)


def build_recruiter_detector(cfg=personal_config) -> RecruiterDetector:
    """Construct a RecruiterDetector from the project configuration."""

    return RecruiterDetector(
        terms=cfg.RECRUITER_TERMS,
        allowlist=cfg.RECRUITER_ALLOWLIST,
    )


# ============================================================
# PRE-LOCATION CLASSIFICATION
# ============================================================


def _job_slug(job) -> str:
    return (
        job.get("company_slug")
        or job.get("company")
        or job.get("slug")
        or ""
    )


def classify_pre_location(job, cfg, matcher, recruiter) -> Optional[Decision]:
    """Run the pre-location filter steps for one Job_Record.

    Returns an excluded `Decision` at the first failing check, or None
    when the Job_Record is a Candidate_Job that should continue to
    detail enrichment and the location check. The order is fixed by
    Requirement 7.2: invalid record -> blocklist -> recruiter -> senior
    exclusion -> title exclusion -> title match.
    """

    title = (job.get("title") or "").strip()
    slug = _job_slug(job)
    slug_lower = slug.lower()

    # Invalid record: no usable title.
    if not title:
        return Decision(
            match_type="excluded",
            match_reason="invalid_record",
            exclusion_reason="invalid_record",
        )

    # Blocklist (Requirement 7.10).
    blocklist = {b.lower() for b in getattr(cfg, "COMPANY_BLOCKLIST", set())}
    if slug_lower in blocklist:
        return Decision(
            match_type="excluded",
            match_reason="blocklisted",
            exclusion_reason="blocklisted",
        )

    # Recruiter (Requirements 7.11, 7.12, 7.13).
    if recruiter.is_recruiter(slug, job.get("company")):
        return Decision(
            match_type="excluded",
            match_reason="recruiter",
            exclusion_reason="recruiter",
        )

    # Title classification covers senior exclusion, title exclusion,
    # and the title/maybe/generic-analyst/no-match decision.
    title_class = matcher.classify(title)

    if title_class == "senior_excluded":  # Requirement 7.4
        return Decision(
            match_type="excluded",
            match_reason="senior_excluded",
            exclusion_reason="senior_excluded",
        )

    if title_class == "title_excluded":  # Requirement 7.5
        return Decision(
            match_type="excluded",
            match_reason="title_excluded",
            exclusion_reason="title_excluded",
        )

    if title_class == "no_match":  # Requirement 7.3
        return Decision(
            match_type="excluded",
            match_reason="title_no_match",
            exclusion_reason="title_no_match",
        )

    # title_class is one of "primary", "maybe", or "generic_analyst":
    # the Job_Record is a Candidate_Job. Continue to detail enrichment
    # (task 7.2a) and the location check.
    return None


# ============================================================
# LOCATION VERDICT -> DECISION
# ============================================================

# Title-based maybe reasons take precedence over location-based ones,
# so each kept job carries exactly one Match_Reason (design table).


def _loc_get(loc_result, key, default=None):
    """Read a field from a location verdict that may be a dict or object."""

    if loc_result is None:
        return default
    if isinstance(loc_result, dict):
        return loc_result.get(key, default)
    return getattr(loc_result, key, default)


def finalize_decision(
    job,
    title_class: str,
    tier,
    loc_result,
    matcher=None,
    category: Optional[str] = None,
) -> Decision:
    """Apply the title/location decision table for a kept Candidate_Job.

    Preconditions: `classify_pre_location` returned None for this job
    (so `title_class` is one of ``primary``, ``maybe``, or
    ``generic_analyst``) and any PhD / industry gate (task 7.2a) also
    passed. `tier` is the value returned by `job_tier_classification`
    (``senior`` triggers the senior-signal row). `loc_result` is the
    Location_Filter verdict.

    `loc_result` exposes:
      - ``kept`` (bool): False means the job was rejected on location.
      - ``exclusion_reason`` (str): ``non_us`` or ``outside_radius``
        when rejected (Requirement 7.14).
      - ``secondary_region`` (bool): a Secondary_Region match
        (Requirement 7.21 / 9.16).
      - ``remote_scope`` (str): ``unspecified`` when the remote scope is
        unknown (Requirement 7.22 / 9.15).
      - ``location_status`` (str): ``ambiguous`` or ``unknown`` keeps the
        job as a maybe (Requirement 7.15).

    Every kept Decision carries `title_priority` (Requirements 18.5,
    18.6) and the `category` passed in (filled by task 7.2a).
    """

    title = job.get("title") or ""
    if matcher is None:
        matcher = build_title_matcher()
    priority = matcher.priority(title)

    def kept(match_type, reason):
        return Decision(
            match_type=match_type,
            match_reason=reason,
            exclusion_reason=None,
            title_priority=priority,
            category=category,
        )

    # --- Location rejection wins over any kept outcome (7.14). ---
    kept_location = _loc_get(loc_result, "kept", True)
    if not kept_location:
        reason = _loc_get(loc_result, "exclusion_reason") or "outside_radius"
        return Decision(
            match_type="excluded",
            match_reason=reason,
            exclusion_reason=reason,
        )

    # --- Title-based maybe reasons take precedence (design table). ---

    # MAYBE_PATTERNS-only match -> title_maybe, regardless of tier (7.8).
    if title_class == "maybe":
        return kept("maybe", "title_maybe")

    # Generic analyst -> title_generic_analyst (7.9).
    if title_class == "generic_analyst":
        return kept("maybe", "title_generic_analyst")

    # From here title_class == "primary".

    # A senior tier signal moves a primary title to maybe (7.7, 18.4).
    if tier == "senior":
        return kept("maybe", "tier_senior_signal")

    # --- Location-based maybe reasons (primary title, not senior). ---

    # Secondary_Region (New England outside the radius) (7.21, 9.16/9.17).
    if _loc_get(loc_result, "secondary_region", False):
        return kept("maybe", "location_new_england")

    # Remote scope unspecified (7.22, 9.15/9.18).
    if _loc_get(loc_result, "remote_scope") == "unspecified":
        return kept("maybe", "remote_unspecified")

    # Ambiguous / unknown location keeps a primary title as maybe (7.15).
    if _loc_get(loc_result, "location_status") in ("ambiguous", "unknown"):
        return kept("maybe", "location_unknown")

    # --- Clean primary (7.6). ---
    return kept("primary", "title_primary")


# ============================================================
# POST-DETAIL CLASSIFICATION AND CATEGORY (task 7.2a)
#
# `requires_phd`, `life_sciences_signal`, `assign_category`, and
# `classify_post_detail` are added here by task 7.2a. They run between
# `classify_pre_location` and `finalize_decision` (after detail
# enrichment, before the location check).
# ============================================================
# Research_Title patterns. These are the research / market-research /
# insights / commercial-analytics titles that are gated on a
# Life_Sciences_Signal (Requirement 19.2, 19.4). They mirror the
# research-family entries of `TITLE_PATTERNS` in personal_config.py.
# A title is a Research_Title when it matches one of these *and* does
# not independently match any other (non-research) primary pattern.
RESEARCH_TITLE_PATTERNS = [
    re.compile(r"\bresearch\s+data\s+analyst\b", re.IGNORECASE),                       # research data analyst
    re.compile(r"\b(clinical|health(care)?|outcomes)\s+research\s+analyst\b", re.IGNORECASE),  # clinical/health/outcomes research analyst
    re.compile(r"\bmarket\s+research\s+(analyst|scientist|associate)\b", re.IGNORECASE),  # market research analyst/scientist/associate
    re.compile(r"\bcommercial\s+(analytics|insights)\b", re.IGNORECASE),               # commercial analytics / insights
    re.compile(r"\binsights?\s+(analyst|scientist)\b", re.IGNORECASE),                 # insights analyst/scientist
]


def _is_research_title(title: str, matcher: TitleMatcher) -> bool:
    """True when `title` is a gated Research_Title.

    A Research_Title matches one of `RESEARCH_TITLE_PATTERNS` and does
    not *independently* match any other (non-research) primary
    `TITLE_PATTERNS` entry, and is not a Data_Scientist_Title. This is
    the only set of titles the industry/Category gate applies to
    (Requirements 19.2, 19.4, 19.5); a Biostatistician, Epidemiologist,
    Analytics Engineer, Data Engineer, or Data Scientist is never gated
    because it matches a non-research primary pattern (or is priority 1).
    """

    text = title or ""

    if not any(p.search(text) for p in RESEARCH_TITLE_PATTERNS):
        return False

    # A Data_Scientist_Title is never gated (handled as data_scientist).
    if matcher.priority(text) == 1:
        return False

    # Re-run the title against the primary patterns with the research
    # patterns removed. If anything else matches, the title stands on a
    # non-research primary pattern and is not gated (Requirement 19.5).
    research_pats = {p.pattern for p in RESEARCH_TITLE_PATTERNS}
    non_research = [p for p in matcher.primary if p.pattern not in research_pats]
    if any(p.search(text) for p in non_research):
        return False

    return True


# ============================================================
# LIFE-SCIENCES SIGNAL
# ============================================================

# Terms that are safe to match as whole words (alphabetic, no internal
# punctuation or spaces) use a word-boundary regex. Multi-word terms,
# terms with hyphens/periods, and short stems (e.g. "epidemiolog") are
# matched as plain case-insensitive substrings so the stem and phrase
# variants in the config all hit.
def _signal_matcher(term: str):
    t = term.lower()
    if " " in t or "-" in t or "." in t:
        return lambda text: t in text
    if t.isalpha():
        pat = re.compile(r"\b" + re.escape(t) + r"\w*", re.IGNORECASE)
        return lambda text: bool(pat.search(text))
    return lambda text: t in text


def life_sciences_signal(title, description, cfg=personal_config) -> bool:
    """True when any LIFE_SCIENCES_SIGNAL_TERMS term is present.

    Tests the lowercased title and description. Multi-word and
    punctuated terms match as phrases; plain alphabetic terms match on a
    word boundary so "epidemiolog" also catches "epidemiology". The
    function reads only the two strings and uses no network, API, or
    secrets, so it is deterministic (Requirements 19.7, 19.11).
    """

    text = ((title or "") + " " + (description or "")).lower()
    if not text.strip():
        return False

    for term in getattr(cfg, "LIFE_SCIENCES_SIGNAL_TERMS", []):
        if _signal_matcher(term)(text):
            return True
    return False


# ============================================================
# CATEGORY ASSIGNMENT
# ============================================================


def assign_category(title, title_class, description, matcher, cfg=personal_config) -> str:
    """Return the Category for a kept Job_Record.

    One of ``data_scientist``, ``life_sciences_research``, or
    ``other_target`` (Requirement 19.1-19.3). Precedence:

    1. A Data_Scientist_Title (``priority == 1``) is ``data_scientist``
       in any industry, with no domain-knowledge requirement (19.1).
    2. A gated Research_Title that passes `life_sciences_signal` is
       ``life_sciences_research`` (19.2). A gated Research_Title that
       *fails* the signal is not a Category at all -- it is excluded as
       ``non_target_industry`` by `classify_post_detail`, so this
       function is only reached for kept jobs and never returns a
       Category for that case.
    3. Everything else kept is ``other_target`` (19.3, 19.5).

    Deterministic: it reads only the title, description, and config
    (Requirement 19.11).
    """

    if matcher.priority(title or "") == 1:
        return "data_scientist"

    if _is_research_title(title or "", matcher):
        # Reached only when the signal passed (else excluded upstream).
        return "life_sciences_research"

    return "other_target"


# ============================================================
# PhD-REQUIRED DETECTION
# ============================================================

# Locate PhD / doctorate mentions so soft and required phrasings are
# only tested in a window around each mention (design, Requirement 20).
_PHD_MENTION = re.compile(r"ph\.?\s?d\.?|doctorate", re.IGNORECASE)
_PHD_WINDOW = 60


def requires_phd(description, cfg=personal_config) -> bool:
    """True when the description requires a PhD with no softening nearby.

    For each PhD / doctorate mention, a window of +/- 60 characters is
    checked: a `PHD_SOFT_PATTERNS` match vetoes that mention (keeps the
    job), and a `PHD_REQUIRED_PATTERNS` match with no veto makes the
    function true (Requirements 20.3, 20.4). Reads only the description
    and the config pattern sets, so it is deterministic (Requirements
    20.5, 20.6).
    """

    text = (description or "").lower()
    if not text:
        return False

    soft = getattr(cfg, "PHD_SOFT_PATTERNS", [])
    required = getattr(cfg, "PHD_REQUIRED_PATTERNS", [])

    for m in _PHD_MENTION.finditer(text):
        start = max(0, m.start() - _PHD_WINDOW)
        end = m.end() + _PHD_WINDOW
        window = text[start:end]
        if any(p.search(window) for p in soft):
            continue
        if any(p.search(window) for p in required):
            return True
    return False


# ============================================================
# POST-DETAIL CLASSIFICATION
# ============================================================


def classify_post_detail(job, title_class, cfg, matcher) -> Optional[Decision]:
    """Run the PhD and industry/Category gates after detail enrichment.

    Called after the Detail_Enricher has run on a Candidate_Job (so
    ``job["description"]`` is populated when the fetch succeeded) and
    before the location check. The order follows Requirements 7.2, 19,
    and 20:

    1. `requires_phd(description)` -> exclude ``phd_required`` (20.1,
       20.2).
    2. Industry gate: a gated Research_Title whose `life_sciences_signal`
       fails on the title and the available description is excluded
       ``non_target_industry`` (19.4). This fails closed: when the detail
       fetch failed and the description is empty, the title alone is
       tested and the job is still excluded, with ``detail_failed``
       recorded so the exclusion is counted under the detail-failure
       tally (Requirements 19.9, 1.9).

    Returns an excluded `Decision`, or None to continue to the location
    check and `finalize_decision` / `assign_category`.
    """

    description = job.get("description") or ""

    # 1. PhD-required exclusion (Requirement 20).
    if requires_phd(description, cfg):
        return Decision(
            match_type="excluded",
            match_reason="phd_required",
            exclusion_reason="phd_required",
        )

    # 2. Industry / Category gate for Research_Titles (Requirement 19).
    title = job.get("title") or ""
    if _is_research_title(title, matcher):
        if not life_sciences_signal(title, description, cfg):
            # Fail closed. Mark the detail-failure subset so the Scraper
            # can count it under the detail-failure tally (19.9, 1.9).
            if not description:
                job["detail_failed"] = True
            return Decision(
                match_type="excluded",
                match_reason="non_target_industry",
                exclusion_reason="non_target_industry",
            )

    return None


# ============================================================
# SORTING AND CAPS (Component 4)
#
# `sort_by_priority_and_date` orders kept Job_Records by Title_Priority
# ascending, then by posting date newest-first, with undated jobs last
# within each priority (Requirement 7.17). `apply_caps` truncates each
# bucket (primary vs maybe) to its configured cap in that sort order and
# reports how many were dropped (Requirement 7.16).
# ============================================================


def _sort_key(job):
    """Sort key for a Job_Record: (priority, undated_flag, -timestamp).

    - ``title_priority`` defaults to 2 when missing, so a
      Data_Scientist_Title (priority 1) sorts ahead of everything else.
    - The posting date is parsed by `ats_helpers.parse_posted`. Dated
      jobs sort before undated jobs of the same priority (undated_flag
      0 before 1), and within the dated group the newest timestamp wins
      (``-ts``). Undated jobs carry a neutral timestamp so the key stays
      comparable.
    - Ties are broken by company then title so the output is stable and
      deterministic (Requirement 7.17).
    """

    priority = job.get("title_priority")
    if not isinstance(priority, int):
        priority = 2

    ts = ats_helpers.parse_posted(job.get("published_at"))
    if ts is None:
        undated = 1
        epoch = 0.0
    else:
        undated = 0
        epoch = ts.timestamp()

    company = (job.get("company") or job.get("company_slug") or "").lower()
    title = (job.get("title") or "").lower()

    return (priority, undated, -epoch, company, title)


def sort_by_priority_and_date(jobs) -> list:
    """Return `jobs` sorted by Title_Priority, then newest date first.

    Title_Priority is non-decreasing; within each priority dated jobs
    come before undated ones and are ordered newest-first; company and
    title break ties for a stable, deterministic ordering
    (Requirement 7.17). The input list is not mutated.
    """

    return sorted(jobs, key=_sort_key)


def apply_caps(jobs, primary_cap, maybe_cap):
    """Truncate the primary and maybe buckets to their caps in sort order.

    Splits `jobs` into the Primary_Bucket (``match_type == "primary"``)
    and the Maybe_Bucket (``match_type == "maybe"``), sorts each with
    `sort_by_priority_and_date`, and keeps the first ``primary_cap`` /
    ``maybe_cap`` records. Each dropped Job_Record has its ``match_type``
    set to ``excluded`` and its ``exclusion_reason`` set to
    ``cap_truncated``; because the kept records are the first in sort
    order, a cap never drops a priority-1 job while keeping a priority-2
    job in the same bucket (Requirement 7.16).

    Returns ``(kept_jobs, {"primary": n_truncated, "maybe": n_truncated})``
    where ``kept_jobs`` is the kept primary records followed by the kept
    maybe records and the counts are ``max(0, n - cap)`` per bucket. Jobs
    with any other ``match_type`` are passed through untouched and are
    not subject to a cap.
    """

    primary = [j for j in jobs if j.get("match_type") == "primary"]
    maybe = [j for j in jobs if j.get("match_type") == "maybe"]

    primary_sorted = sort_by_priority_and_date(primary)
    maybe_sorted = sort_by_priority_and_date(maybe)

    kept_primary = primary_sorted[:primary_cap]
    kept_maybe = maybe_sorted[:maybe_cap]

    for job in primary_sorted[primary_cap:]:
        job["match_type"] = "excluded"
        job["exclusion_reason"] = "cap_truncated"
    for job in maybe_sorted[maybe_cap:]:
        job["match_type"] = "excluded"
        job["exclusion_reason"] = "cap_truncated"

    truncated = {
        "primary": max(0, len(primary) - primary_cap),
        "maybe": max(0, len(maybe) - maybe_cap),
    }

    return kept_primary + kept_maybe, truncated
