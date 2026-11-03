# ============================================================
# PERSONAL CONFIGURATION
# Controls scope, filtering, and publishing limits.
#
# This module is a plain collection of constants and compiled
# regexes. It is imported by scraper.py and filters.py and must
# have no side effects beyond defining these names.
# ============================================================

import re

# Which ATS platforms to scan. Add "ashby" later once measured.
ENABLED_PLATFORMS = (
    "greenhouse"
    , "lever"
    , "ashby"
    , "workday"
    , "bamboohr"
    , "icims"
    # , "paylocity"
    )


US_LOCATION_PATTERNS = (
    "united states",
    "u.s.",
    "usa",
    "us remote",
    "remote, us",
    "remote - us",
    "remote (us)",
    "remote - united states",
    "remote (united states)"
)

US_STATE_ABBREVIATIONS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC"
}

COMPANY_BLOCKLIST = {
    "lensa"
}


# ============================================================
# TITLE MATCHING
# ============================================================

# Titles matching this pattern are excluded entirely as too senior.
# Only the Excluded_Seniority_Terms: principal, director, VP/vice
# president, head of, chief, distinguished, staff, senior manager,
# and group manager. "senior"/"sr" alone are NOT here any more; a
# plain "senior"/"sr"/level-III title reaches the tier rule and lands
# in the "maybe" bucket instead of being dropped.
SENIOR_EXCLUDE_PATTERN = re.compile(
    r"\b(principal|director|vp|vice\s*president|head\s*of|chief|distinguished|staff"
    r"|(senior|sr\.?)\s+manager|group\s+manager)\b",
    re.IGNORECASE,
)

# Primary titles: matched anywhere in the title, case-insensitive.
# Built around the user's CV role families; Data Scientist titles are
# ranked first by DATA_SCIENTIST_PATTERNS below. Each line names what
# it catches.
TITLE_PATTERNS = [
    # --- Data science / data analytics ---
    re.compile(r"\bdata\s*scien(tist|ce)\b", re.IGNORECASE),                          # data scientist, data science (any level/prefix)
    re.compile(r"\bdata\s*analyst\b", re.IGNORECASE),                                 # data analyst (incl. numbered levels I-V via \b boundary)
    re.compile(r"\bhealth(care)?\s+(data\s+)?analyst\b", re.IGNORECASE),              # healthcare/health (data) analyst
    re.compile(r"\bclinical\s+data\s+(analyst|scientist)\b", re.IGNORECASE),          # clinical data analyst/scientist

    # --- Real-world data / evidence ---
    re.compile(r"\brw[de]\b", re.IGNORECASE),                                         # RWD / RWE
    re.compile(r"real[\s-]?world\s*(data|evidence)", re.IGNORECASE),                  # real-world data / real world evidence

    # --- HEOR / health economics / outcomes research ---
    re.compile(r"\bheor\b", re.IGNORECASE),                                           # HEOR
    re.compile(r"\bhealth\s+econom(ics|ist)\b", re.IGNORECASE),                       # health economics / health economist
    re.compile(r"\boutcomes\s+research\b", re.IGNORECASE),                            # outcomes research (analyst/scientist)

    # --- Epidemiology ---
    re.compile(r"\b(pharmaco)?epidemiolog(ist|y)\b", re.IGNORECASE),                  # epidemiologist / pharmacoepidemiology

    # --- Biostatistics / statistics ---
    re.compile(r"\b(bio)?statistician\b", re.IGNORECASE),                             # biostatistician / statistician

    # --- Statistical programming ---
    re.compile(r"\bstatistical\s+programm(er|ing)\b", re.IGNORECASE),                 # statistical programmer / programming
    re.compile(r"\bsas\s+(programmer|developer|analyst)\b", re.IGNORECASE),           # SAS programmer / developer / analyst

    # --- Research analyst family ---
    re.compile(r"\bresearch\s+data\s+analyst\b", re.IGNORECASE),                      # research data analyst
    re.compile(r"\b(clinical|health(care)?|outcomes)\s+research\s+analyst\b", re.IGNORECASE),  # clinical/health/outcomes research analyst

    # --- Analytics engineer ---
    re.compile(r"\banalytics\s*engineer\b", re.IGNORECASE),                           # analytics engineer

    # --- Business intelligence ---
    re.compile(r"\b(bi|business\s+intelligence)\s+(analyst|developer|engineer)\b", re.IGNORECASE),  # BI / business intelligence analyst/developer/engineer

    # --- Analytics roles ---
    re.compile(r"\banalytics\s+(analyst|specialist|consultant)\b", re.IGNORECASE),    # analytics analyst/specialist/consultant

    # --- Market research and insights ---
    re.compile(r"\bmarket\s+research\s+(analyst|scientist|associate)\b", re.IGNORECASE),  # market research analyst/scientist/associate
    re.compile(r"\bcommercial\s+(analytics|insights)\b", re.IGNORECASE),              # commercial analytics / insights
    re.compile(r"\binsights?\s+(analyst|scientist)\b", re.IGNORECASE),               # insights analyst/scientist (moved from MAYBE)

    # --- Data and ML engineering ---
    re.compile(r"\bdata\s*engineer\b", re.IGNORECASE),                                # data engineer
    re.compile(r"machine\s*learning\s*(engineer|scientist)", re.IGNORECASE),          # machine learning engineer/scientist
    re.compile(r"\bml\s*(engineer|scientist)\b", re.IGNORECASE),                      # ML engineer/scientist
    re.compile(r"\bai\s*engineer\b", re.IGNORECASE),                                  # AI engineer

    # --- Scientist and quant (kept) ---
    re.compile(r"(applied|research)\s*scientist", re.IGNORECASE),                     # applied scientist / research scientist
    re.compile(r"\bdecision\s*scientist\b", re.IGNORECASE),                           # decision scientist
    re.compile(r"quantitative\s*(analyst|researcher)", re.IGNORECASE),               # quantitative analyst/researcher
]


