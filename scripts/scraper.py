import requests, threading, json, random, time, re, os, gzip, argparse, html, functools
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote
from geolocation import build_lookup, lookup_location
from requests.adapters import HTTPAdapter

import personal_config
from personal_config import (
    COMPANY_BLOCKLIST,
    ENABLED_PLATFORMS,
    MAX_PUBLISHED_JOBS,
    MAX_MAYBE_JOBS,
    MAYBE_PATTERNS,
    TITLE_PATTERNS,
    US_LOCATION_PATTERNS,
    US_STATE_ABBREVIATIONS,
    SENIOR_EXCLUDE_PATTERN  
)

from diagnostics import (
    DiagnosticsCollector,
    FetchOutcome,
    RunContext,
    default_context,
)

from ats_helpers import (
    parse_workday_slug,
    extract_req_id,
    workday_location_from_path,
    icims_host_candidates,
)

# New filter chain / location / enrichment / summarizer modules (Component
# 4/5/6/7). The pipeline in save_results wires these together (task 11.1).
import filters
import location_filter
from detail_enricher import enrich_details, needs_detail
from jd_summarizer import summarize_jd

# ============================================================
# CONFIGURATION
# ============================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
GREENHOUSE_FILE = os.path.join(ROOT_DIR, "data", "greenhouse_companies.json")
ASHBY_FILE = os.path.join(ROOT_DIR, "data", "ashby_companies.json")
BAMBOOHR_FILE = os.path.join(ROOT_DIR, "data", "bamboohr_companies.json")
WORKDAY_FILE = os.path.join(ROOT_DIR, "data", "workday_companies.json")
LEVER_FILE = os.path.join(ROOT_DIR, "data", "lever_companies.json")
ICIMS_FILE = os.path.join(ROOT_DIR, "data", "icims_companies.json")
PAYLOCITY_FILE = os.path.join(ROOT_DIR, "data", "paylocity_companies_clean.json")

LOCATIONS_FILE = os.path.join(ROOT_DIR, "data", "locations.json")


PAGEDATA_RE = re.compile(r"window\.pageData\s*=\s*(\{.*?\});\s*</script>", re.DOTALL)

# guid -> company name, filled by load_paylocity()
PAYLOCITY_NAMES = {}

# one requests.Session per worker thread, with a capped connection pool
_paylocity_local = threading.local()


def load_paylocity(filepath):
    """Paylocity clean file is [{guid, name, jobs}, ...], not a flat slug list.
    Returns a set of GUIDs and fills PAYLOCITY_NAMES (guid -> name)."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            rows = json.load(f)
    except FileNotFoundError:
        print(f"File not found: {filepath}")
        return set()
    guids = set()
    for r in rows:
        g = r.get("guid")
        if not g:
            continue
        guids.add(g)
        name = r.get("name") or g
        PAYLOCITY_NAMES[g] = html.unescape(name)
    print(f"Loaded {len(guids):,} Paylocity companies from {filepath}")
    return guids


def _paylocity_session():
    s = getattr(_paylocity_local, "session", None)
    if s is None:
        s = requests.Session()
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=4)
        s.mount("https://", adapter)
        _paylocity_local.session = s
    return s


OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================================================
# GEOLOCATION LOOKUP (loaded once, shared across all workers)
# ============================================================

@functools.lru_cache(maxsize=1)
def get_location_maps():
    """Build and cache the location lookup maps on first use.

    Deferring the 29 MB locations.json read until the first location lookup
    keeps `import scraper` cheap for tests and tooling (Requirement 16.5).
    The lru_cache ensures the file is parsed at most once per process, and the
    result is shared safely across worker threads (read-only after build).
    """
    print("Loading location lookup from locations.json...")
    maps = build_lookup(LOCATIONS_FILE)
    print(f"  {len(maps['city']):,} city-only entries loaded")
    return maps


def enrich_location(location_str):
    """Resolve a location string to (remote, coords). Safe to call from worker threads."""
    result = lookup_location(location_str, get_location_maps())
    return result["remote"], result["coords"]


# ============================================================
# FETCH FRAMEWORK: run_fetch + time budget
# ============================================================


def run_fetch(platform, fn, slug, ctx):
    """Run one internal fetcher, catching any exception at a single place.

    ``fn`` is an internal ``_fetch_<platform>(slug, ctx) -> (jobs, FetchOutcome)``.
    This wrapper is the only place exceptions in fetchers are caught; it records
    ``status="exception"`` with the exception type and message and returns an
    empty job list with that outcome (Requirement 1.3). It replaces the old bare
    ``except Exception: return slug, [], None`` blocks in each fetcher.

    Returns ``(jobs, FetchOutcome)``.
    """
    start = time.monotonic()
    try:
        jobs, outcome = fn(slug, ctx)
        if outcome is not None and not outcome.elapsed_s:
            outcome.elapsed_s = time.monotonic() - start
        return jobs, outcome
    except Exception as e:
        outcome = FetchOutcome(
            platform=platform,
            slug=slug,
            status="exception",
            error_type=type(e).__name__,
            error_message=str(e),
            elapsed_s=time.monotonic() - start,
        )
        return [], outcome


def _outcome_from_response(platform, slug, status_code, jobs, host=None):
    """Build a FetchOutcome from an HTTP status code and the normalized jobs.

    This preserves the current (pre-task-3/4) behavior while attaching a
    Fetch_Outcome: a 200 with jobs is ``ok``, a 200 with no jobs is ``empty``,
    and any other status is ``http_error``. The platform-specific rewrites in
    later tasks (Workday 3.3, iCIMS 4.1, others 4.4) refine the status set
    (``blocked``, ``invalid_slug``, ``truncated``).
    """
    if status_code == 200:
        status = "ok" if jobs else "empty"
    else:
        status = "http_error"
    return FetchOutcome(
        platform=platform,
        slug=slug,
        status=status,
        http_status=status_code,
        fetched=len(jobs),
        host=host,
    )


def _platform_time_budget_seconds(ctx):
    """Return the PLATFORM_TIME_BUDGET_MINUTES budget in seconds, or None (off).

    The config value is the default; the ``PLATFORM_TIME_BUDGET_MINUTES``
    environment variable overrides it when set (Requirement 15.8). A value of
    ``None`` (the default) turns the budget off.
    """
    value = None
    cfg = getattr(ctx, "config", None)
    if cfg is not None:
        value = getattr(cfg, "PLATFORM_TIME_BUDGET_MINUTES", None)

    env = os.environ.get("PLATFORM_TIME_BUDGET_MINUTES")
    if env is not None and env.strip() != "":
        value = env.strip()

    if value is None:
        return None
    try:
        minutes = float(value)
    except (TypeError, ValueError):
        return None
    if minutes <= 0:
        return None
    return minutes * 60.0


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

USER_AGENTS = [
    # Chrome 144 - Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    # Chrome 144 - macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    # Chrome 144 - Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    # Firefox 147 - Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0",
    # Firefox 147 - macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:147.0) Gecko/20100101 Firefox/147.0",
    # Firefox 147 - Linux
    "Mozilla/5.0 (X11; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0",
    # Safari 26 - macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15",
    # Edge 144 - Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0",
]

# ============================================================
# LOAD COMPANIES
# ============================================================


def load_companies(file_path):
    with open(file_path, "r") as f:
        data = json.load(f)

    if not data:
        print(f"Loaded 0 companies from {file_path}")
        return []

    first = data[0]

    if isinstance(first, str):
        companies = sorted(set(data))
        print(f"Loaded {len(companies):,} companies from {file_path}")
        return companies

    if isinstance(first, dict):
        print(f"Loaded {len(data):,} company records from {file_path}")
        return data

    raise ValueError(f"Unsupported company format in {file_path}")


# ============================================================
# VERIFY ACTIVE JOBS + FETCH ALL JOBS
# ============================================================

# API requests for testing in browser console
"""
fetch("https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobBoardWithTeams", {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify({
    operationName: "ApiJobBoardWithTeams",
    variables: {organizationHostedJobsPageName: "zip"},
    query: "query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) { jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) { jobPostings { id title locationName } } }"
  })
}).then(r => r.json()).then(console.log)

