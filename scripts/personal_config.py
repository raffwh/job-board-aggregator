# ============================================================
# PERSONAL CONFIGURATION
# Controls scope, filtering, and publishing limits.
# ============================================================

# Which ATS platforms to scan. Add "ashby" later once measured.
ENABLED_PLATFORMS = ("greenhouse", "lever", "ashby", "workday")  # "bamboohr", "icims", "paylocity"



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


TITLE_TERMS = ( # ("data scientist", "machine learning engineer", "data analyst")
    "data scientist",
    "data science",
    "machine learning engineer",
    "ml engineer",
    "machine learning scientist",
    "data analyst",
    "analytics engineer",
    "decision scientist",
    "applied scientist",
    "research scientist",
    "business intelligence analyst",
    "quantitative analyst",
    "data engineer",

)

MAX_PUBLISHED_JOBS = 20_000