# Secondary/adjacent titles: shown in a separate "Maybe relevant"
# bucket, not silently added to the main list. Review these manually
# and promote good matches into TITLE_PATTERNS above.
MAYBE_PATTERNS = [
    re.compile(r"\bclinical\s+research\s+(coordinator|associate)\b", re.IGNORECASE),  # clinical research coordinator/associate
    re.compile(r"\bclinical\s+data\s+manager\b", re.IGNORECASE),                      # clinical data manager
    re.compile(r"\bdata\s+manager\b", re.IGNORECASE),                                 # data manager
    re.compile(r"\bproduct\s*(data\s*)?(scientist|analyst)\b", re.IGNORECASE),        # product (data) scientist/analyst
    re.compile(r"\boperations?\s*research\b", re.IGNORECASE),                         # operations research
    re.compile(r"\bdata\s*strategist\b", re.IGNORECASE),                              # data strategist
    re.compile(r"\bmanager\b.*\b(data|analytics|ml|machine\s*learning)\b", re.IGNORECASE),  # manager ... data/analytics/ML
    re.compile(r"\b(data|analytics|ml)\b.*\bmanager\b", re.IGNORECASE),              # data/analytics/ML ... manager
]


# Data_Scientist_Title patterns. A title matching any of these gets
# Title_Priority 1 (listed first on the site). Everything else kept
# gets Title_Priority 2.
DATA_SCIENTIST_PATTERNS = [
    re.compile(r"\bdata\s*scien(tist|ce)\b", re.I),                                   # data scientist/science, any prefix/level
    re.compile(r"\b(rw[de]|real[\s-]?world\s+(evidence|data))\s+scientist\b", re.I),  # RWE/RWD/real-world (evidence|data) scientist
    re.compile(r"\bdecision\s+scientist\b", re.I),                                    # decision scientist
]

