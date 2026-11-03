"""Property-based test for skill-extraction monotonicity under appended text.

Property 24: Skill extraction is monotonic under appended text.

*For any* description D and text S, the set of skills extracted from D SHALL be
a subset of the skills extracted from ``D + "\\n" + S``, for the same title.
Appending text can only reveal more skills, never hide an existing one, because
the Keyword_Backend relies on compiled patterns that ``.search`` the whole text
and no pattern anchors on the end of the input (Requirement 11.8).

To keep the skill sets non-trivial (so the subset relation has real content to
check rather than passing vacuously on empty sets), both D and S are assembled
from fragments that include known Skills_Dictionary tokens interleaved with
ordinary filler words.

Validates: Requirements 11.8
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from jd_summarizer import KeywordBackend


backend = KeywordBackend()


# ---------------------------------------------------------------------------
# Fragment generators
#
# Known skill tokens are written in the exact case their Skills_Dictionary
# patterns require (uppercase SAS/GEE/CPT/NDC/DRG/ICD-10, context forms for R
# and Cox), so the generated text produces a genuinely non-empty skill set.
# Ordinary filler words are interleaved so that the text is prose-like and the
# extracted set is a proper subset of the dictionary.
# ---------------------------------------------------------------------------

# Tokens that match a Skills_Dictionary entry on their own, in required case.
_SKILL_TOKENS = [
    "Python",
    "pandas",
    "polars",
    "NumPy",
    "SQL",
    "Snowflake",
    "AWS",
    "Tableau",
    "Power BI",
    "Spark",
    "Airflow",
    "PyTorch",
    "TensorFlow",
    "XGBoost",
    "machine learning",
    "deep learning",
    "NLP",
    "regression",
    "survival analysis",
    "Kaplan-Meier",
    "Cox regression",
    "causal inference",
    "propensity score",
    "forecasting",
    "claims data",
    "MarketScan",
    "SAS",
    "GEE",
    "ICD-10",
    "CPT",
    "NDC",
    "DRG",
    "epidemiology",
    "Docker",
    "Git",
]

# Ordinary words that must NOT match any skill pattern, used as filler.
_FILLER_WORDS = [
    "the",
    "team",
    "role",
    "will",
    "build",
    "support",
    "across",
    "stakeholders",
    "deliver",
    "insights",
    "reporting",
    "pipeline",
    "collaborate",
    "experience",
    "working",
    "models",
]

# A single fragment is either a skill token or a filler word.
_fragment = st.one_of(st.sampled_from(_SKILL_TOKENS), st.sampled_from(_FILLER_WORDS))

# A chunk of text is a space-joined run of fragments (possibly empty).
_text_chunk = st.lists(_fragment, min_size=0, max_size=12).map(" ".join)

# Titles: a few realistic titles plus the empty string. The title is held
# fixed between the two extractions so only appended body text varies.
_titles = st.sampled_from(
    [
        "",
        "Data Scientist",
        "Senior Data Analyst",
        "Biostatistician II",
        "HEOR Analyst",
    ]
)


def _skills(title, text):
    return backend.summarize(title, text)["skills"]


# ---------------------------------------------------------------------------
# Property 24
# ---------------------------------------------------------------------------


@settings(max_examples=500)
@given(title=_titles, description=_text_chunk, appended=_text_chunk)
def test_appending_text_only_grows_the_skill_set(title, description, appended):
    """Every skill found in D is also found in ``D + "\\n" + S``."""
    before = _skills(title, description)
    after = _skills(title, description + "\n" + appended)

    missing = set(before) - set(after)
    assert not missing, (
        f"appending text dropped skills {sorted(missing)}; "
        f"title={title!r} D={description!r} S={appended!r}\n"
        f"before={before}\nafter={after}"
    )


@settings(max_examples=300)
@given(title=_titles, description=_text_chunk, appended=_text_chunk)
def test_monotonicity_is_transitive_under_repeated_appends(title, description, appended):
    """Appending twice is still a superset of the original extraction.

    A second, independent append must not shrink the set either, which guards
    against any pattern that could match only at an interior boundary of the
    first appended chunk.
    """
    base = set(_skills(title, description))
    once = set(_skills(title, description + "\n" + appended))
    twice = set(_skills(title, description + "\n" + appended + "\n" + appended))

    assert base <= once, f"first append dropped {sorted(base - once)}"
    assert once <= twice, f"second append dropped {sorted(once - twice)}"
