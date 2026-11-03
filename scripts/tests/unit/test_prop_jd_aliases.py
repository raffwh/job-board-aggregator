"""Property-based test for Skills_Dictionary alias detection and short-skill context.

Property 23: Dictionary aliases are detected in context.

For any Skills_Dictionary entry, any of its example aliases, and any filler text
drawn from ordinary English words, inserting the alias as a separate word
(surrounded by spaces or punctuation) SHALL cause the Keyword_Backend to return
the entry's canonical name (Requirement 11.2).

For any filler text that contains ordinary lowercase words like "go", "or", "a",
"r", "c", "epic", "clarity", "cox", "gee", and "polar" in sentence positions
without a context pattern, the backend SHALL NOT return the short/ambiguous
skills (R, SAS, Go, C++, C#, Epic/Clarity, Cox Regression, GEE, CPT, NDC, DRG)
(Requirement 11.4).

The positive side pairs each canonical skill with a representative alias written
in its required form (uppercase/context for the short skills). The alias is
embedded inside randomly generated ordinary-English filler, separated by spaces
or punctuation, and the test asserts the canonical name appears in the extracted
skills.

The negative side generates sentences built only from ordinary lowercase words
(including the deceptive words "epic", "clarity", "go", "cox", "polar", "sas",
"cpt", lone "r"/"c", "gee") and asserts that none of the short/context-gated
skills are returned.

Validates: Requirements 11.2, 11.4
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from jd_skills import SKILLS
from jd_summarizer import KeywordBackend


backend = KeywordBackend()

# Canonical names the dictionary can emit.
_CANONICAL_NAMES = {skill.name for skill in SKILLS}

# The short / ordinary-word / case-or-context skills from Requirement 11.4 and
# design Component 7. These must only match in their required case/context and
# never on ordinary lowercase prose.
_SHORT_CONTEXT_SKILLS = {
    "R",
    "SAS",
    "Go",
    "C++",
    "C#",
    "Epic/Clarity",
    "Cox Regression",
    "GEE",
    "CPT",
    "NDC",
    "DRG",
    "ICD-10",
    "polars",
}


# ---------------------------------------------------------------------------
# Positive aliases
#
# One representative alias per canonical skill, written in the form the
# Skills_Dictionary requires it to match (uppercase / context for the short
# skills). Each entry is a (canonical_name, alias_text) pair. The alias is
# dropped into ordinary filler as a separate token, so the backend must return
# the canonical name.
# ---------------------------------------------------------------------------

_POSITIVE_ALIASES = [
    ("Python", "python"),
    ("pandas", "pandas"),
    ("polars", "polars"),
    ("NumPy", "numpy"),
    ("SQL", "sql"),
    ("SQL Server", "T-SQL"),
    ("PostgreSQL", "postgresql"),
    ("MySQL", "mysql"),
    # R only matches with context: "R," / "R and" / "R programming" / RStudio.
    ("R", "R programming"),
    # SAS only matches uppercase.
    ("SAS", "SAS"),
    ("RMarkdown", "R Markdown"),
    ("Jupyter", "jupyter"),
    ("matplotlib", "matplotlib"),
    ("seaborn", "seaborn"),
    # Go only matches as Golang or "Go programming/language".
    ("Go", "Golang"),
    # C++ / C# are literal.
    ("C++", "C++"),
    ("C#", "C#"),
    ("Tableau", "tableau"),
    ("Power BI", "power bi"),
    ("Looker", "looker"),
    ("Excel", "excel"),
    ("Snowflake", "snowflake"),
    ("Databricks", "databricks"),
    ("Spark", "pyspark"),
    ("dbt", "dbt"),
    ("Airflow", "airflow"),
    ("AWS", "aws"),
    ("GCP", "gcp"),
    ("Azure", "azure"),
    ("scikit-learn", "scikit-learn"),
    ("PyTorch", "pytorch"),
    ("TensorFlow", "tensorflow"),
    ("XGBoost", "xgboost"),
    ("Random Forest", "random forest"),
    ("LASSO/Ridge", "lasso"),
    ("PCA", "pca"),
    ("k-means", "k-means"),
    ("Machine Learning", "machine learning"),
    ("Deep Learning", "deep learning"),
    ("NLP", "nlp"),
    ("Regression", "regression"),
    ("Logistic Regression", "logistic regression"),
    ("GLM", "glm"),
    # GEE matches uppercase or spelled out.
    ("GEE", "GEE"),
    ("Mixed-Effects Models", "mixed-effects"),
    ("Survival Analysis", "survival analysis"),
    ("Kaplan-Meier", "kaplan-meier"),
    # Cox only matches "Cox proportional/regression/model/PH".
    ("Cox Regression", "Cox proportional"),
    ("A/B Testing", "a/b test"),
    ("Causal Inference", "causal inference"),
    ("Propensity Score", "propensity score"),
    ("ANOVA", "anova"),
    ("Hypothesis Testing", "hypothesis testing"),
    ("Forecasting", "forecasting"),
    ("Claims Data", "claims data"),
    ("MarketScan", "marketscan"),
    ("EHR/EMR", "ehr"),
    # Epic/Clarity needs uppercase EPIC or "Epic Clarity"/"Clarity database".
    ("Epic/Clarity", "EPIC"),
    ("OnCore", "oncore"),
    # ICD-10 matches uppercase ICD-10 / ICD-9.
    ("ICD-10", "ICD-10"),
    ("HCPCS", "hcpcs"),
    # CPT / NDC / DRG match uppercase only.
    ("CPT", "CPT"),
    ("NDC", "NDC"),
    ("DRG", "DRG"),
    ("OMOP", "omop"),
    ("HEOR", "heor"),
    ("Epidemiology", "epidemiology"),
    ("CDISC", "cdisc"),
    ("SDTM", "sdtm"),
    ("ADaM", "adam"),
    ("FHIR", "fhir"),
    ("HL7", "hl7"),
    ("RWE/RWD", "real-world evidence"),
    ("Git", "git"),
    ("Docker", "docker"),
    ("Statistics", "statistics"),
]

# Sanity check: every canonical name in the dictionary has a positive alias.
_COVERED = {name for name, _ in _POSITIVE_ALIASES}
_MISSING = _CANONICAL_NAMES - _COVERED
assert not _MISSING, f"positive aliases missing for canonical skills: {_MISSING}"


# ---------------------------------------------------------------------------
# Filler generators
# ---------------------------------------------------------------------------

# Ordinary English words that never, on their own, name a skill. These include
# the deceptive lowercase words that the short/context skills must ignore:
# "go", "or", "a", "r", "c", "epic", "clarity", "cox", "gee", "polar", "sas",
# "cpt". None of these should trigger a short/context skill.
_FILLER_WORDS = [
    "the", "team", "will", "work", "with", "our", "to", "build", "support",
    "role", "candidate", "experience", "years", "an", "epic", "opportunity",
    "clarity", "of", "purpose", "go", "meeting", "or", "a", "r", "c", "cox",
    "polar", "bear", "sas", "cpt", "gee", "whiz", "dr", "please", "join", "us",
]

_filler_words = st.sampled_from(_FILLER_WORDS)

# A sentence of ordinary lowercase words, space-joined. This is pure filler:
# nothing here should match a short/context skill.
_filler_text = st.lists(_filler_words, min_size=0, max_size=14).map(
    lambda parts: " ".join(parts)
)

# Separators placed around an embedded alias so it stays a separate token.
_SEPARATORS = [" ", "  ", ", ", ". ", "; ", " / ", " (", ") ", " - ", "\n"]
_separator = st.sampled_from(_SEPARATORS)


# ---------------------------------------------------------------------------
# Property 23 - positive: aliases are detected in context
# ---------------------------------------------------------------------------


@settings(max_examples=600)
@given(
    pair=st.sampled_from(_POSITIVE_ALIASES),
    before=_filler_text,
    after=_filler_text,
    left=_separator,
    right=_separator,
    in_title=st.booleans(),
)
def test_alias_detected_in_filler(pair, before, after, left, right, in_title):
    """Embedding an alias as a separate word yields its canonical name (11.2)."""
    canonical, alias = pair
    embedded = f"{before}{left}{alias}{right}{after}"

    if in_title:
        result = backend.summarize(embedded, "")
    else:
        result = backend.summarize("Some Role", embedded)

    assert canonical in result["skills"], (
        f"alias {alias!r} for {canonical!r} not detected in text {embedded!r}; "
        f"got {result['skills']!r}"
    )


# ---------------------------------------------------------------------------
# Property 23 - negative: ordinary lowercase prose does not trigger short skills
# ---------------------------------------------------------------------------


@settings(max_examples=600)
@given(before=_filler_text, after=_filler_text)
def test_ordinary_words_do_not_match_short_skills(before, after):
    """Lowercase filler never triggers the short/context-gated skills (11.4)."""
    text = f"{before} {after}".strip()
    result = backend.summarize("Operations Coordinator", text)

    leaked = _SHORT_CONTEXT_SKILLS.intersection(result["skills"])
    assert not leaked, (
        f"ordinary filler {text!r} wrongly matched short/context skills "
        f"{sorted(leaked)!r}; got {result['skills']!r}"
    )


# ---------------------------------------------------------------------------
# Property 23 - negative: specific deceptive phrases from the design
# ---------------------------------------------------------------------------

# Phrases that use short-skill tokens as ordinary words. None should match.
_DECEPTIVE_PHRASES = [
    "this is an epic opportunity to grow",
    "we value clarity of purpose and communication",
    "please go to the meeting on time",
    "experience with sas tooling is a plus",       # lowercase sas
    "the cpt review board meets weekly",           # lowercase cpt
    "report to Dr. Cox in the clinic",             # Dr. Cox, not Cox regression
    "a polar bear lives in the arctic",            # polar, not polars
    "r and c are letters in the alphabet",         # lone r / c
    "the gee whiz factor was high",                # lowercase gee
    "we want ndc and drg awareness",               # lowercase ndc/drg
    "go or stay, it is your choice",               # go as ordinary verb
]


@given(
    phrase=st.sampled_from(_DECEPTIVE_PHRASES),
    before=_filler_text,
    after=_filler_text,
    in_title=st.booleans(),
)
def test_deceptive_phrases_do_not_match(phrase, before, after, in_title):
    """Known ordinary-word phrases never match the short/context skills (11.4)."""
    text = f"{before} {phrase} {after}".strip()

    if in_title:
        result = backend.summarize(text, "")
    else:
        result = backend.summarize("Program Manager", text)

    leaked = _SHORT_CONTEXT_SKILLS.intersection(result["skills"])
    assert not leaked, (
        f"deceptive text {text!r} wrongly matched {sorted(leaked)!r}; "
        f"got {result['skills']!r}"
    )