# "applied scientist" / "research scientist" count as Data_Scientist
# only when the title also carries a data qualifier (below), so that
# e.g. "Research Scientist, Cell Biology" stays priority 2.
QUALIFIED_SCIENTIST_PATTERN = re.compile(r"\b(applied|research)\s+scientist\b", re.I)  # applied/research scientist

# Data qualifier that promotes a qualified scientist title to priority 1.
DATA_QUALIFIER_PATTERN = re.compile(
    r"\b(data|machine\s+learning|ml|ai|analytics|statistic\w*|quantitative|computational|nlp)\b",
    re.I,
)  # data / ML / AI / analytics / statistics / quantitative / computational / NLP


# Titles matching any of these are excluded outright (title_excluded),
# regardless of other matches. Initial set, user-tunable.
TITLE_EXCLUDE_PATTERNS = [
    re.compile(r"\bintern(ship)?\b", re.IGNORECASE),                                  # intern / internship
    re.compile(r"\bco-?op\b", re.IGNORECASE),                                         # co-op / coop
    re.compile(r"\bapprentice", re.IGNORECASE),                                       # apprentice / apprenticeship
    re.compile(r"m/w/d", re.IGNORECASE),                                              # German job-ad gender marker (non-US)
    re.compile(r"\b(security|cyber|soc|threat)\s+analyst\b", re.IGNORECASE),          # security/cyber/SOC/threat analyst
    re.compile(r"\b(credit|loan|underwriting|payroll|claims)\s+analyst\b", re.IGNORECASE),  # finance/claims analyst (not "claims DATA analyst")
    re.compile(r"\b(help|service)\s+desk\b", re.IGNORECASE),                          # help/service desk
]


# ============================================================
# RECRUITER DETECTION
# ============================================================

# Staffing/recruiting terms. The RecruiterDetector in filters.py
# tokenizes the slug and name and matches these only as whole tokens,
# so "search" no longer flags "iqviaresearch" or "research-triangle".
RECRUITER_TERMS = [
    "recruit",
    "recruiting",
    "recruiter",
    "staffing",
    "staff",
    "talent",
    "talenthub",
    "talentgroup",
    "solutions",
    "consulting",
    "placement",
    "search",
    "resources",
    "agency",
]

# Company slugs that should never be flagged as recruiters even if a
# RECRUITER_TERMS token appears in the slug or name.
RECRUITER_ALLOWLIST = set()


# ============================================================
# DIAGNOSTICS / REVIEW
# ============================================================

# Company slugs whose per-job filter decisions are written in detail
# to the Diagnostics_Report for inspection.
WATCHLIST = set()


# ============================================================
# PUBLISHING CAPS
# ============================================================

MAX_PUBLISHED_JOBS = 10_000   # cap on primary-bucket jobs published
MAX_MAYBE_JOBS = 2_000        # cap on maybe-bucket jobs published (was an implicit // 5)


# ============================================================
# DETAIL ENRICHMENT
# ============================================================

DETAIL_MAX_WORKERS = 8              # max concurrent detail requests per platform
DETAIL_MAX_PER_RUN = None          # max detail fetches per platform per run (None = unlimited)
PLATFORM_TIME_BUDGET_MINUTES = None  # optional safety knob, off by default; if set, stop starting
                                     # new company fetches for a platform once its elapsed time
                                     # exceeds this many minutes (recorded as informational)


# ============================================================
# LOCATION FILTER
# ============================================================

BOSTON_CENTER_LAT = 42.3601   # Boston_Center reference latitude
BOSTON_CENTER_LNG = -71.0589  # Boston_Center reference longitude
BOSTON_RADIUS_MILES = 30      # keep hybrid/onsite jobs within this great-circle distance of Boston

# Secondary_Region: US states whose hybrid/onsite jobs outside the
# radius go to the "maybe" bucket. An empty set disables the region.
SECONDARY_REGION_STATES = {"MA", "NH", "RI", "CT", "VT", "ME"}  # New England


# ============================================================
# WORKDAY PAGINATION
# ============================================================

