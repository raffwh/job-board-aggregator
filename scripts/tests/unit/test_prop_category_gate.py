"""Property-based test for Category assignment and the industry gate.

Property 30: Category assignment and the industry gate are correct, total,
orthogonal, and deterministic.

For any title built from random fragments (Data_Scientist_Title forms;
Research_Titles such as "Market Research Analyst", "Research Data Analyst",
"Clinical Research Analyst", "Insights Analyst"; non-research primary phrases
such as "Biostatistician", "Epidemiologist", "Analytics Engineer", "Data
Engineer"; adjacent and junk phrases) and for any description that either
carries a random ``LIFE_SCIENCES_SIGNAL_TERMS`` term or is signal-free
(including empty), the Category decision SHALL:

- return ``data_scientist`` whenever the title is a Data_Scientist_Title, for
  any description (Requirement 19.1), and never gate it (19.5);
- for a gated Research_Title (does not independently match a non-research
  primary pattern): return ``life_sciences_research`` when the title or
  description carries a signal (19.2), and exclude the job with
  ``non_target_industry`` when neither does -- including the empty-description
  detail-failure case, which fails closed and sets ``detail_failed`` (19.4,
  19.9);
- never gate a title that independently matches a non-research primary
  pattern, returning ``other_target`` for those and for every other kept
  title (19.3, 19.5);
- assign exactly one Category to every kept job and no Category to any
  excluded job (19.6);
- leave the job's Match_Type, Match_Reason, and Title_Priority unchanged, i.e.
  ``classify_post_detail`` touches only the gate (orthogonality, 19.10);
- return the same Category on a second call with the same inputs
  (determinism, 19.11).

The reference predicates for Data_Scientist_Title and the life-sciences
signal are re-derived here with their own regexes/term list so the test does
not merely restate the implementation's compiled patterns.

Validates: Requirements 19.1, 19.2, 19.3, 19.4, 19.5, 19.6, 19.9, 19.10,
19.11, 7.23, 7.24
"""

import re

from hypothesis import given, settings
from hypothesis import strategies as st

import filters
import personal_config


matcher = filters.build_title_matcher()


# ---------------------------------------------------------------------------
# Independent reference predicates
# ---------------------------------------------------------------------------

# Data_Scientist_Title (mirrors Property 28's reference, re-derived here).
_REF_DATA_SCIENCE = re.compile(r"\bdata\s*scien(?:tist|ce)\b", re.I)
_REF_RWE_SCIENTIST = re.compile(
    r"\b(?:rwe|rwd|real[\s-]?world\s+(?:evidence|data))\s+scientist\b", re.I
)
_REF_DECISION_SCIENTIST = re.compile(r"\bdecision\s+scientist\b", re.I)
_REF_QUALIFIED_SCIENTIST = re.compile(r"\b(?:applied|research)\s+scientist\b", re.I)
_REF_DATA_QUALIFIER = re.compile(
    r"\b(?:data|machine\s+learning|ml|ai|analytics|statistic\w*|"
    r"quantitative|computational|nlp)\b",
    re.I,
)


def is_data_scientist_title(title: str) -> bool:
    """Reference predicate: True when ``title`` is a Data_Scientist_Title."""
    text = title or ""
    if _REF_DATA_SCIENCE.search(text):
        return True
    if _REF_RWE_SCIENTIST.search(text):
        return True
    if _REF_DECISION_SCIENTIST.search(text):
        return True
    if _REF_QUALIFIED_SCIENTIST.search(text) and _REF_DATA_QUALIFIER.search(text):
        return True
    return False


def _ref_term_present(term: str, text: str) -> bool:
    """Re-derived signal semantics for a single term.

    A plain alphabetic term (no spaces, hyphens, or periods) matches on a
    word boundary with an open suffix, so "epidemiolog" catches
    "epidemiology" but "cro" does not match "across". Multi-word or
    punctuated terms match as plain substrings. This mirrors the glossary
    semantics ("whole words where sensible") with the test's own regexes.
    """
    t = term.lower()
    if " " in t or "-" in t or "." in t:
        return t in text
    if t.isalpha():
        return re.search(r"\b" + re.escape(t) + r"\w*", text) is not None
    return t in text