fetch("https://{slug}.bamboohr.com/careers/list"){
    method: "GET",
    headers: {"Content-Type": "application/json"},
}.then(r => r.json()).then(console.log)

}
"""

SOURCE_TYPE = "automated"


def get_job_metadata():
    """Generate consistent metadata for each job."""
    return {
        "scraped_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": SOURCE_TYPE,
    }


def _fetch_greenhouse(slug, ctx):
    """Fetch all jobs for a Greenhouse company.

    Returns ``(jobs, FetchOutcome)``. Exceptions propagate to :func:`run_fetch`,
    which records them as ``status="exception"`` (Requirement 1.3).
    """
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    response = requests.get(url, timeout=30)

    normalized = []
    if response.status_code == 200:
        data = response.json()
        jobs = data.get("jobs", [])
        for job in jobs:
            # Full location string, untruncated (Requirement 5.1).
            location = job.get("location", {}).get("name", "Not specified")
            remote, coords = enrich_location(location)
            job_id = job.get("id")
            normalized.append(
                {
                    "company": slug,
                    "company_slug": slug,
                    "title": job.get("title"),
                    "location": location,
                    "locations": [location] if location else [],
                    "remote": remote,
                    "coords": coords,
                    "url": job.get("absolute_url"),
                    "absolute_url": job.get("absolute_url"),
                    "departments": [
                        d.get("name") for d in job.get("departments", [])
                    ],
                    "id": job_id,
                    "ats_job_id": str(job_id) if job_id is not None else None,
                    # Greenhouse list has no created_at; first_published is the
                    # posting date (Requirement 5.4). The description comes later
                    # from the Detail_Enricher.
                    "published_at": job.get("first_published") or "",
                    "updated_at": job.get("updated_at"),
                    "is_recruiter": is_recruiter_company(slug),
                    "ats": "Greenhouse",
                    "skill_level": job_tier_classification(job.get("title", "")),
                    **get_job_metadata(),
                }
            )

    outcome = _outcome_from_response(
        "greenhouse", slug, response.status_code, normalized
    )
    return normalized, outcome


def fetch_company_jobs_greenhouse(slug):
    """Legacy 3-tuple wrapper. Returns ``(slug, jobs, status_code)``."""
    jobs, outcome = run_fetch("greenhouse", _fetch_greenhouse, slug, default_context())
    return slug, jobs, outcome.legacy_status_code()


def _ashby_secondary_locations(job):
    """Return the ``location`` strings from an Ashby posting's ``secondaryLocations``.

    Each entry is ``{"location": str, "address": {...}}``; only the non-empty
    ``location`` values are kept, in order.
    """
    out = []
    for entry in job.get("secondaryLocations") or []:
        if not isinstance(entry, dict):
            continue
        loc = entry.get("location")
        if loc:
            out.append(loc)
    return out


def _fetch_ashby(slug, ctx):
    """Fetch all jobs for an Ashby company. Returns ``(jobs, FetchOutcome)``.

    Uses the Ashby public posting API (Requirement 5.2):
    ``GET https://api.ashbyhq.com/posting-api/job-board/{slug}``. Each posting
    carries ``descriptionPlain``, ``location``, ``secondaryLocations``,
    ``isRemote``, ``workplaceType``, ``address.postalAddress.addressCountry``,
    ``publishedAt`` and ``jobUrl``, so no detail request is needed.
    """
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    headers = {
        "Accept": "application/json",
        "User-Agent": random.choice(USER_AGENTS),
    }

    # Jitter before request to spread out concurrent workers
    time.sleep(random.uniform(0.5, 2.0))

    max_retries = 2
    for attempt in range(max_retries + 1):
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code == 200:
            break
        elif response.status_code in (429, 503, 502):
            if attempt < max_retries:
                backoff = (2**attempt) + random.uniform(0.5, 1.5)
                print(
                    f"  Ashby {slug}: {response.status_code}, retrying in {backoff:.1f}s"
                )
                time.sleep(backoff)
                headers["User-Agent"] = random.choice(USER_AGENTS)
                continue
        # Non-retryable status
        return [], _outcome_from_response("ashby", slug, response.status_code, [])

    if response.status_code != 200:
        return [], _outcome_from_response("ashby", slug, response.status_code, [])

    data = response.json()
    jobs = (data or {}).get("jobs") or []

    normalized = []
    for job in jobs:
        primary = job.get("location") or ""
        secondary = _ashby_secondary_locations(job)
        # Full, untruncated locations: primary first, then secondaries
        # (Requirements 5.1, 5.6).
        locations = ([primary] if primary else []) + secondary
        location = primary or (secondary[0] if secondary else "Not specified")

        workplace = job.get("workplaceType")
        if not workplace and job.get("isRemote"):
            workplace = "remote"

        postal = ((job.get("address") or {}).get("postalAddress") or {})
        native_country = postal.get("addressCountry")

        job_id = job.get("id")
        job_url = job.get("jobUrl") or f"https://jobs.ashbyhq.com/{slug}/{job_id}"

        remote, coords = enrich_location(location)
        normalized.append(
            {
                "company": slug,
                "company_slug": slug,
                "title": job.get("title", ""),
                "location": location,
                "locations": locations,
                "native_workplace": workplace or None,
                "native_country": native_country,
                "description": job.get("descriptionPlain") or "",
                "published_at": job.get("publishedAt") or "",
                "remote": remote,
                "coords": coords,
                "url": job_url,
                "ats_job_id": str(job_id) if job_id is not None else None,
                "id": job_id,
                "is_recruiter": is_recruiter_company(slug),
                "ats": "Ashby",
                "skill_level": job_tier_classification(job.get("title", "")),
                **get_job_metadata(),
            }
        )

    outcome = _outcome_from_response("ashby", slug, response.status_code, normalized)
    return normalized, outcome


def fetch_company_jobs_ashby(slug):
    """Legacy 3-tuple wrapper. Returns ``(slug, jobs, status_code)``."""
    jobs, outcome = run_fetch("ashby", _fetch_ashby, slug, default_context())
    return slug, jobs, outcome.legacy_status_code()


# BambooHR encodes the workplace type as a numeric string in ``locationType``.
# The mapping is pinned from captured fixture data (see
# scripts/tests/fixtures/README.md). Unknown/missing codes map to None, and the
# fetcher then falls back to ``isRemote`` (Requirement 5.5).
_BAMBOOHR_LOCATION_TYPE = {"0": "onsite", "1": "remote", "2": "hybrid"}


def _bamboohr_workplace(location_type, is_remote):
    """Map BambooHR ``locationType``/``isRemote`` to a native workplace string.

    ``locationType`` takes precedence via the pinned mapping; an unknown or
    missing code falls back to ``"remote"`` when ``isRemote`` is true, else
    ``None`` (Requirement 5.5).
    """
    if location_type is not None:
        mapped = _BAMBOOHR_LOCATION_TYPE.get(str(location_type))
        if mapped is not None:
            return mapped
    if is_remote:
        return "remote"
    return None


def _fetch_bamboohr(slug, ctx):
    """Fetch all jobs for a BambooHR company. Returns ``(jobs, FetchOutcome)``.

    SSL errors are retried in place; a final SSL failure is reported as an
    ``http_error`` outcome with no HTTP status (matching the old ``None``
    status code). Other exceptions propagate to :func:`run_fetch`.
    """
    url = f"https://{slug}.bamboohr.com/careers/list"

    time.sleep(random.uniform(0.5, 2.0))

    max_retries = 2
    for attempt in range(max_retries + 1):
        headers = {
            "Accept": "application/json",
            "User-Agent": random.choice(USER_AGENTS),
        }

        try:
            response = requests.get(url, timeout=30, headers=headers)
        except requests.exceptions.SSLError:
            if attempt < max_retries:
                time.sleep((2**attempt) + random.uniform(0.5, 1.5))
                continue
            return [], FetchOutcome(
                platform="bamboohr",
                slug=slug,
                status="http_error",
                http_status=None,
                error_type="SSLError",
            )

        if response.status_code == 200:
            if "application/json" not in response.headers.get("Content-Type", ""):
                return [], _outcome_from_response("bamboohr", slug, 404, [])

            data = response.json()
            jobs = data.get("result", [])

            normalized = []
            for job in jobs:
                # Location can live under ``atsLocation`` (current API) or the
                # older ``location`` key. City/state/country are joined into the
                # full, untruncated location string (Requirements 5.1, 5.5).
                loc = job.get("atsLocation") or job.get("location") or {}
                if isinstance(loc, dict):
                    city = loc.get("city", "")
                    state = loc.get("state", "")
                    country = loc.get("country", "")
                    location = (
                        ", ".join(filter(None, [city, state, country]))
                        or "Not specified"
                    )
                    native_country = loc.get("country") or None
                else:
                    location = str(loc) if loc else "Not specified"
                    native_country = None

                native_workplace = _bamboohr_workplace(
                    job.get("locationType"), job.get("isRemote")
                )

                job_id = job.get("id")
                remote, coords = enrich_location(location)
                normalized.append(
                    {
                        "company": slug,
                        "company_slug": slug,
                        "title": job.get("jobOpeningName"),
                        "location": location,
                        "locations": [location] if location else [],
                        "native_workplace": native_workplace,
                        "native_country": native_country,
                        "published_at": (
                            job.get("datePosted")
                            or job.get("updated_at")
                            or job.get("created_at")
                            or ""
                        ),
                        "remote": remote,
                        "coords": coords,
                        "url": f"https://{slug}.bamboohr.com/careers/{job_id}",
                        "ats_job_id": str(job_id) if job_id is not None else None,
                        "is_recruiter": is_recruiter_company(slug),
                        "ats": "BambooHR",
                        "skill_level": job_tier_classification(
                            job.get("jobOpeningName", "")
                        ),
                        **get_job_metadata(),
                    }
                )
            return normalized, _outcome_from_response(
                "bamboohr", slug, response.status_code, normalized
            )

        if response.status_code in (429, 503, 502):
            if attempt < max_retries:
                backoff = (2**attempt) + random.uniform(0.5, 1.5)
                time.sleep(backoff)
                continue

        return [], _outcome_from_response("bamboohr", slug, response.status_code, [])

    return [], _outcome_from_response("bamboohr", slug, None, [])


def fetch_company_jobs_bamboohr(slug):
    """Legacy 3-tuple wrapper. Returns ``(slug, jobs, status_code)``."""
    jobs, outcome = run_fetch("bamboohr", _fetch_bamboohr, slug, default_context())
    return slug, jobs, outcome.legacy_status_code()


def _lever_description(job):
    """Build a plain-text description for a Lever posting (Requirement 5.3).

    Combines ``descriptionPlain`` with the plain text of each ``lists`` entry
    (its ``text`` heading followed by the HTML ``content`` converted to text),
    so skill and requirement bullets are kept for the JD_Summarizer.
    """
    from detail_enricher import html_to_text

    parts = []
    base = job.get("descriptionPlain")
    if base:
        parts.append(base.strip())

    for entry in job.get("lists") or []:
        if not isinstance(entry, dict):
            continue
        heading = (entry.get("text") or "").strip()
        body = html_to_text(entry.get("content"))
        section = "\n".join(p for p in [heading, body] if p)
        if section:
            parts.append(section)

    return "\n\n".join(parts)


def _fetch_lever(slug, ctx):
    """Fetch all jobs for a Lever company. Returns ``(jobs, FetchOutcome)``."""
    url = f"https://api.lever.co/v0/postings/{slug}"
    response = requests.get(url, timeout=30)

    normalized = []
    if response.status_code == 200:
        jobs = response.json()
        for job in jobs:
            categories = job.get("categories", {}) or {}
            # Full location string plus every provided location
            # (Requirements 5.1, 5.3, 5.6).
            all_locations = [
                loc for loc in (categories.get("allLocations") or []) if loc
            ]
            location = categories.get("location") or (
                all_locations[0] if all_locations else "Not specified"
            )
            if not all_locations and location and location != "Not specified":
                all_locations = [location]

            remote, coords = enrich_location(location)
            published_at = ""
            created_at = job.get("createdAt")
            if created_at:
                published_at = datetime.fromtimestamp(
                    created_at / 1000,
                    tz=timezone.utc,
                ).isoformat()

            job_id = job.get("id")
            normalized.append(
                {
                    "company": slug,
                    "company_slug": slug,
                    "title": job.get("text"),
                    "location": location,
                    "locations": all_locations,
                    "native_workplace": job.get("workplaceType") or None,
                    "native_country": job.get("country"),
                    "description": _lever_description(job),
                    "published_at": published_at,
                    "created_at": created_at,
                    "remote": remote,
                    "coords": coords,
                    "url": job.get("hostedUrl"),
                    "ats_job_id": str(job_id) if job_id is not None else None,
                    "id": job_id,
                    "is_recruiter": is_recruiter_company(slug),
                    "ats": "Lever",
                    "skill_level": job_tier_classification(job.get("text", "")),
                    **get_job_metadata(),
                }
            )

    outcome = _outcome_from_response("lever", slug, response.status_code, normalized)
    return normalized, outcome


def fetch_company_jobs_lever(slug):
    """Legacy 3-tuple wrapper. Returns ``(slug, jobs, status_code)``."""
    jobs, outcome = run_fetch("lever", _fetch_lever, slug, default_context())
    return slug, jobs, outcome.legacy_status_code()


def _parse_workday_posted_on(text):
    """Convert Workday's relative string (e.g. 'Posted 2 Days Ago') to an ISO date."""
    if not text or not isinstance(text, str):
        return None
    t = text.strip().lower()
    today = datetime.now(timezone.utc).date()
    if "today" in t:
        return today.isoformat()
    m = re.search(r"(\d+)\s+day", t)
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    m = re.search(r"(\d+)\s+week", t)
    if m:
        return (today - timedelta(weeks=int(m.group(1)))).isoformat()
    m = re.search(r"(\d+)\s+month", t)
    if m:
        return (today - timedelta(days=int(m.group(1)) * 30)).isoformat()
    return None