WORKDAY_MAX_PAGES = 25  # stop a Workday query after this many pages and mark it truncated

# One paginated Workday query is run per term, covering the CV's role
# families. Each term costs at least one request per tenant, so the number of
# requests per tenant scales with this list. An empty list switches to a
# single full-pagination query with no search text. The optional
# PLATFORM_TIME_BUDGET_MINUTES knob bounds the total time these queries take.
WORKDAY_SEARCH_TERMS = [
    "data scientist",    # data scientist roles
    "data science",      # data science roles
    "data analyst",      # data analyst roles
    "analytics",         # analytics roles
    "analyst",           # broad analyst sweep
    # The stems below are kept as stems: a read-only check against
    # iqvia|wd1|iqvia (task 3.3) confirmed Workday's searchText matches word
    # stems, so each stem returns a superset of its full word
    # (statistic 146 > statistician 21, biostatistic 47 > biostatistician 12,
    # epidemiolog 58 > epidemiologist 9). No stem-to-word replacement needed.
    "statistic",         # statistician / biostatistician / statistical programmer
    "biostatistic",      # biostatistics roles
    "epidemiolog",       # epidemiology roles
    "outcomes research", # outcomes research roles
    "real world",        # real-world data/evidence roles
    "heor",              # HEOR / health economics roles
    "insights",          # insights / market research roles
    "data",              # broad data sweep (catches remaining data titles)
]


# ============================================================
# CATEGORY / INDUSTRY / PhD GATES
# ============================================================

# Any of these terms in the title or description marks a life-sciences
# signal, used to gate Research_Titles into the life_sciences_research
# category. Matched case-insensitively (whole-word where sensible).
LIFE_SCIENCES_SIGNAL_TERMS = [
    "pharma", "pharmaceutical", "biotech", "biotechnology", "biopharma",
    "life science", "life sciences", "clinical", "clinical trial", "drug",
    "therapeutic", "therapeutics", "oncology", "patient", "ehr", "emr",
    "claims data", "rwe", "rwd", "real-world evidence", "real-world data",
    "real world evidence", "real world data", "epidemiolog", "heor",
    "health economics", "fda", "medical device", "cro", "hospital",
    "healthcare payer", "healthcare provider", "payer", "provider",
    "diagnostics", "genomics", "molecule", "indication", "disease",
]

# A PhD is required (exclude the job) when one of these matches near a
# PhD mention and no PHD_SOFT_PATTERNS entry vetoes it in the window.
PHD_REQUIRED_PATTERNS = [re.compile(p, re.I) for p in (
    r"\bph\.?\s?d\.?\s+(is\s+)?required\b",            # "phd required" / "phd is required"
    r"\brequires?\s+a\s+ph\.?\s?d\b",                  # "requires a phd"
    r"\bmust\s+have\s+a\s+ph\.?\s?d\b",                # "must have a phd"
    r"\bdoctorate\s+required\b",                       # "doctorate required"
    r"\bph\.?\s?d\.?\s*/\s*doctorate\s+required\b",    # "phd/doctorate required"
    r"\ba\s+ph\.?\s?d\b[\w\s,]{0,40}\bis\s+required\b",  # "a phd ... is required"
)]

# Softening phrasings. A match near a PhD mention vetoes the exclusion
# so that PhD-preferred jobs are kept.
PHD_SOFT_PATTERNS = [re.compile(p, re.I) for p in (
    r"\bph\.?\s?d\.?\s+(preferred|a\s+plus|desirable|desired|nice\s+to\s+have|or\s+equivalent)\b",  # "phd preferred" / "phd a plus" / "phd or equivalent" ...
    r"\b(master'?s?|ms|bachelor'?s?|bs)\s*(,\s*master'?s?,?)?\s*(or|/)\s*ph\.?\s?d\b",  # "master's or phd" / "bachelor's, master's, or phd"
    r"\bms\s*/\s*ph\.?\s?d\b",                          # "ms/phd"
)]
