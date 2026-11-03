"""Property-based test for Title_Priority.

Property 28: Title_Priority marks exactly the Data_Scientist_Titles.

For any title built from random fragments (data-scientist / data-science
forms with prefixes such as associate, junior, RWE, clinical, healthcare,
and levels I-III; RWE/RWD and real-world-evidence/-data scientist; decision
scientist; applied or research scientist with and without a data qualifier;
other primary phrases such as data analyst, biostatistician, HEOR analyst;
and junk words), ``TitleMatcher.priority`` SHALL:

- return 1 exactly when an independent reference predicate for
  Data_Scientist_Title is true, and 2 otherwise (Requirements 18.5, 18.6);
- return the same value when called twice on the same title (determinism,
  Requirement 18.7).

The reference predicate re-derives the Data_Scientist_Title definition from
the glossary semantics (a data scientist/science phrase in any form, the
RWE/RWD/real-world and decision scientist phrases, or an applied/research
scientist that also carries a data qualifier) with its own regexes, so the
test does not merely restate the matcher's own compiled patterns.

Validates: Requirements 18.5, 18.6, 18.7
"""

import re

from hypothesis import given, settings
from hypothesis import strategies as st

import filters


matcher = filters.build_title_matcher()


# ---------------------------------------------------------------------------
# Independent reference predicate for Data_Scientist_Title
# ---------------------------------------------------------------------------

# "data scientist"/"data science" in any form, allowing collapsed spacing.
_REF_DATA_SCIENCE = re.compile(r"\bdata\s*scien(?:tist|ce)\b", re.I)

# RWE / RWD / real-world (evidence|data) scientist.
_REF_RWE_SCIENTIST = re.compile(
    r"\b(?:rwe|rwd|real[\s-]?world\s+(?:evidence|data))\s+scientist\b", re.I
)

# decision scientist.
_REF_DECISION_SCIENTIST = re.compile(r"\bdecision\s+scientist\b", re.I)

# applied / research scientist (only a Data_Scientist_Title with a qualifier).
_REF_QUALIFIED_SCIENTIST = re.compile(r"\b(?:applied|research)\s+scientist\b", re.I)

# data qualifier that promotes a qualified scientist to a Data_Scientist_Title.
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


# ---------------------------------------------------------------------------
# Title fragment generators
# ---------------------------------------------------------------------------

# Prefixes/levels that may decorate a data-scientist core without changing it.
_DS_PREFIXES = [
    "", "Associate", "Junior", "Senior", "Lead", "Staff",
    "RWE", "RWD", "Clinical", "Healthcare", "Health",
]
_DS_LEVELS = ["", "I", "II", "III"]

# Data-scientist cores: each one makes the title a Data_Scientist_Title.
_DS_CORES = [
    "Data Scientist",
    "Data Science",
    "DataScientist",            # collapsed spacing still matches \s*
    "RWE Scientist",
    "RWD Scientist",
    "Real World Evidence Scientist",
    "Real-World Data Scientist",
    "Decision Scientist",
]

# Applied/research scientist stems: Data_Scientist_Title only with a qualifier.
_QUALIFIED_STEMS = ["Applied Scientist", "Research Scientist"]

# Data qualifiers that promote a qualified scientist stem to priority 1.
_DATA_QUALIFIERS = [
    "Machine Learning", "ML", "AI", "Analytics", "Statistics",
    "Quantitative", "Computational", "NLP", "Data",
]

# Non-qualifier topics that keep a qualified scientist stem at priority 2.
_NON_DATA_QUALIFIERS = [
    "Cell Biology", "Chemistry", "Materials", "Immunology", "Genomics",
]

# Other primary / non-data titles that are not Data_Scientist_Titles.
_OTHER_TITLES = [
    "Data Analyst",
    "Biostatistician",
    "HEOR Analyst",
    "Analytics Engineer",
    "Data Engineer",
    "Epidemiologist",
    "Market Research Analyst",
    "Software Engineer",
    "Product Manager",
]

# Junk words that never create a match on their own.
_JUNK_WORDS = ["Remote", "Hybrid", "Contract", "Team", "Platform", "2024", "USA"]


def _joined(parts):
    """Join non-empty parts with single spaces."""
    return " ".join(p for p in parts if p).strip()


# A data-scientist title: prefix + core + optional level + optional junk.
_data_scientist_titles = st.builds(
    lambda pre, core, lvl, junk: _joined([pre, core, lvl, junk]),
    st.sampled_from(_DS_PREFIXES),
    st.sampled_from(_DS_CORES),
    st.sampled_from(_DS_LEVELS),
    st.sampled_from([""] + _JUNK_WORDS),
)

# Applied/research scientist with a data qualifier (-> Data_Scientist_Title).
_qualified_with_qualifier = st.builds(
    lambda stem, qual, sep: f"{stem}{sep}{qual}",
    st.sampled_from(_QUALIFIED_STEMS),
    st.sampled_from(_DATA_QUALIFIERS),
    st.sampled_from([", ", " - ", " "]),
)

# Applied/research scientist without a data qualifier (NOT a Data_Scientist).
_qualified_without_qualifier = st.builds(
    lambda stem, topic, sep: f"{stem}{sep}{topic}" if topic else stem,
    st.sampled_from(_QUALIFIED_STEMS),
    st.sampled_from([""] + _NON_DATA_QUALIFIERS),
    st.sampled_from([", ", " - ", " "]),
)

# Non-data titles, optionally decorated with junk words.
_non_data_titles = st.builds(
    lambda title, junk: _joined([title, junk]),
    st.sampled_from(_OTHER_TITLES),
    st.sampled_from([""] + _JUNK_WORDS),
)

# Free-form junk-only strings.
_junk_titles = st.lists(
    st.sampled_from(_JUNK_WORDS), min_size=0, max_size=4
).map(lambda ws: " ".join(ws))

titles = st.one_of(
    _data_scientist_titles,
    _qualified_with_qualifier,
    _qualified_without_qualifier,
    _non_data_titles,
    _junk_titles,
)


# ---------------------------------------------------------------------------
# Property 28
# ---------------------------------------------------------------------------


@settings(max_examples=400)
@given(title=titles)
def test_priority_marks_exactly_data_scientist_titles(title):
    expected = 1 if is_data_scientist_title(title) else 2

    result = matcher.priority(title)

    # Requirements 18.5 / 18.6: priority is 1 exactly for Data_Scientist_Titles.
    assert result == expected, (
        f"title={title!r}: priority {result}, "
        f"reference expected {expected}"
    )

    # Requirement 18.7: value is in range and deterministic across calls.
    assert result in (1, 2)
    assert matcher.priority(title) == result