# HTTP statuses that justify a Workday page retry (Requirement 3.7).
_WORKDAY_RETRY_STATUSES = (429, 502, 503)
_WORKDAY_PAGE_LIMIT = 20
_WORKDAY_MULTI_LOCATION_RE = re.compile(r"^\d+\s+Locations$", re.IGNORECASE)


class _WorkdayHTTPError(Exception):
    """Raised when a Workday page keeps returning a non-retryable HTTP error.

    Carries the HTTP status so the caller can record it on the Fetch_Outcome
    as an ``http_error`` (Requirement 3.7).
    """

    def __init__(self, status_code):
        super().__init__(f"Workday page returned HTTP {status_code}")
        self.status_code = status_code


def _workday_post_with_retry(api_url, headers, payload, max_attempts=3):
    """POST one Workday page, retrying transient failures (Requirement 3.7).

    Retries on HTTP 429, 502, 503, and network errors up to ``max_attempts``
    times with an increasing delay between attempts. Returns the parsed JSON
    body of the first successful (HTTP 200) response, or ``None`` when every
    attempt hits a retryable failure (so the caller can keep partial results
    and mark the Fetch_Outcome truncated, Requirement 3.8). A non-retryable
    non-200 status raises :class:`_WorkdayHTTPError` so the caller records an
    ``http_error`` outcome.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(api_url, json=payload, headers=headers, timeout=30)
        except requests.RequestException:
            # Network error: retry with backoff, give up after the last attempt.
            if attempt >= max_attempts:
                return None
            time.sleep(random.uniform(1.0, 2.0) * attempt)
            continue

        status = response.status_code
        if status == 200:
            try:
                return response.json()
            except ValueError:
                # A 200 with a non-JSON body is treated as a transient failure.
                if attempt >= max_attempts:
                    return None
                time.sleep(random.uniform(1.0, 2.0) * attempt)
                continue

        if status in _WORKDAY_RETRY_STATUSES:
            if attempt >= max_attempts:
                return None
            time.sleep(random.uniform(1.0, 2.0) * attempt)
            continue

        # Any other status is not retryable; surface it as an http_error.
        raise _WorkdayHTTPError(status)

    return None


def _normalize_workday(job, slug, company, base_url, site_id):
    """Build a normalized Job_Record from one Workday posting.

    Sets ``req_id`` from ``bulletFields`` (Requirement 3.9) and stores the full
    ``location``. When ``locationsText`` matches ``N Locations`` it sets
    ``location_multi=True`` and derives a location hint from ``externalPath``
    (Requirement 3.10). ``workday_external_path`` is stored for the later detail
    fetch. ``published_at`` keeps the existing relative-``postedOn`` handling.
    """
    external_path = job.get("externalPath", "") or ""
    locations_text = job.get("locationsText") or ""

    location_multi = bool(_WORKDAY_MULTI_LOCATION_RE.match(locations_text.strip()))
    if location_multi:
        # "N Locations" is not a usable location; derive a hint from the path.
        # The Detail_Enricher replaces this with real values later.
        location = workday_location_from_path(external_path) or locations_text
    else:
        location = locations_text or "Not specified"

    remote, coords = enrich_location(location)
    posted_date = _parse_workday_posted_on(job.get("postedOn"))

    return {
        "company": company,
        "company_slug": slug,
        "title": job.get("title"),
        "location": location,
        "location_multi": location_multi,
        "remote": remote,
        "coords": coords,
        "req_id": extract_req_id(job.get("bulletFields")),
        "workday_external_path": external_path,
        "url": f"{base_url}/{site_id}{external_path}",
        "published_at": posted_date or "",
        "updated_at": posted_date or "",
        "is_recruiter": is_recruiter_company(company),
        "ats": "Workday",
        "skill_level": job_tier_classification(job.get("title", "") or ""),
        **get_job_metadata(),
    }


def _workday_dedup_key(job_record, posting):
    """Dedup key for a Workday posting: requisition ID, else externalPath.

    Requisition IDs are stable across search-term queries, so they dedupe the
    same posting returned by two terms (Requirement 3.4/3.5). When no
    requisition ID is present, the posting's ``externalPath`` is used.
    """
    return job_record.get("req_id") or posting.get("externalPath") or id(posting)


def _fetch_workday(slug, ctx):
    """Fetch all relevant jobs for a Workday company. Returns ``(jobs, FetchOutcome)``.

    Pagination (Requirement 3):

    - The expected posting total is read from page 1 only; later pages that
      report 0 or omit the total are normal and do not stop pagination
      (3.1, 3.2). This is the IQVIA fix: the old code stopped when ``total``
      changed to 0 on page 2, capping every tenant at 20 postings.
    - A query stops on an empty page or when the fetched count reaches the
      page-1 total (3.3).
    - One paginated query runs per ``WORKDAY_SEARCH_TERMS`` entry; an empty
      list means a single empty-``searchText`` query (3.4, 3.5). Results are
      combined across terms, deduplicated by requisition ID (or externalPath).
    - A query stops at ``WORKDAY_MAX_PAGES`` and marks the outcome truncated
      (3.6).
    - Pages retry on 429/502/503/network up to 3 times (3.7); a failure partway
      through keeps the postings already fetched and marks the outcome
      truncated (3.8).

    An entry that is not a valid Workday_Slug yields an ``invalid_slug``
    outcome (3.11). The outcome records pages, reported_total, and truncated
    (Requirement 1.4).

    slug format: "company|wd#|site_id" e.g. "kohls|wd1|kohlscareers"
    url: https://{company}.wd{num}.myworkdayjobs.com/wday/cxs/{company}/{site_id}/jobs
    """
    parts = parse_workday_slug(slug)
    if parts is None:
        return [], FetchOutcome(platform="workday", slug=slug, status="invalid_slug")

    company, instance, site_id = parts
    base_url = f"https://{company}.wd{instance}.myworkdayjobs.com"
    api_url = f"{base_url}/wday/cxs/{company}/{site_id}/jobs"

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": random.choice(USER_AGENTS),
        "Origin": base_url,
        "Referer": f"{base_url}/{site_id}",
    }

    config = getattr(ctx, "config", None) or personal_config
    max_pages = getattr(config, "WORKDAY_MAX_PAGES", 25)
    terms = getattr(config, "WORKDAY_SEARCH_TERMS", None)
    if not terms:
        terms = [""]

    seen = {}
    total_pages = 0
    truncated = False
    http_error_status = None
    # The reported total for diagnostics is the first term's page-1 total.
    reported_total = None

    for term in terms:
        expected = None
        offset = 0
        pages = 0
        try:
            while True:
                if pages >= max_pages:
                    # Hit the page cap before reaching the expected total (3.6).
                    truncated = True
                    break

                payload = {
                    "appliedFacets": {},
                    "limit": _WORKDAY_PAGE_LIMIT,
                    "offset": offset,
                    "searchText": term,
                }

                data = _workday_post_with_retry(api_url, headers, payload)
                if data is None:
                    # Retries exhausted partway through: keep partial results
                    # and mark truncated (3.8).
                    truncated = True
                    break

                pages += 1
                total_pages += 1

                if expected is None:
                    # Take the total from page 1 only (3.1). Later pages
                    # reporting 0 or omitting total are normal (3.2).
                    expected = data.get("total") or 0
                    if reported_total is None:
                        reported_total = expected

                postings = data.get("jobPostings") or []
                if not postings:
                    # Empty page ends this query (3.3).
                    break

                for posting in postings:
                    record = _normalize_workday(
                        posting, slug, company, base_url, site_id
                    )
                    key = _workday_dedup_key(record, posting)
                    seen.setdefault(key, record)

                offset += len(postings)

                if expected and offset >= expected:
                    # Fetched count reached the page-1 total (3.3).
                    break

                # Jitter between pages to be polite to the endpoint.
                time.sleep(random.uniform(0.3, 1.0))
        except _WorkdayHTTPError as exc:
            # A non-retryable HTTP error. Keep whatever we have; record the
            # status so the outcome is an http_error (3.7).
            http_error_status = exc.status_code
            if seen:
                truncated = True
            break

    jobs = list(seen.values())

    if http_error_status is not None:
        status = "http_error"
    elif jobs:
        status = "ok"
    else:
        status = "empty"

    outcome = FetchOutcome(
        platform="workday",
        slug=slug,
        status=status,
        http_status=http_error_status if http_error_status is not None else 200,
        fetched=len(jobs),
        host=base_url,
        pages=total_pages,
        reported_total=reported_total,
        truncated=truncated,
    )
    return jobs, outcome


def fetch_company_jobs_workday(slug):
    """Legacy 3-tuple wrapper. Returns ``(slug, jobs, status_code)``."""
    jobs, outcome = run_fetch("workday", _fetch_workday, slug, default_context())
    return slug, jobs, outcome.legacy_status_code()


def _parse_icims_sitemap(content, slug):
    """Parse an iCIMS sitemap.xml body into normalized Job_Records.

    The sitemap lists job URLs like
    ``https://<host>/jobs/9620/financial-service-representative/job``; the title
    is derived from the URL path. The sitemap carries no location data, so each
    Job_Record is left with an empty location and ``location_status="unknown"``
    until the Detail_Enricher reads the real location from the posting page's
    JSON-LD (Requirement 4.5). The fabricated ``"United States"`` value and its
    misleading ``remote=False`` default are no longer set.
    """
    root = ET.fromstring(content)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    normalized = []
    for url_el in root.findall(".//s:url", ns):
        loc_el = url_el.find("s:loc", ns)
        if loc_el is None:
            continue
        job_url = loc_el.text.strip() if loc_el.text else ""
        if not job_url or "/jobs/" not in job_url or job_url.endswith("/jobs/intro"):
            continue

        path = job_url.split("/jobs/")[-1]
        parts = path.split("/")
        if len(parts) >= 2:
            title = unquote(parts[1]).replace("-", " ").strip().title()
        else:
            continue

        lastmod_el = url_el.find("s:lastmod", ns)
        updated_at = (
            lastmod_el.text.strip()
            if lastmod_el is not None and lastmod_el.text
            else None
        )

        normalized.append(
            {
                "company": slug,
                "company_slug": slug,
                "title": title,
                # Location is unknown from the sitemap; the Detail_Enricher
                # supplies the real location later (Requirement 4.5).
                "location": "",
                "location_status": "unknown",
                "remote": False,
                "coords": None,
                "url": job_url,
                "updated_at": updated_at,
                "published_at": updated_at,
                "is_recruiter": is_recruiter_company(slug),
                "ats": "iCIMS",
                "skill_level": job_tier_classification(title),
                **get_job_metadata(),
            }
        )

    return normalized


def _fetch_icims(slug, ctx):
    """Fetch all jobs for an iCIMS company. Returns ``(jobs, FetchOutcome)``.

    Host resolution follows :func:`ats_helpers.icims_host_candidates`
    (Requirements 4.1, 4.2): a slug that already carries a known subdomain
    prefix is tried at ``<slug>.icims.com`` first, otherwise
    ``careers-<slug>.icims.com`` is tried first. On HTTP 404 the alternate host
    candidate is tried once before the outcome is recorded (Requirement 4.3).
    An HTTP 403 records status ``blocked`` and is deliberately left out of the
    Dead_Slug_Cache (Requirement 4.4). The resolved host is recorded on the
    Fetch_Outcome.
    """
    headers = {
        "Accept": "application/xml",
        "User-Agent": random.choice(USER_AGENTS),
    }

    candidates = icims_host_candidates(slug)
    last_host = candidates[0]
    last_status = None

    for index, host in enumerate(candidates):
        last_host = host
        sitemap_url = f"https://{host}/sitemap.xml"
        resp = requests.get(sitemap_url, headers=headers, timeout=10)
        last_status = resp.status_code

        # 403: the host exists but blocks us. This is transient, so report
        # ``blocked`` and keep the slug out of the Dead_Slug_Cache
        # (Requirement 4.4). No fallback host is tried.
        if resp.status_code == 403:
            return [], FetchOutcome(
                platform="icims",
                slug=slug,
                status="blocked",
                http_status=403,
                host=host,
            )

        # 404: the host candidate is wrong. Try the alternate candidate once
        # (Requirement 4.3); if this was already the last candidate, fall
        # through to record the 404 so a both-candidates-404 slug can be
        # dead-cached (Requirements 6.3, 6.4).
        if resp.status_code == 404 and index + 1 < len(candidates):
            continue

        if resp.status_code != 200:
            return [], _outcome_from_response(
                "icims", slug, resp.status_code, [], host=host
            )

        normalized = _parse_icims_sitemap(resp.content, slug)
        return normalized, _outcome_from_response(
            "icims", slug, resp.status_code, normalized, host=host
        )

    # Every candidate returned 404: record the permanent failure on the last
    # host tried so should_cache_dead() can cache the slug.
    return [], _outcome_from_response(
        "icims", slug, last_status, [], host=last_host
    )


def fetch_company_jobs_icims(slug):
    """Legacy 3-tuple wrapper. Returns ``(slug, jobs, status_code)``."""
    jobs, outcome = run_fetch("icims", _fetch_icims, slug, default_context())
    return slug, jobs, outcome.legacy_status_code()




def _paylocity_location(j):
    """JobLocation carries the real city/state. LocationName is an internal
    label ('Main', 'AVI') and is only a last-resort fallback."""
    loc = j.get("JobLocation") or {}
    city, state = loc.get("City"), loc.get("State")
    if city and state:
        return html.unescape(f"{city}, {state}")
    if city:
        return html.unescape(city)
    return html.unescape(j.get("LocationName") or "Not specified")


def fetch_company_jobs_paylocity(slug):
    """slug is the Paylocity tenant GUID. Jobs come from window.pageData in the
    page HTML, not a JSON API. Name is resolved from PAYLOCITY_NAMES."""
    url = f"https://recruiting.paylocity.com/recruiting/jobs/All/{slug}/"
    session = _paylocity_session()
    # jitter so concurrent workers don't fire in lockstep
    time.sleep(random.uniform(0.5, 2.0))
    max_retries = 3
    for attempt in range(max_retries + 1):
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        try:
            response = session.get(url, timeout=30, headers=headers)
        except requests.RequestException as e:
            # reset/abort = throttling. drop the poisoned connection, back off, retry.
            try:
                session.close()
            except Exception:
                pass
            _paylocity_local.session = None
            session = _paylocity_session()
            if attempt < max_retries:
                time.sleep((2**attempt) + random.uniform(1.0, 2.0))
                continue
            print(f"Error fetching Paylocity for {slug}: {e}")
            return slug, [], None
        if response.status_code in (429, 503, 502):
            if attempt < max_retries:
                time.sleep((2**attempt) + random.uniform(1.0, 2.0))
                continue
            return slug, [], response.status_code
        if response.status_code != 200:
            return slug, [], response.status_code
        m = PAGEDATA_RE.search(response.text)
        if not m:
            # page loaded but blob missing = soft block or layout drift.
            # return 200 so it retries next run, NOT cached as dead.
            return slug, [], 200
        try:
            data = json.loads(m.group(1))
        except ValueError:
            return slug, [], 200
        company = PAYLOCITY_NAMES.get(slug, slug)
        normalized = []
        for job in data.get("Jobs") or []:
            job_id = job.get("JobId")
            title = html.unescape(job.get("JobTitle") or "")
            location = _paylocity_location(job)
            inferred_remote, coords = enrich_location(location)
            remote = bool(job.get("IsRemote")) or inferred_remote
            dept = job.get("HiringDepartment")  # almost always null on Paylocity
            if dept:
                dept = html.unescape(dept)
            detail = (
                f"https://recruiting.paylocity.com/recruiting/Jobs/Details/{job_id}"
                if job_id
                else url
            )
            normalized.append(
                {
                    "company": company,
                    "company_slug": slug,  # the GUID
                    "title": title,
                    "location": location,
                    "remote": remote,
                    "coords": coords,
                    "url": detail,
                    "absolute_url": detail,
                    "departments": [dept] if dept else [],
                    "id": job_id,
                    "published_at": job.get("updated_at") or job.get("created_at") or "",
                    "updated_at": job.get("PublishedDate"),
                    "is_recruiter": is_recruiter_company(company),
                    "ats": "Paylocity",
                    "skill_level": job_tier_classification(title or ""),
                    **get_job_metadata(),
                }
            )
        return slug, normalized, response.status_code
    return slug, [], None


def _fetch_paylocity(slug, ctx):
    """Internal adapter so ``fetch_all_jobs`` can call Paylocity uniformly.

    Paylocity is out of scope for the platform rewrites, so this simply wraps
    the legacy 3-tuple ``fetch_company_jobs_paylocity`` and converts its result
    to the internal ``(jobs, FetchOutcome)`` contract.
    """
    _slug, jobs, status_code = fetch_company_jobs_paylocity(slug)
    return jobs, _outcome_from_response("paylocity", slug, status_code, jobs)

# TODO - Add Workable
# TODO - Add eightfold
# TODO - Add taleo


def fetch_all_jobs(companies, fetch_fn, platform="ATS", ctx=None):
    """Fetch jobs from all companies in parallel.

    ``fetch_fn`` is an internal ``_fetch_<platform>(slug, ctx) -> (jobs,
    FetchOutcome)``; each call is wrapped in :func:`run_fetch` so a raising
    fetcher is recorded as ``status="exception"`` (Requirement 1.3). Every
    outcome is recorded on ``ctx.diag`` (Requirements 1.2, 1.4).

    When ``PLATFORM_TIME_BUDGET_MINUTES`` is set and the platform's elapsed wall
    time passes the budget, futures that have not started are cancelled and the
    count of cancelled companies is recorded as informational, without being
    treated as an error (Requirements 15.7, 15.8).

    The Dead_Slug_Cache is updated only on a non-scoped run (Requirement 2.7).
    """
    if ctx is None:
        ctx = default_context()

    print("=" * 80)
    print(f"FETCHING JOBS FROM {len(companies):,} COMPANIES FROM PLATFORM: {platform}")
    print("=" * 80 + "\n")

    platform_lower = platform.lower()

    # Scoped-run slug restriction: keep only the requested slugs that belong to
    # this platform's Company_List (Requirement 2.6). A slug names exactly one
    # platform (for example a Workday slug "iqvia|wd1|iqvia" only appears in the
    # Workday list), so intersecting each platform's companies with --slugs
    # routes every slug to the right fetcher.
    scope_slugs = getattr(ctx, "slugs", None)
    if scope_slugs:
        companies = [s for s in companies if s in scope_slugs]

    # Skip known dead slugs
    dead_slugs = load_dead_slugs(platform_lower)
    live_companies = [s for s in companies if s not in dead_slugs]

    # Scoped-run cap: fetch at most N companies per platform (Requirement 2.5).
    # The cap is applied after the slug filter and dead-slug skip so N bounds the
    # companies actually fetched.
    max_companies = getattr(ctx, "max_companies", None)
    if max_companies is not None and max_companies >= 0:
        if len(live_companies) > max_companies:
            print(
                f"  Capping to {max_companies} companies "
                f"(of {len(live_companies):,}) for scoped run"
            )
        live_companies = live_companies[:max_companies]

    if dead_slugs:
        print(f"  Skipping {len(dead_slugs):,} known dead slugs")
        print(f"  Checking {len(live_companies):,} potentially active companies\n")
    ctx.diag.record_skipped_dead(platform_lower, len(dead_slugs))

    all_jobs = []
    active_companies = {}
    failed = 0
    new_dead = set()
    not_fetched = 0

    MAX_WORKERS = {
        "bamboohr": 10,
        "greenhouse": 30,
        "ashby": 5,
        "lever": 30,
        "workday": 50,
        "icims": 30,
        "paylocity": 5,
    }

    max_workers = MAX_WORKERS.get(platform_lower, 30)

    budget_seconds = _platform_time_budget_seconds(ctx)
    start = time.monotonic()
    budget_hit = False

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(run_fetch, platform_lower, fetch_fn, slug, ctx): slug
            for slug in live_companies
        }

        for i, future in enumerate(as_completed(futures), 1):
            # Time budget: once exceeded, cancel futures that have not started
            # and count them as not fetched (Requirement 15.8).
            if (
                budget_seconds is not None
                and not budget_hit
                and (time.monotonic() - start) > budget_seconds
            ):
                budget_hit = True
                cancelled = 0
                for f in futures:
                    if f.cancel():
                        cancelled += 1
                not_fetched += cancelled
                if cancelled:
                    print(
                        f"  Time budget of {budget_seconds / 60:.0f} min exceeded for "
                        f"{platform}: not fetching {cancelled} remaining companies"
                    )

            slug = futures[future]
            if future.cancelled():
                continue

            jobs, outcome = future.result()
            ctx.diag.record_outcome(outcome)

            if jobs:
                all_jobs.extend(jobs)
                active_companies[slug] = len(jobs)
                print(f"  [{i}/{len(live_companies)}] {slug}: {len(jobs)} jobs")
            else:
                failed += 1
                # Cache a slug only when every host candidate was a permanent
                # 404/410 failure (Requirements 6.3, 6.4). Blocked, other
                # http_error, exception, and empty outcomes are never cached.
                if should_cache_dead(outcome):
                    new_dead.add(slug)
                if i % 50 == 0:
                    print(
                        f"  [{i}/{len(live_companies)}] Checked... ({failed} inactive)"
                    )

    elapsed = time.monotonic() - start
    ctx.diag.record_platform_time(platform_lower, elapsed, not_fetched)

    # Update the Dead_Slug_Cache only on a non-scoped run (Requirement 2.7).
    # Rewrite the file when there are newly dead slugs, or when the existing
    # file is a legacy plain list or a version mismatch that was ignored for
    # this run (Requirements 6.1, 6.2), so it is migrated to the new versioned
    # format.
    if not ctx.scoped and (new_dead or not dead_slug_file_is_current(platform_lower)):
        all_dead = dead_slugs | new_dead
        save_dead_slugs(platform_lower, all_dead)

    print(f"\nDETAILED STATS FOR {platform}:")
    print(f"  Companies checked: {len(live_companies)}")
    print(f"  Companies with jobs: {len(active_companies)}")
    print(f"  Failed/empty: {failed}")
    print(f"  Newly dead: {len(new_dead)}")
    if not_fetched:
        print(f"  Not fetched (time budget): {not_fetched}")
    print(f"  Elapsed: {elapsed:.1f}s")
    print(f"  Total jobs: {len(all_jobs)}")

    return active_companies, all_jobs


# ============================================================
# Helper Functions
# ============================================================
def is_recruiter_company(slug):
    slug = slug.lower()

    # Keyword-based detection
    if any(term in slug for term in RECRUITER_TERMS):
        return True

    return False


def clean_job_data(jobs):
    """Remove invalid/useless job entries."""
    cleaned = []
    skipped_reasons = {"no_title": 0, "no_url": 0, "no_company": 0}

    for job in jobs:
        title = (job.get("title") or "").strip().lower()
        url = job.get("url") or job.get("absolute_url")
        company = job.get("company") or job.get("company_slug")

        # Skip jobs with invalid titles
        if not title or title in ["not specified", "n/a", "unknown", ""]:
            skipped_reasons["no_title"] += 1
            continue

        # Skip jobs without URLs
        if not url:
            skipped_reasons["no_url"] += 1
            continue

        # Skip jobs without company info
        if not company:
            skipped_reasons["no_company"] += 1
            continue

        cleaned.append(job)

    # Print summary
    total_skipped = sum(skipped_reasons.values())
    if total_skipped > 0:
        print(f"\n  Skipped {total_skipped:,} invalid jobs:")
        for reason, count in skipped_reasons.items():
            if count > 0:
                print(f"    - {reason.replace('_', ' ').title()}: {count:,}")

    return cleaned


# module level, compiled once
_TIER_PATTERNS = [
    (re.compile(r"\b(?:chief|cto|ceo|cfo|vp|vice president|director)\b"), 50),
    (re.compile(r"\b(?:principal|distinguished|fellow)\b"), 40),
    (re.compile(r"\b(?:staff|lead|head of)\b"), 30),
    (re.compile(r"\b(?:senior|sr\.?)\b"), 20),
    (re.compile(r"\b(?:architect|manager)\b"), 15),
    (re.compile(r"\b(?:iii|iv|v|vi)\b"), 15),
    (re.compile(r"\blevel\s*[4-9]\b"), 15),
    (re.compile(r"\bengr?\s*[4-6]\b"), 15),
    (re.compile(r"\b(?:counsel|of\s*counsel)\b"), 20),
    (re.compile(r"\b(?:attending|charge)\b"), 20),
    (re.compile(r"\b(?:ii|2)\b"), 5),
    (re.compile(r"\blevel\s*3\b"), 5),
    (re.compile(r"\b(?:associate)\b"), -10),
    (re.compile(r"\b(?:junior|jr\.?)\b"), -20),
    (re.compile(r"\bentry[\s-]?level\b"), -25),
    (re.compile(r"\b(?:i|1)\b(?!\s*-|\d)"), -15),
    (re.compile(r"\b(?:trainee|graduate|new\s*grad)\b"), -25),
    (re.compile(r"\b(?:paralegal|clerk)\b"), -15),
    (re.compile(r"\b(?:resident|clinical\s*fellow)\b"), -15),
    (re.compile(r"\b(?:aide|assistant|tech)\b"), -10),
    (re.compile(r"\bintern(?:ship)?\b"), -100),
]


def job_tier_classification(title):
    title_lower = title.lower()
    score = 0
    for pattern, weight in _TIER_PATTERNS:
        if pattern.search(title_lower):
            score += weight
    if score <= -50:
        return "intern"
    elif score <= -5:
        return "entry"
    elif score >= 15:
        return "senior"
    else:
        return "mid"

## ---------------------------------------------------------
## ________  CUSTOM - TEMP  FUNCTIONS

def is_us_location(location):
    text = (location or "").strip()
    lower = text.lower()

    if any(pattern in lower for pattern in US_LOCATION_PATTERNS):
        return True

    m = re.search(r"(?:,\s*|\b)([A-Z]{2})(?:\b)", text)
    return bool(m and m.group(1) in US_STATE_ABBREVIATIONS)

def matches_title(title):
    if not TITLE_PATTERNS:
        return True
    return any(pattern.search(title or "") for pattern in TITLE_PATTERNS)


def matches_maybe_title(title):
    return any(pattern.search(title or "") for pattern in MAYBE_PATTERNS)


def keep_personal_job(job):
    company = (job.get("company") or job.get("company_slug") or "").lower()
    title = job.get("title") or ""
    location = job.get("location") or ""
    skill_level = job.get("skill_level") or job_tier_classification(title)

    if SENIOR_EXCLUDE_PATTERN.search(title):
        return (False, False)

    if skill_level == "senior":
        return (False, False)

    base_ok = (
        company not in COMPANY_BLOCKLIST
        and not job.get("is_recruiter", False)
        and is_us_location(location)
    )

    if not base_ok:
        return (False, False)

    is_primary = matches_title(title)
    is_maybe = (not is_primary) and matches_maybe_title(title)
    return is_primary, is_maybe

## ---------------------------------------------------------




# ============================================================
# DEAD SLUG CACHE
# ============================================================

DEAD_SLUG_DIR = os.path.join(ROOT_DIR, "data", "dead_slugs")
os.makedirs(DEAD_SLUG_DIR, exist_ok=True)

# The version of each Platform's host-resolution rules. A Dead_Slug_Cache file
# records the version that produced its entries (Requirement 6.1). When the
# stored version differs from the current version here -- or the file is a
# legacy plain list with no version -- its entries are ignored for the run and
# the file is rewritten in the new format at the end of a non-scoped run
# (Requirements 6.1, 6.2). Ashby is at version 2 because its endpoint changes;
# iCIMS and Workday are at 2 because their host/pagination rules are rewritten.
DEAD_SLUG_RULES_VERSION = {
    "icims": "2",
    "workday": "2",
    "greenhouse": "1",
    "lever": "1",
    "ashby": "2",
    "bamboohr": "1",
}


def dead_slug_version(platform):
    """Return the current Dead_Slug_Cache rules version for a platform.

    Platforms without an explicit entry (for example Paylocity, which is out of
    scope) default to ``"1"``.
    """
    return DEAD_SLUG_RULES_VERSION.get(platform.lower(), "1")


def should_cache_dead(outcome):
    """Return True only when a company is a permanent (404/410) failure.

    A slug enters the Dead_Slug_Cache exactly when every host candidate returned
    HTTP 404 or 410 (Requirements 6.3, 6.4). Any other outcome -- ``ok``,
    ``empty``, ``blocked``, ``exception``, ``invalid_slug``, or an
    ``http_error`` with a status other than 404/410 -- is never cached, so a
    transient block or server error cannot hide a company on later runs.

    The single ``FetchOutcome`` reflects the final host candidate tried; fetchers
    that try more than one host (iCIMS) report ``blocked`` or the surviving
    error status when any candidate was not a 404/410, so checking the recorded
    status and HTTP code captures the "every candidate" rule.
    """
    if outcome is None:
        return False
    return outcome.status == "http_error" and outcome.http_status in (404, 410)


def load_dead_slugs(platform):
    """Load cached dead slugs for a platform in the versioned file format.

    The file format is ``{"version": "<platform rules version>", "slugs":
    [...]}``. When the stored version differs from the current platform rules
    version, or the file is a legacy plain list (or otherwise unreadable), its
    entries are ignored for the run and an empty set is returned (Requirements
    6.1, 6.2).
    """
    filepath = os.path.join(DEAD_SLUG_DIR, f"{platform}.json")
    if not os.path.exists(filepath):
        return set()
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return set()

    # Legacy plain-list files have no version and are ignored (Requirement 6.2).
    if not isinstance(data, dict):
        return set()

    if str(data.get("version")) != dead_slug_version(platform):
        return set()

    return set(data.get("slugs") or [])


def dead_slug_file_is_current(platform):
    """Return True when the on-disk cache file is already in the current format.

    A file is "current" when it exists, is a dict, and its stored version
    matches the current platform rules version. A missing file, a legacy
    plain-list file, or a version mismatch is not current, so a non-scoped run
    rewrites it in the new format (Requirements 6.1, 6.2).
    """
    filepath = os.path.join(DEAD_SLUG_DIR, f"{platform}.json")
    if not os.path.exists(filepath):
        return False
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return False
    return isinstance(data, dict) and str(data.get("version")) == dead_slug_version(platform)


def save_dead_slugs(platform, slugs):
    """Save dead slugs for a platform in the versioned file format.

    Writes ``{"version": "<platform rules version>", "slugs": [...]}`` so a
    later run can tell whether the entries were produced by the current
    host-resolution rules (Requirement 6.1).
    """
    filepath = os.path.join(DEAD_SLUG_DIR, f"{platform}.json")
    payload = {"version": dead_slug_version(platform), "slugs": sorted(slugs)}
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"  Cached {len(slugs):,} dead slugs for {platform}")


# ============================================================
# SAVE RESULTS
# ============================================================
from collections import Counter


def _loc_adapter(verdict):
    """Bridge a ``LocationVerdict`` to the shape ``finalize_decision`` reads.

    ``apply_location_rule`` returns a :class:`location_filter.LocationVerdict`
    dataclass with attributes ``keep``, ``maybe_reason`` (``location_new_england``
    for a Secondary_Region match, ``remote_unspecified``/``location_unknown``
    otherwise, else None), ``exclusion_reason``, ``remote_scope``, and
    ``status``. ``filters.finalize_decision`` instead reads a tolerant accessor
    over the keys ``kept``, ``exclusion_reason``, ``secondary_region``,
    ``remote_scope``, and ``location_status``. This adapter maps one to the
    other so neither filters.py nor location_filter.py has to change:

      - ``kept``            <- ``verdict.keep``
      - ``exclusion_reason``<- ``verdict.exclusion_reason``
      - ``secondary_region``<- ``verdict.maybe_reason == "location_new_england"``
      - ``remote_scope``    <- ``verdict.remote_scope``
      - ``location_status`` <- ``verdict.status`` only when the verdict's
        maybe_reason is ``location_unknown`` (so an ambiguous/unknown place
        keeps a primary title as a maybe), else None.
    """
    return {
        "kept": verdict.keep,
        "exclusion_reason": verdict.exclusion_reason,
        "secondary_region": verdict.maybe_reason == "location_new_england",
        "remote_scope": verdict.remote_scope,
        "location_status": (
            verdict.status if verdict.maybe_reason == "location_unknown" else None
        ),
    }


def _stamp_location_fields(job, verdict):
    """Copy the Location_Filter verdict's place fields onto the Job_Record."""
    job["workplace_type"] = verdict.workplace_type
    job["remote_scope"] = verdict.remote_scope
    job["state"] = verdict.state
    job["distance_miles"] = verdict.distance_miles
    job["location_status"] = verdict.status
    # A remote posting is one whose resolved workplace type is "remote".
    job["remote"] = verdict.workplace_type == "remote"


