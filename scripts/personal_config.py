# ============================================================
# PERSONAL CONFIGURATION
# Controls scope, filtering, and publishing limits.
# ============================================================

# Which ATS platforms to scan. Add "ashby" later once measured.
ENABLED_PLATFORMS = ("greenhouse", "lever", "ashby", "workday" , "bamboohr", "icims", "paylocity")



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


# TITLE_TERMS = ( # ("data scientist", "machine learning engineer", "data analyst")
#     "data scientist",
#     "data science",
#     "machine learning engineer",
#     "ml engineer",
#     "machine learning scientist",
#     "data analyst",
#     "analytics engineer",
#     "decision scientist",
#     "applied scientist",
#     "research scientist",
#     "business intelligence analyst",
#     "quantitative analyst",
#     "data engineer",

# )



import re

# Titles matching these patterns are excluded entirely (too senior).
SENIOR_EXCLUDE_PATTERN = re.compile(
    r"\b(principal|director|vp|vice\s*president|head\s*of|chief|staff\s*(data|ml|ai)|distinguished)\b",
    re.IGNORECASE
)

# Primary titles: matched anywhere in the title, case-insensitive.
TITLE_PATTERNS = [
    re.compile(r"data\s*(scientist|analyst|science)", re.IGNORECASE),
    re.compile(r"machine\s*learning\s*(engineer|scientist)", re.IGNORECASE),
    re.compile(r"\bml\s*(engineer|scientist)\b", re.IGNORECASE),
    re.compile(r"analytics\s*engineer", re.IGNORECASE),
    re.compile(r"(applied|research)\s*scientist", re.IGNORECASE),
    
    re.compile(r"business\s*intelligence\s*analyst", re.IGNORECASE),
    re.compile(r"quantitative\s*(analyst|researcher)", re.IGNORECASE),
    re.compile(r"\bdata\s*engineer\b", re.IGNORECASE),

    # Catches "RWD/RWE Data Scientist", "Real-World Data Analyst", etc.
    re.compile(r"\brwd\b|\brwe\b|real[\s-]?world\s*(data|evidence)", re.IGNORECASE),

    # Catches numbered/roman-numeral levels: "Data Analyst II", "Data Analyst 3"
    re.compile(r"data\s*(analyst|scientist)\s*(i{1,3}|iv|v|[1-5])\b", re.IGNORECASE),

    re.compile(r"\bai\s*engineer\b", re.IGNORECASE),

    re.compile(r"decision\s*scientist", re.IGNORECASE),
    re.compile(r"\bdecision\s*scientist\b", re.IGNORECASE),
]


# Secondary/adjacent titles: shown in a separate "Maybe relevant" bucket,
# not silently added to the main list. Review these manually and promote
# good matches into TITLE_PATTERNS above.
MAYBE_PATTERNS = [
    re.compile(r"\bmanager\b.*\b(data|analytics|ml|machine\s*learning)\b", re.IGNORECASE),
    re.compile(r"\b(data|analytics|ml)\b.*\bmanager\b", re.IGNORECASE),
    re.compile(r"\bproduct\s*(data\s*)?(scientist|analyst)\b", re.IGNORECASE),
    re.compile(r"\bstatistician\b", re.IGNORECASE),
    # re.compile(r"\boperations?\s*research\b", re.IGNORECASE),
    re.compile(r"\bbiostatistician\b", re.IGNORECASE),
    re.compile(r"\bdata\s*strategist\b", re.IGNORECASE),
    re.compile(r"\binsights?\s*(analyst|scientist|lead)\b", re.IGNORECASE),
]

MAX_PUBLISHED_JOBS = 20_000