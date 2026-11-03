"""Property-based test for Keyword_Backend output invariants.

Property 22: Keyword output invariants.

For any title and description, the Keyword_Backend SHALL return:

- skills with no duplicates, ordered by the position of their first match in
  ``title + "\\n" + description`` (Requirement 11.3);
- a summary of at most 300 characters (Requirement 11.5);
- identical output on a second call with the same inputs (determinism,
  Requirement 11.7).

The generators mix free-form Unicode text with text seeded from known
Skills_Dictionary alias tokens, so most examples actually exercise the
ordering and dedup logic rather than returning an empty skills list.

The ordering oracle is derived independently of the backend's own sort: for
each canonical skill the backend returns, the test recomputes the earliest
match position across that skill's compiled patterns over the same
``title + "\\n" + description`` text, then checks the returned sequence is
sorted by that position. It does not simply replay the backend's sort key.

Validates: Requirements 11.3, 11.5, 11.7
"""

from typing import Optional

from hypothesis import given, settings
from hypothesis import strategies as st

from jd_skills import SKILLS
from jd_summarizer import KeywordBackend


MAX_SUMMARY_CHARS = 300

backend = KeywordBackend(max_summary_chars=MAX_SUMMARY_CHARS)

# Canonical names the dictionary can emit (some names repeat across entries).
_CANONICAL_NAMES = {skill.name for skill in SKILLS}


# ---------------------------------------------------------------------------
# Independent ordering oracle
# ---------------------------------------------------------------------------


def _earliest_position(name: str, text: str) -> Optional[int]:
    """Earliest start offset of any pattern of any skill named ``name``.

    A canonical name may be backed by more than one ``Skill`` entry (for
    example ``GEE``); the oracle takes the minimum match position across every
    pattern of every entry carrying that name, matching the dictionary
    semantics without reusing the backend's sort implementation.
    """
    earliest: Optional[int] = None
    for skill in SKILLS:
        if skill.name != name:
            continue
        for pattern in skill.compiled:
            match = pattern.search(text)
            if match is not None and (earliest is None or match.start() < earliest):
                earliest = match.start()
    return earliest


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# Alias tokens pulled from the dictionary that match in ordinary lowercase
# prose with word boundaries (keeps the generator from leaning only on the
# case/context-sensitive short skills). Each reliably triggers its skill.
_SKILL_TOKENS = [
    "python", "pandas", "polars", "numpy", "sql", "tableau", "power bi",
    "looker", "excel", "snowflake", "databricks", "spark", "dbt", "airflow",
    "aws", "gcp", "azure", "scikit-learn", "pytorch", "tensorflow", "xgboost",
    "machine learning", "deep learning", "nlp", "regression",
    "logistic regression", "survival analysis", "kaplan-meier",
    "causal inference", "anova", "forecasting", "claims data", "marketscan",
    "ehr", "oncore", "hcpcs", "omop", "heor", "epidemiology", "cdisc",
    "fhir", "git", "docker", "statistics",
]

# Ordinary filler words that should not, on their own, match any skill.
_FILLER_WORDS = [
    "the", "team", "will", "work", "with", "our", "data", "and", "to",
    "build", "support", "role", "candidate", "experience", "years",
]

_tokens = st.sampled_from(_SKILL_TOKENS)
_fillers = st.sampled_from(_FILLER_WORDS)

# A seeded fragment: a shuffle of skill tokens and filler words joined into a
# sentence-like string. This guarantees several skills appear in varied order.
_seeded_text = st.lists(
    st.one_of(_tokens, _tokens, _fillers),
    min_size=0,
    max_size=12,
).map(lambda parts: " ".join(parts))

# Free-form text including Unicode, newlines, punctuation.
_free_text = st.text(max_size=400)

# A single field value: None, free-form text, seeded text, or a mix of both.
_field = st.one_of(
    st.none(),
    _free_text,
    _seeded_text,
    st.builds(lambda a, b: a + "\n" + b, _seeded_text, _free_text),
    st.builds(lambda a, b: a + " " + b, _free_text, _seeded_text),
)


# ---------------------------------------------------------------------------
# Property 22
# ---------------------------------------------------------------------------


@settings(max_examples=500)
@given(title=_field, description=_field)
def test_keyword_output_invariants(title, description):
    result = backend.summarize(title, description)

    skills = result["skills"]
    summary = result["summary"]

    # Shape: skills is a list of strings, summary is a string.
    assert isinstance(skills, list)
    assert all(isinstance(s, str) for s in skills)
    assert isinstance(summary, str)

    # Requirement 11.3: no duplicates.
    assert len(skills) == len(set(skills)), f"duplicate skills in {skills!r}"

    # Every returned skill is a canonical Skills_Dictionary name.
    for name in skills:
        assert name in _CANONICAL_NAMES, f"{name!r} is not a canonical skill name"

    # Requirement 11.3: ordered by first appearance position in
    # title + "\n" + description. Recompute positions with the independent
    # oracle and check the returned order is non-decreasing by position.
    text = (title or "") + "\n" + (description or "")
    positions = []
    for name in skills:
        pos = _earliest_position(name, text)
        assert pos is not None, (
            f"{name!r} was returned but the oracle found no match in text"
        )
        positions.append(pos)
    assert positions == sorted(positions), (
        f"skills {skills!r} not ordered by first-appearance positions {positions!r}"
    )

    # Requirement 11.5: summary length is capped at 300 characters.
    assert len(summary) <= MAX_SUMMARY_CHARS, (
        f"summary length {len(summary)} exceeds {MAX_SUMMARY_CHARS}"
    )

    # Requirement 11.7: determinism - a second call gives identical output.
    again = backend.summarize(title, description)
    assert again == result, "backend output differs across identical calls"