def _stamp_decision_fields(job, decision):
    """Copy a Filter_Chain ``Decision`` onto the Job_Record."""
    job["match_type"] = decision.match_type
    job["match_reason"] = decision.match_reason
    job["exclusion_reason"] = decision.exclusion_reason
    job["category"] = decision.category
    job["title_priority"] = decision.title_priority


def save_results(all_companies, active_companies, all_jobs, ctx=None):
    """Run the new Filter_Chain pipeline and save all data to JSON files.

    Pipeline order (design Pipeline flow / Component 4-7):
      1. ``clean_job_data``.
      2. ``classify_pre_location`` per job: an excluded Decision is stamped,
         recorded, and the job skips the rest.
      3. List-data location classification for the surviving Candidate_Jobs
         (``classify_job_locations`` + ``apply_location_rule``) to produce an
         initial verdict.
      4. ``enrich_details`` for the candidates that ``needs_detail`` flags
         (missing description, multi-location, iCIMS, remote scope
         unspecified -- which also covers Research_Titles that need a
         description for the industry gate).
      5. Re-run ``classify_job_locations`` + ``apply_location_rule`` after
         enrichment (reclassify, Requirement 8.9).
      6. ``classify_post_detail``: PhD and industry/Category gate (may exclude
         ``phd_required`` / ``non_target_industry``).
      7. ``finalize_decision`` with ``assign_category`` -> Match_Type,
         Match_Reason, Category, Title_Priority.
      8. ``summarize_jd`` for kept jobs -> ``skills``, ``summary``, ``jd_backend``.
      9. ``sort_by_priority_and_date`` + ``apply_caps`` (primary/maybe caps),
         record cap truncation.

    Every decision is recorded in ``ctx.diag`` (Requirements 1.5, 1.7). ``ctx``
    is threaded in from ``main()``; it defaults to ``default_context()`` so
    existing callers and tests that omit it keep working.
    """
    if ctx is None:
        ctx = default_context()
    diag = ctx.diag
    cfg = ctx.config or personal_config

    print("=" * 80)
    print("SAVING RESULTS")
    print("=" * 80 + "\n")

    original_count = len(all_jobs)
    all_jobs = clean_job_data(all_jobs)

    ats_before = Counter(job.get("ats", "Unknown") for job in all_jobs)
    print(f"ATS before filtering: {dict(ats_before)}")

    # The Filter_Chain helpers (built once; pure and thread-safe to reuse).
    matcher = filters.build_title_matcher(cfg)
    recruiter = filters.build_recruiter_detector(cfg)
    maps = get_location_maps()

    # ---- Steps 2-3: pre-location filter + initial location verdict ----
    candidates = []            # jobs that survive classify_pre_location
    candidate_title_class = {}  # id(job) -> title classification string

    for job in all_jobs:
        decision = filters.classify_pre_location(job, cfg, matcher, recruiter)
        if decision is not None:
            # Excluded before the location check. Stamp, record, and skip.
            _stamp_decision_fields(job, decision)
            diag.record_decision(job)
            continue
        candidates.append(job)
        candidate_title_class[id(job)] = matcher.classify(job.get("title") or "")

    # Initial list-data location classification so we know which candidates are
    # already definitely rejected on location before spending detail fetches.
    for job in candidates:
        results = location_filter.classify_job_locations(job, maps)
        verdict = location_filter.apply_location_rule(
            results, secondary_region_states=cfg.SECONDARY_REGION_STATES
        )
        # Stash the remote_scope so needs_detail can see "unspecified" and
        # fetch the detail (Requirement 10.9).
        job["remote_scope"] = verdict.remote_scope

    # ---- Step 4: enrich details for candidates that need it ----
    # Only enrich candidates not already definitely rejected on location,
    # EXCEPT always enrich when needs_detail is True (iCIMS, multi-location,
    # remote scope unspecified, or missing description -- which also covers a
    # Research_Title that needs a description for the industry gate).
    to_enrich = [job for job in candidates if needs_detail(job)]
    if to_enrich:
        print(f"Enriching details for {len(to_enrich):,} candidate jobs...")
        enrich_details(to_enrich, ctx)

    # ---- Steps 5-8: reclassify, post-detail gate, finalize, summarize ----
    kept_jobs = []
    for job in candidates:
        title_class = candidate_title_class[id(job)]

        # Step 6: PhD + industry/Category gate (after enrichment).
        post = filters.classify_post_detail(job, title_class, cfg, matcher)
        if post is not None:
            _stamp_decision_fields(job, post)
            diag.record_decision(job)
            continue

        # Step 5 (reclassify, Requirement 8.9): re-run on the enriched data.
        results = location_filter.classify_job_locations(job, maps)
        verdict = location_filter.apply_location_rule(
            results, secondary_region_states=cfg.SECONDARY_REGION_STATES
        )
        _stamp_location_fields(job, verdict)

        # Step 7: category + final decision via the location adapter bridge.
        tier = job.get("skill_level") or job_tier_classification(job.get("title") or "")
        category = filters.assign_category(
            job.get("title") or "", title_class, job.get("description") or "", matcher, cfg
        )
        decision = filters.finalize_decision(
            job,
            title_class,
            tier,
            _loc_adapter(verdict),
            matcher=matcher,
            category=category,
        )
        _stamp_decision_fields(job, decision)

        if decision.match_type == "excluded":
            diag.record_decision(job)
            continue

        # Step 8: JD summary + skills for kept jobs.
        summary = summarize_jd(job.get("title") or "", job.get("description") or "")
        job["skills"] = summary.get("skills") or []
        job["summary"] = summary.get("summary") or ""
        job["jd_backend"] = summary.get("backend")

        kept_jobs.append(job)

    # ---- Step 9: sort + caps ----
    kept_jobs, truncated = filters.apply_caps(
        kept_jobs, primary_cap=MAX_PUBLISHED_JOBS, maybe_cap=MAX_MAYBE_JOBS
    )
    for bucket, count in truncated.items():
        if count:
            diag.record_cap_truncation(bucket, count)

    # apply_caps flips the dropped jobs to excluded/cap_truncated in place but
    # drops them from kept_jobs. Record those dropped decisions, then record
    # every surviving kept decision (primary/maybe) in diagnostics.
    kept_ids = {id(j) for j in kept_jobs}
    for job in candidates:
        if (
            id(job) not in kept_ids
            and job.get("exclusion_reason") == "cap_truncated"
        ):
            diag.record_decision(job)

    for job in kept_jobs:
        diag.record_decision(job)

    all_jobs = filters.sort_by_priority_and_date(kept_jobs)

    primary_count = sum(1 for j in all_jobs if j.get("match_type") == "primary")
    maybe_count = sum(1 for j in all_jobs if j.get("match_type") == "maybe")
    print(f"Primary matches: {primary_count:,}")
    print(f"Maybe matches: {maybe_count:,}")
    print(f"Cap truncated: primary={truncated['primary']:,}, maybe={truncated['maybe']:,}")

    cleaned_count = original_count - len(all_jobs)
    print(f"Removed/filtered {cleaned_count:,} jobs")

    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # Save all companies list
    companies_file = os.path.join(OUTPUT_DIR, "all_companies.json")
    with open(companies_file, "w", encoding="utf-8") as f:
        json.dump(sorted(list(all_companies)), f, ensure_ascii=False, indent=2)
    print(f"All companies: {companies_file}")

    # Save active companies with job counts
    active_file = os.path.join(OUTPUT_DIR, "active_companies.json")
    with open(active_file, "w", encoding="utf-8") as f:
        json.dump(active_companies, f, ensure_ascii=False, indent=2, sort_keys=True)
    print(f"Active companies: {active_file}")

    # Load salary lookup once
    salary_lookup_path = os.path.join(ROOT_DIR, "data", "salary", "salary_lookup.json")
    salary_lookup = {}
    salary_fallback = {}
    if os.path.exists(salary_lookup_path):
        with open(salary_lookup_path, encoding="utf-8") as f:
            data = json.load(f)
            salary_lookup = data.get("primary", {})
            salary_fallback = data.get("fallback", {})
        print(f"Loaded {len(salary_lookup):,} salary entries")

    # Enrich jobs with salary data
    for job in all_jobs:
        company = (job.get("company") or "").lower().strip()
        title = (job.get("title") or "").lower().strip()
        level = job.get("skill_level", "mid")

        primary_key = f"{company}|{title}|{level}"
        fallback_key = f"{title}|{level}"

        job["salary"] = salary_lookup.get(primary_key) or salary_fallback.get(
            fallback_key
        )

    # Save all jobs
    all_jobs_file = os.path.join(OUTPUT_DIR, "all_jobs.json")
    with open(all_jobs_file, "w", encoding="utf-8") as f:
        json.dump(all_jobs, f, ensure_ascii=False, indent=2)
    print(f"All jobs: {all_jobs_file} ({len(all_jobs):,} jobs)")

    # Build slim version for frontend. The full `description` is intentionally
    # NOT included here -- it lives only in all_jobs.json -- so the chunk
    # records stay small (Requirement 13.7 / design decision 7).
    FRONTEND_FIELDS = {
        "title",
        "company",
        "location",
        "url",
        "ats",
        "skill_level",
        "is_recruiter",
        "workplaceType",
        "scraped_at",
        "remote",
        "coords",
        "salary",
        "updated_at",
        "first_seen",
        # New Filter_Chain / enrichment fields (task 11.1).
        "skills",
        "summary",
        "workplace_type",
        "location_status",
        "match_type",
        "match_reason",
        "category",
        "title_priority",
        "distance_miles",
        "remote_scope",
        "published_at",
        "id",
        "ats_job_id",
    }

    slim_jobs = [
        {k: job.get(k) for k in FRONTEND_FIELDS if k in job} for job in all_jobs
    ]

    # Pre-sort by company name for better frontend caching
    slim_jobs.sort(
        key=lambda x: (x.get("company", "").lower(), x.get("title", "").lower())
    )

    # Chunks go in a subdirectory to keep the output folder organized
    chunks_dir = os.path.join(OUTPUT_DIR, "chunks")
    os.makedirs(chunks_dir, exist_ok=True)

    # Remove old chunk files to prevent confusion and save space
    for old_chunk in os.listdir(chunks_dir):
        if old_chunk.startswith("jobs_chunk_") and old_chunk.endswith(".json.gz"):
            os.remove(os.path.join(chunks_dir, old_chunk))

    # Split into chunks of ~25k for frontend loading (with gzip compression)
    CHUNK_SIZE = 25_000

    chunks = [
        slim_jobs[i : i + CHUNK_SIZE] for i in range(0, len(slim_jobs), CHUNK_SIZE)
    ]

    chunk_filenames = []
    for idx, chunk in enumerate(chunks):
        chunk_file = os.path.join(chunks_dir, f"jobs_chunk_{idx}.json.gz")
        with gzip.open(chunk_file, "wt", encoding="utf-8") as f:
            json.dump(chunk, f, ensure_ascii=False, indent=0)
        chunk_filenames.append(f"jobs_chunk_{idx}.json.gz")
        size_mb = os.path.getsize(chunk_file) / (1024 * 1024)
        print(f"  Chunk {idx}: {len(chunk):,} jobs ({size_mb:.1f}MB)")

    # Manifest so the frontend knows what to load
    manifest = {
        "chunks": chunk_filenames,
        "totalJobs": len(slim_jobs),
        "last_updated": timestamp,
    }
    manifest_file = os.path.join(chunks_dir, "jobs_manifest.json")
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    recruiter_jobs = sum(1 for job in all_jobs if job.get("is_recruiter"))

    # Save metadata summary
    metadata = {
        "last_updated": timestamp,
        "total_companies": len(all_companies),
        "active_companies": len(active_companies),
        "total_jobs": len(all_jobs),
        "recruiter_jobs": recruiter_jobs,
        "source_type": SOURCE_TYPE,
        "platforms": "greenhouse_api, ashby_api, bamboohr_api, lever_api, workday_api, icims_sitemap, paylocity_scrape",
    }

    metadata_file = os.path.join(OUTPUT_DIR, "metadata.json")
    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    print(f"Metadata: {metadata_file}")

    print()


