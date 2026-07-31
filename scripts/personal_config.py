# ============================================================
# PERSONAL CONFIGURATION
# Controls scope, filtering, and publishing limits.
# ============================================================

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
    r"\b(senior|sr\.?|principal|director|vp|vice\s*president|head\s*of|chief|staff\s*(data|ml|ai)|distinguished)\b",
    re.IGNORECASE
)

# Primary titles: matched anywhere in the title, case-insensitive.
TITLE_PATTERNS = [
    re.compile(r"data\s*(scientist|analyst|science)", re.IGNORECASE),           #-data scientist, data analyst, data science
    re.compile(r"machine\s*learning\s*(engineer|scientist)", re.IGNORECASE),    #-machine learning engineer, machine learning scientist
    re.compile(r"\bml\s*(engineer|scientist)\b", re.IGNORECASE),                #-ml engineer, ml scientist

    re.compile(r"analytics\s*engineer", re.IGNORECASE),                         #-analytics engineer
    re.compile(r"(applied|research)\s*scientist", re.IGNORECASE),               #-applied scientist, research scientist
    
    re.compile(r"business\s*intelligence\s*analyst", re.IGNORECASE),            #-business intelligence analyst
    re.compile(r"quantitative\s*(analyst|researcher)", re.IGNORECASE),          #-quantitative analyst, quantitative researcher
    re.compile(r"\bdata\s*engineer\b", re.IGNORECASE),                          #-data engineer

    # Catches "RWD/RWE Data Scientist", "Real-World Data Analyst", etc.
    re.compile(r"\brwd\b|\brwe\b|real[\s-]?world\s*(data|evidence|analyst)", re.IGNORECASE), #-RWD/RWE Data Scientist, Real-World Data Analyst

    # Catches numbered/roman-numeral levels: "Data Analyst II", "Data Analyst 3"
    re.compile(r"data\s*(analyst|scientist)\s*(i{1,3}|iv|v|[1-5])\b", re.IGNORECASE),

    re.compile(r"\banalyst\s*\b", re.IGNORECASE),

    re.compile(r"\bai\s*engineer\b", re.IGNORECASE),

    re.compile(r"decision\s*scientist", re.IGNORECASE),
    re.compile(r"\bdecision\s*scientist\b", re.IGNORECASE),

    re.compile(r"\bhealthcare\s*analyst\b", re.IGNORECASE),
    re.compile(r"\bhealthcare\s*data\s*analyst\b", re.IGNORECASE),
]


# Secondary/adjacent titles: shown in a separate "Maybe relevant" bucket,
# not silently added to the main list. Review these manually and promote
# good matches into TITLE_PATTERNS above.
MAYBE_PATTERNS = [
    re.compile(r"\bmanager\b.*\b(data|analytics|ml|machine\s*learning)\b", re.IGNORECASE),      #-manager 
    re.compile(r"\b(data|analytics|ml)\b.*\bmanager\b", re.IGNORECASE),                         #-data analytic manager
    re.compile(r"\bproduct\s*(data\s*)?(scientist|analyst)\b", re.IGNORECASE),                  #-product data 
    re.compile(r"\bstatistician\b", re.IGNORECASE),                                             #-statistician
    re.compile(r"\boperations?\s*research\b", re.IGNORECASE),                                   #-operations research
    re.compile(r"\bbiostatistician\b", re.IGNORECASE),                                          #-biostatistician
    re.compile(r"\bdata\s*strategist\b", re.IGNORECASE),                                        #-data strategist
    re.compile(r"\binsights?\s*(analyst|scientist)\b", re.IGNORECASE),                          #-insights analyst, insights scientist
]

MAX_PUBLISHED_JOBS = 10_000