def ref_has_signal(title: str, description: str) -> bool:
    """Reference life-sciences signal: any config term, whole-word sensible."""
    text = ((title or "") + " " + (description or "")).lower()
    return any(
        _ref_term_present(term, text)
        for term in personal_config.LIFE_SCIENCES_SIGNAL_TERMS
    )


# ---------------------------------------------------------------------------
# Title fragment generators (the four families from Property 30)
# ---------------------------------------------------------------------------

# 1. Data_Scientist_Title forms -> always data_scientist, never gated.
_DS_TITLES = [
    "Data Scientist",
    "Senior Data Scientist",
    "Associate Data Scientist",
    "Data Science Manager",
    "RWE Scientist",
    "Real-World Evidence Scientist",
    "Decision Scientist",
    "Applied Scientist, Machine Learning",
    "Research Scientist, Data",
]

# 2. Gated Research_Titles -> life_sciences_research with a signal,
#    excluded non_target_industry without one. None of these independently
#    match a non-research primary pattern.
_RESEARCH_TITLES = [
    "Market Research Analyst",
    "Market Research Scientist",
    "Market Research Associate",
    "Research Data Analyst",
    "Clinical Research Analyst",
    "Healthcare Research Analyst",
    "Outcomes Research Analyst",
    "Insights Analyst",
    "Insights Scientist",
    "Commercial Analytics",
    "Commercial Insights",
]

# 3. Non-research primary titles -> other_target, never gated. Each
#    independently matches a non-research TITLE_PATTERNS entry.
_NON_RESEARCH_PRIMARY = [
    "Biostatistician",
    "Statistician II",
    "Epidemiologist",
    "Pharmacoepidemiology Lead",
    "Analytics Engineer",
    "Data Engineer",
    "Data Analyst",
    "Healthcare Data Analyst",
    "HEOR Analyst",
    "Health Economist",
    "Machine Learning Engineer",
    "BI Developer",
]

# 4. Adjacent / junk titles. These are not kept by the pre-location filter,
#    but assign_category is still total over them and must return a value
#    (other_target) without gating.
_JUNK_TITLES = [
    "Software Engineer",
    "Product Manager",
    "Account Executive",
    "Warehouse Associate",
    "",
]


# ---------------------------------------------------------------------------
# Description generators
# ---------------------------------------------------------------------------

_FILLER = (
    "We are hiring for a growing team. You will collaborate with "
    "stakeholders, build dashboards, and communicate findings to "
    "leadership across the organization."
)

# A random life-sciences term to inject (guarantees a signal).
_signal_terms = st.sampled_from(personal_config.LIFE_SCIENCES_SIGNAL_TERMS)

# Signal-free descriptions: neutral filler with no life-sciences term, plus
# the empty string to exercise the fail-closed detail-failure path.
_signal_free = st.sampled_from(
    [
        "",
        _FILLER,
        "Join our fintech team working on payments and ledgers in Python.",
        "Retail analytics role focused on merchandising and supply chain.",
        "Ad-tech position building bidding models for a marketing platform.",
    ]
)

# Signal-bearing descriptions: filler with an injected term somewhere.
_signal_bearing = st.builds(
    lambda term: f"{_FILLER} This role supports our {term} programs.",
    _signal_terms,
)

descriptions = st.one_of(_signal_free, _signal_bearing)

titles = st.one_of(
    st.sampled_from(_DS_TITLES),
    st.sampled_from(_RESEARCH_TITLES),
    st.sampled_from(_NON_RESEARCH_PRIMARY),
    st.sampled_from(_JUNK_TITLES),
)


# ---------------------------------------------------------------------------
# Helpers mirroring the pipeline order: classify_post_detail then
# (for kept jobs) assign_category / finalize_decision.
# ---------------------------------------------------------------------------


def _title_class(title: str) -> str:
    return matcher.classify(title)


