# jd_skills.py
"""
Skills_Dictionary for the keyword JD backend.

Each :class:`Skill` maps a canonical display name to one or more compiled
regex patterns. ``SKILLS`` is the module-level, ordered list the
``KeywordBackend`` scans. The dictionary order defines the tie-break order
when two skills first appear at the same position in the text.

Pattern rules (design Component 7, Requirements 11.4, 11.9):

- Normal skills use case-insensitive ``\\b<alias>\\b`` patterns.
- Single-letter, short, or ordinary-word skills (R, SAS, Go, C++, C#,
  Epic/Clarity, Cox, GEE, CPT, NDC, DRG) use case-sensitive and/or context
  patterns so ordinary prose ("epic", "clarity", "go", ...) does not match.
- No pattern uses a negative lookahead anywhere. Positive lookaround
  (``(?=...)`` / ``(?<=...)``) is used only to anchor short-skill context.
  A later test lints the compiled patterns to enforce the no-negative-
  lookahead rule, which keeps skill extraction monotonic under appended
  text (Requirement 11.8).

Validates: Requirements 11.4, 11.9
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Pattern


@dataclass(frozen=True)
class Skill:
    """A canonical skill name and the compiled patterns that detect it.

    ``patterns`` is supplied as a list of regex source strings and compiled
    at construction time. ``case_sensitive`` controls whether the
    ``re.IGNORECASE`` flag is applied; short/ordinary-word skills set it to
    ``True`` so that only the exact (usually uppercase) form matches.
    """

    name: str
    patterns: List[str]
    case_sensitive: bool = False
    compiled: List[Pattern] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        flags = 0 if self.case_sensitive else re.IGNORECASE
        compiled = [re.compile(p, flags) for p in self.patterns]
        # frozen dataclass: bypass the immutability guard for the derived field.
        object.__setattr__(self, "compiled", compiled)


def _skill(name: str, *patterns: str, case_sensitive: bool = False) -> Skill:
    """Terse constructor so the table below reads as (name, patterns...)."""
    return Skill(name=name, patterns=list(patterns), case_sensitive=case_sensitive)


# ---------------------------------------------------------------------------
# Skills_Dictionary
#
# Ordered by family. The order only matters as a tie-break when two skills
# share the same earliest match position in a document.
# ---------------------------------------------------------------------------

SKILLS: List[Skill] = [
    # --- Languages and libraries ---
    _skill("Python", r"\bpython\b"),                         # language
    _skill("pandas", r"\bpandas\b"),                         # dataframe lib
    _skill("polars", r"\bpolars\b"),                         # dataframe lib
    _skill("NumPy", r"\bnumpy\b"),                            # numeric lib
    _skill("SQL", r"\bsql\b"),                                # query language
    _skill("SQL Server", r"\bsql\s*server\b", r"\bt-?sql\b"), # MS SQL Server / T-SQL
    _skill("PostgreSQL", r"\bpostgres(ql)?\b"),              # Postgres
    _skill("MySQL", r"\bmysql\b"),                            # MySQL
    _skill("R", r"\bR\b(?=\s*(,|/|and\b|or\b|\)|programming|language|Studio))",
            r"(?<=[,/(]\s)R\b", r"(?<=[,/(])R\b",
            r"\bRStudio\b", r"\bR\s+(programming|language)\b",
            case_sensitive=True),                            # R language (context)
    _skill("SAS", r"\bSAS\b", case_sensitive=True),          # SAS (uppercase only)
    _skill("RMarkdown", r"\br\s*markdown\b"),                # R Markdown
    _skill("Jupyter", r"\bjupyter\b"),                        # notebooks
    _skill("matplotlib", r"\bmatplotlib\b"),                 # plotting lib
    _skill("seaborn", r"\bseaborn\b"),                        # plotting lib
    _skill("Go", r"\bGolang\b", r"\bGo\s+(programming|language)\b",
            case_sensitive=True),                            # Go language (context)
    _skill("C++", r"C\+\+", case_sensitive=True),            # C++ (bare C is not a skill)
    _skill("C#", r"C#", case_sensitive=True),                # C#

    # --- Analytics and BI tools ---
    _skill("Tableau", r"\btableau\b"),                        # BI/viz
    _skill("Power BI", r"power\s*bi"),                        # BI/viz
    _skill("Looker", r"\blooker\b"),                          # BI/viz
    _skill("Excel", r"\bexcel\b"),                            # boundary rejects "excellent"

    # --- Data platforms ---
    _skill("Snowflake", r"\bsnowflake\b"),                   # warehouse
    _skill("Databricks", r"\bdatabricks\b"),                 # lakehouse
    _skill("Spark", r"\b(py)?spark\b"),                       # Spark / PySpark
    _skill("dbt", r"\bdbt\b"),                                # transform tool
    _skill("Airflow", r"\bairflow\b"),                        # orchestration

    # --- Cloud ---
    _skill("AWS", r"\baws\b", r"amazon web services"),       # AWS
    _skill("GCP", r"\bgcp\b", r"google cloud"),              # Google Cloud
    _skill("Azure", r"\bazure\b"),                            # Azure

    # --- Machine learning ---
    _skill("scikit-learn", r"scikit[-\s]?learn", r"\bsklearn\b"),  # sklearn
    _skill("PyTorch", r"\bpytorch\b"),                        # DL framework
    _skill("TensorFlow", r"\btensorflow\b"),                 # DL framework
    _skill("XGBoost", r"\bxgboost\b",
            r"gradient\s+boost(ing|ed)?"),                   # XGBoost / gradient boosting
    _skill("Random Forest", r"random\s+forests?"),           # random forest
    _skill("LASSO/Ridge", r"\blasso\b",
            r"\bridge\s+regression\b",
            r"\bregulariz(ation|ed|e)\b"),                   # LASSO / Ridge
    _skill("PCA", r"\bpca\b",
            r"principal\s+components?\s+analysis"),          # PCA
    _skill("k-means", r"\bk-?means\b"),                       # clustering
    _skill("Machine Learning", r"machine\s+learning"),       # ML (general)
    _skill("Deep Learning", r"deep\s+learning"),             # DL (general)
    _skill("NLP", r"\bnlp\b", r"natural\s+language\s+processing"),  # NLP

    # --- Statistics ---
    _skill("Regression", r"\bregression\b"),                 # regression (general)
    _skill("Logistic Regression", r"logistic\s+regression"),  # logistic regression
    _skill("GLM", r"\bglm\b",
            r"generalized\s+linear\s+models?"),              # GLM
    _skill("GEE", r"\bGEE\b", case_sensitive=True),          # GEE (uppercase)
    _skill("GEE", r"generalized\s+estimating\s+equations?"),  # GEE (spelled out)
    _skill("Mixed-Effects Models",
            r"mixed[-\s]effects?",
            r"\bhierarchical\s+linear\s+models?\b",
            r"\bmixed\s+models?\b"),                         # mixed-effects
    _skill("Survival Analysis", r"survival\s+analysis"),     # survival analysis
    _skill("Kaplan-Meier", r"kaplan[-\s]meier"),             # Kaplan-Meier
    _skill("Cox Regression",
            r"\bCox\s+(proportional|regression|model\w*|PH)\b"),  # Cox (context)
    _skill("A/B Testing", r"\ba/b\s*test", r"\bab\s+test",
            r"experimentation"),                             # A/B testing
    _skill("Causal Inference", r"causal\s+inference"),       # causal inference
    _skill("Propensity Score", r"propensity[-\s]scor"),      # propensity score
    _skill("ANOVA", r"\banova\b"),                            # ANOVA
    _skill("Hypothesis Testing", r"hypothesis\s+test(ing|s)?"),  # hypothesis testing
    _skill("Forecasting", r"\bforecast(ing|s)?\b",
            r"time[-\s]series"),                             # forecasting / time-series

    # --- Healthcare and RWE ---
    _skill("Claims Data", r"claims\s+data"),                 # claims data
    _skill("MarketScan", r"\bmarketscan\b"),                 # MarketScan db
    _skill("EHR/EMR", r"\behr\b", r"\bemr\b",
            r"electronic\s+(health|medical)\s+records?"),    # EHR/EMR
    _skill("Epic/Clarity",
            r"\bEPIC\b",
            r"\bEpic\s+(Clarity|Caboodle|EHR|EMR|Systems|certifi\w*)\b",
            r"\bClarity\s+(database|data|reporting|report\w*)\b",
            case_sensitive=True),                            # Epic/Clarity (context)
    _skill("OnCore", r"\boncore\b"),                          # OnCore CTMS
    _skill("ICD-10", r"\bICD[-\s]?(9|10)(-CM|-PCS)?\b",
            r"\bICD\s+codes?\b", case_sensitive=True),       # ICD-9/10 codes
    _skill("HCPCS", r"\bhcpcs\b"),                            # HCPCS
    _skill("CPT", r"\bCPT\b", case_sensitive=True),          # CPT (uppercase)
    _skill("NDC", r"\bNDCs?\b", case_sensitive=True),        # NDC (uppercase)
    _skill("DRG", r"\bDRGs?\b", case_sensitive=True),        # DRG (uppercase)
    _skill("OMOP", r"\bomop\b"),                              # OMOP CDM
    _skill("HEOR", r"\bheor\b"),                              # HEOR
    _skill("Epidemiology", r"epidemiolog"),                  # epidemiology
    _skill("CDISC", r"\bcdisc\b"),                            # CDISC
    _skill("SDTM", r"\bsdtm\b"),                              # SDTM
    _skill("ADaM", r"\badam\b"),                              # ADaM
    _skill("FHIR", r"\bfhir\b"),                              # FHIR
    _skill("HL7", r"\bhl7\b"),                                # HL7
    _skill("RWE/RWD", r"real[-\s]world\s+(evidence|data)",
            r"\brwe\b", r"\brwd\b"),                          # RWE/RWD

    # --- General engineering ---
    _skill("Git", r"\bgit\b"),                                # version control
    _skill("Docker", r"\bdocker\b"),                          # containers
    _skill("Statistics", r"\bstatistic(s|al)\b"),            # statistics (general)
]