DIAGNOSTICS_FILE = os.path.join(OUTPUT_DIR, "diagnostics.json")


def _finalize_diagnostics(diag, scoped):
    """Write ``scripts/output/diagnostics.json`` and print the summary table.

    Called at the end of every run -- scoped or not -- so the Diagnostics_Report
    and the per-Platform stdout table are always produced (Requirements 1.1,
    1.8). The ``scoped`` flag is forwarded so the report records whether the run
    was scoped.
    """
    diag.write(DIAGNOSTICS_FILE, scoped=scoped)
    print(f"Diagnostics report: {DIAGNOSTICS_FILE}")
    diag.print_summary(scoped=scoped)


def main(platforms_filter=None, max_companies=None, slugs=None):
    """Run the full scrape, optionally scoped to a subset of work.

    ``platforms_filter`` restricts which Platforms run (Requirement 2.4),
    ``max_companies`` caps companies per Platform (Requirement 2.5), and
    ``slugs`` fetches only the listed company slugs (Requirement 2.6). Any of
    these makes the run *scoped*, which leaves the Dead_Slug_Cache untouched
    (Requirement 2.7). Every run -- scoped or not -- writes
    ``scripts/output/diagnostics.json`` and prints the per-Platform summary
    table (Requirements 1.1, 1.8).
    """
    scoped = bool(platforms_filter or max_companies is not None or slugs)
    platforms_filter = {p.lower() for p in platforms_filter} if platforms_filter else None
    slugs = frozenset(slugs) if slugs else None

    print("\n" + "=" * 80)
    print("JOB BOARD AGGREGATOR")
    print("Scraping all jobs from ATS companies")
    if scoped:
        print("(scoped run: Dead_Slug_Cache will not be modified)")
        if platforms_filter:
            print(f"  platforms: {', '.join(sorted(platforms_filter))}")
        if max_companies is not None:
            print(f"  max companies per platform: {max_companies}")
        if slugs:
            print(f"  slugs: {', '.join(sorted(slugs))}")
    print("=" * 80)

    # # Load existing companies
    # greenhouse_companies = load_companies(GREENHOUSE_FILE)
    # ashby_companies = load_companies(ASHBY_FILE)
    # bamboohr_companies = load_companies(BAMBOOHR_FILE)
    # lever_companies = load_companies(LEVER_FILE)
    # workday_companies = load_companies(WORKDAY_FILE)
    # icims_companies = load_companies(ICIMS_FILE)
    # paylocity_companies = load_paylocity(PAYLOCITY_FILE)
    # if (
    #         not greenhouse_companies
    #         and not ashby_companies
    #         and not bamboohr_companies
    #         and not lever_companies
    #         and not workday_companies
    #         and not icims_companies
    #         and not paylocity_companies
    #     ):
    #         print("Exiting - no companies loaded!")
    #         return



    ####------------------------------------------------------------------
    # A platform runs when it is enabled and, for a scoped run, also listed in
    # --platforms (Requirement 2.4). The helper keeps the load conditions short.
    def _platform_active(name):
        return name in ENABLED_PLATFORMS and (
            platforms_filter is None or name in platforms_filter
        )

    greenhouse_companies =      (load_companies(GREENHOUSE_FILE) if _platform_active("greenhouse") else set())
    lever_companies =           (load_companies(LEVER_FILE)      if _platform_active("lever") else set())
    ashby_companies =           (load_companies(ASHBY_FILE)      if _platform_active("ashby") else set())
    workday_companies =         (load_companies(WORKDAY_FILE)    if _platform_active("workday") else set())
    icims_companies =           (load_companies(ICIMS_FILE)      if _platform_active("icims") else set())
    bamboohr_companies =        (load_companies(BAMBOOHR_FILE)   if _platform_active("bamboohr") else set())

    paylocity_companies = (load_paylocity(PAYLOCITY_FILE) if _platform_active("paylocity") else set())




    if (
            not greenhouse_companies
            and not ashby_companies
            and not bamboohr_companies
            and not lever_companies
            and not workday_companies
            and not icims_companies
            and not paylocity_companies
        ):
            print("Exiting - no companies in enabled list loaded!")
            # Still emit an (empty) Diagnostics_Report and summary so every run
            # produces the same artifacts (Requirements 1.1, 1.8).
            empty_diag = DiagnosticsCollector(
                watchlist=getattr(personal_config, "WATCHLIST", set()) or set()
            )
            _finalize_diagnostics(empty_diag, scoped)
            return
    ####------------------------------------------------------------------

    # # Define all platform jobs
    # platforms = [
    #     (greenhouse_companies, fetch_company_jobs_greenhouse, "GREENHOUSE"),
    #     (ashby_companies, fetch_company_jobs_ashby, "ASHBY"),
    #     (bamboohr_companies, fetch_company_jobs_bamboohr, "BAMBOOHR"),
    #     (lever_companies, fetch_company_jobs_lever, "LEVER"),
    #     (workday_companies, fetch_company_jobs_workday, "WORKDAY"),
    #     (icims_companies, fetch_company_jobs_icims, "iCIMS"),
    #     (paylocity_companies, fetch_company_jobs_paylocity, "PAYLOCITY"),
    # ]



    platforms = []
    if greenhouse_companies:
        platforms.append((greenhouse_companies, _fetch_greenhouse, "GREENHOUSE"))
    if lever_companies:
        platforms.append((lever_companies, _fetch_lever, "LEVER"))
    if ashby_companies:
        platforms.append((ashby_companies, _fetch_ashby, "ASHBY"))
    if workday_companies:
        platforms.append((workday_companies, _fetch_workday, "WORKDAY"))
    if icims_companies:
        platforms.append((icims_companies, _fetch_icims, "iCIMS"))
    if paylocity_companies:
        platforms.append((paylocity_companies, _fetch_paylocity, "PAYLOCITY"))
    if bamboohr_companies:
        platforms.append((bamboohr_companies, _fetch_bamboohr, "BAMBOOHR"))

    # One DiagnosticsCollector per run, threaded through a RunContext. The
    # scoped-run CLI flags (--platforms/--slugs/--max-companies) set ``scoped``
    # and carry the per-platform filters that ``fetch_all_jobs`` applies
    # (Requirements 2.4, 2.5, 2.6, 2.7).
    watchlist = getattr(personal_config, "WATCHLIST", set()) or set()
    ctx = RunContext(
        diag=DiagnosticsCollector(watchlist=watchlist),
        config=personal_config,
        scoped=scoped,
        max_companies=max_companies,
        slugs=slugs,
    )

    # Run all platforms concurrently
    all_active_companies = {}
    all_jobs = []

    with ThreadPoolExecutor(max_workers=len(platforms)) as platform_executor:
        futures = {
            platform_executor.submit(fetch_all_jobs, companies, fetcher, name, ctx): name
            for companies, fetcher, name in platforms
        }

        for future in as_completed(futures):
            name = futures[future]
            active, jobs = future.result()
            all_active_companies.update(active)
            all_jobs.extend(jobs)
            print(
                f"\n  >>> {name} COMPLETE: {len(active):,} active, {len(jobs):,} jobs <<<\n"
            )

    # Combine all company sets for total count
    # all_companies = (
    #     greenhouse_companies
    #     | ashby_companies
    #     | bamboohr_companies
    #     | lever_companies
    #     | workday_companies
    #     | icims_companies
    #     | paylocity_companies
    # )

    all_companies = (
            set(greenhouse_companies)
            | set(ashby_companies)
            | set(bamboohr_companies)
            | set(lever_companies)
            | set(workday_companies)
            | set(icims_companies)
            | set(paylocity_companies)
        )   

    save_results(all_companies, all_active_companies, all_jobs, ctx)

    # Write the Diagnostics_Report and print the per-Platform summary table at
    # the end of every run (Requirements 1.1, 1.8).
    _finalize_diagnostics(ctx.diag, scoped)

    # Final summary
    print("=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)
    print(f"Total companies:   {len(all_companies):,}")
    print(f"Active companies:  {len(all_active_companies):,}")
    print(f"Total jobs:        {len(all_jobs):,}")
    print(f"\nAll data saved to '{OUTPUT_DIR}/' directory")
    print("=" * 80 + "\n")


def _parse_args(argv=None):
    """Parse the Scraper CLI, including the scoped-run flags.

    ``--platforms``, ``--max-companies``, and ``--slugs`` each scope a run and,
    when any is given, leave the Dead_Slug_Cache untouched (Requirements 2.4,
    2.5, 2.6, 2.7). ``--source`` keeps its existing behavior.
    """
    parser = argparse.ArgumentParser(description="Job Board Aggregator Scraper")
    parser.add_argument(
        "--source",
        choices=["automated", "manual"],
        default="automated",
        help="Source type: automated (GitHub Actions) or manual (local run)",
    )
    parser.add_argument(
        "--platforms",
        default=None,
        help=(
            "Comma-separated subset of enabled platforms to run "
            f"(choose from: {', '.join(sorted(ENABLED_PLATFORMS))}). "
            "Scopes the run and skips Dead_Slug_Cache writes."
        ),
    )
    parser.add_argument(
        "--max-companies",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Fetch at most N companies per platform. "
            "Scopes the run and skips Dead_Slug_Cache writes."
        ),
    )
    parser.add_argument(
        "--slugs",
        nargs="+",
        default=None,
        metavar="SLUG",
        help=(
            "One or more explicit company slugs to fetch. Each slug is routed "
            "to the platform whose Company_List contains it. Scopes the run and "
            "skips Dead_Slug_Cache writes."
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    SOURCE_TYPE = args.source

    platforms_filter = None
    if args.platforms:
        platforms_filter = [
            p.strip().lower() for p in args.platforms.split(",") if p.strip()
        ]

    print(f"\nRunning in {SOURCE_TYPE.upper()} mode\n")

    main(
        platforms_filter=platforms_filter,
        max_companies=args.max_companies,
        slugs=args.slugs,
    )