def _run_gate(title: str, description: str):
    """Run the industry/PhD gate on a copy of a minimal job dict.

    Returns ``(decision_or_none, job)`` where ``job`` is the possibly
    mutated dict so the test can inspect the ``detail_failed`` marker.
    """
    job = {"title": title, "description": description}
    decision = filters.classify_post_detail(
        job, _title_class(title), personal_config, matcher
    )
    return decision, job


# ---------------------------------------------------------------------------
# Property 30
# ---------------------------------------------------------------------------


@settings(max_examples=500)
@given(title=titles, description=descriptions)
def test_category_and_industry_gate(title, description):
    is_ds = is_data_scientist_title(title)
    is_research = filters._is_research_title(title, matcher)
    has_signal = ref_has_signal(title, description)

    decision, job = _run_gate(title, description)

    # --- Gate verdict (classify_post_detail) ---
    if is_research and not is_ds and not has_signal:
        # 19.4 / 19.9: gated Research_Title with no signal is excluded,
        # fail closed (including the empty-description detail-failure case).
        assert decision is not None, (
            f"title={title!r} desc={description!r}: expected exclusion, "
            f"got continue"
        )
        assert decision.match_type == "excluded"
        assert decision.exclusion_reason == "non_target_industry"
        assert decision.match_reason == "non_target_industry"
        # 19.6: an excluded job carries no Category.
        assert decision.category is None
        # 19.9: the empty-description failure is marked for the tally.
        if not description:
            assert job.get("detail_failed") is True
        else:
            assert "detail_failed" not in job
    else:
        # Not gated out (a non-research primary title is never gated, a
        # Data_Scientist_Title is never gated, and a Research_Title with a
        # signal passes). classify_post_detail returns None to continue.
        # Junk titles that are neither research nor primary also pass the
        # gate here (the title filter excludes them earlier, not the gate).
        assert decision is None, (
            f"title={title!r} desc={description!r}: expected continue, "
            f"got {decision}"
        )
        assert "detail_failed" not in job

        # --- Category assignment for a kept job (assign_category) ---
        category = filters.assign_category(
            title, _title_class(title), description, matcher
        )
        # 19.6: exactly one of the three Categories.
        assert category in (
            "data_scientist",
            "life_sciences_research",
            "other_target",
        )

        if is_ds:
            # 19.1 / 19.5: Data_Scientist_Title -> data_scientist, any desc.
            assert category == "data_scientist", (
                f"title={title!r}: expected data_scientist, got {category}"
            )
        elif is_research:
            # Reached only with a signal (else excluded above): 19.2.
            assert has_signal
            assert category == "life_sciences_research", (
                f"title={title!r} desc={description!r}: expected "
                f"life_sciences_research, got {category}"
            )
        else:
            # 19.3 / 19.5: everything else kept -> other_target, no gating.
            assert category == "other_target", (
                f"title={title!r}: expected other_target, got {category}"
            )

        # 19.11: determinism -- a second call returns the same Category.
        assert (
            filters.assign_category(title, _title_class(title), description, matcher)
            == category
        )


@settings(max_examples=300)
@given(title=titles, description=descriptions)
def test_gate_is_deterministic_and_orthogonal(title, description):
    """19.10 / 19.11: the gate is deterministic and touches only the gate.

    Two runs on independent copies yield the same verdict, and the gate only
    ever adds the ``detail_failed`` marker -- it never writes Match_Type,
    Match_Reason, or Title_Priority onto the job (those stay the Filter_Chain's
    job elsewhere).
    """
    d1, j1 = _run_gate(title, description)
    d2, j2 = _run_gate(title, description)

    # Determinism: same verdict on a second call.
    assert (d1 is None) == (d2 is None)
    if d1 is not None:
        assert d1.match_type == d2.match_type
        assert d1.exclusion_reason == d2.exclusion_reason
        assert d1.match_reason == d2.match_reason

    # Orthogonality: the gate never stamps decision fields onto the job dict.
    for job in (j1, j2):
        assert "match_type" not in job
        assert "match_reason" not in job
        assert "title_priority" not in job
        assert "category" not in job
