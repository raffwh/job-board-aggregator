# detail_enricher.py
"""
Per-platform detail enrichment for Candidate_Jobs.

This module provides ``html_to_text`` (HTML/entity-escaped HTML to collapsed
plain text), the per-platform detail fetchers (Workday, iCIMS, Greenhouse,
BambooHR), the ``needs_detail`` predicate, and ``enrich_details`` -- the
bounded-concurrency orchestration that fetches descriptions and better location
data for Candidate_Jobs.

Validates: Requirements 4.6, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 10.9
"""

from __future__ import annotations

import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from typing import Callable, Dict, List, Optional

try:  # requests is available at runtime; tests inject a FakeSession instead.
    import requests
except Exception:  # pragma: no cover - requests is a hard runtime dependency
    requests = None  # type: ignore

# ---- Constants ----

# Block-level tags that should produce a line break in the plain-text output.
_BLOCK_TAGS = frozenset(
    {"p", "br", "li", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}
)

# Tags whose text content is dropped entirely.
_DROP_TAGS = frozenset({"script", "style"})

# Maximum length of the returned description, to bound memory and artifact size.
_MAX_LEN = 20000

# Whitespace collapsing patterns.
_SPACES_RE = re.compile(r"[ \t]+")
_NEWLINES_RE = re.compile(r"\n{3,}")

# Residual-markup neutralization patterns. Inserting a space after these
# prevents the output from being re-parsed as markup, making html_to_text a
# fixed point (idempotent).
_RESIDUAL_LT_RE = re.compile(r"<(?=[A-Za-z/!?])")
_RESIDUAL_AMP_RE = re.compile(r"&(?=#?[A-Za-z0-9]+;)")


class _TextExtractor(HTMLParser):
    """Collects text content, emitting newlines for block tags and dropping
    the content of script/style tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._drop_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _DROP_TAGS:
            self._drop_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        # Self-closing tags, e.g. <br/>.
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_TAGS:
            if self._drop_depth > 0:
                self._drop_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._drop_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def html_to_text(s: str | None) -> str:
    """Convert an HTML (or entity-escaped HTML) string to collapsed plain text.

    The result contains no HTML tags, has entities decoded and whitespace
    collapsed, and is a fixed point: ``html_to_text(html_to_text(x)) ==
    html_to_text(x)``.

    Validates: Requirements 10.4, 10.8
    """
    if not s:
        return ""

    # 1. Greenhouse case: entity-escaped HTML with no real tags. Unescape once
    #    so the markup can be parsed below.
    if "&lt;" in s and "<" not in s:
        s = html.unescape(s)

    # 2. Extract text, emitting newlines for block tags, dropping script/style.
    parser = _TextExtractor()
    parser.feed(s)
    parser.close()
    text = parser.get_text()

    # 3. Collapse whitespace.
    text = _SPACES_RE.sub(" ", text)
    text = _NEWLINES_RE.sub("\n\n", text)
    # Trim spaces/tabs around newlines left over from collapsing.
    text = re.sub(r" *\n *", "\n", text)
    text = text.strip()

    # 4. Neutralize residual markup so the output cannot be re-parsed as markup.
    text = _RESIDUAL_LT_RE.sub("< ", text)
    text = _RESIDUAL_AMP_RE.sub("& ", text)

    # 5. Cap length.
    return text[:_MAX_LEN]

# ============================================================
# Detail enrichment: per-platform fetchers and orchestration
# ============================================================

# DetailResult is a plain dict with these keys. Fetchers fill what the platform
# provides and omit the rest; ``enrich_details`` applies only the present keys.
#
#   description:      str              - plain text (HTML converted)
#   title:            str | None       - overrides the Job_Record title (iCIMS)
#   locations:        list[str]        - primary + additional locations
#   native_workplace: str | None       - remote | hybrid | onsite
#   native_country:   str | None
#   req_id:           str | None       - Workday jobReqId
#   published_at:     str | None
DetailResult = Dict[str, object]

# Default per-run network settings. ``enrich_details`` reads the worker and
# per-run caps from ``ctx.config`` (personal_config) when present.
_DEFAULT_MAX_WORKERS = 8
_DETAIL_TIMEOUT = 30

# Workday remoteType -> Workplace_Type.
_WORKDAY_REMOTE_MAP = {
    "Remote": "remote",
    "Hybrid": "hybrid",
    "On-site": "onsite",
}

# JSON-LD placeholder iCIMS uses when an address field is unknown.
_ICIMS_UNAVAILABLE = "UNAVAILABLE"


def _config_value(ctx, name, default):
    """Read a knob off ``ctx.config`` (the personal_config module), with a default."""
    config = getattr(ctx, "config", None)
    if config is None:
        return default
    value = getattr(config, name, default)
    return default if value is None and default is not None else value


# ------------------------------------------------------------------
# Workday
# ------------------------------------------------------------------


def _workday_detail_url(job: dict) -> Optional[str]:
    """Build ``{base}/wday/cxs/{tenant}/{site}{externalPath}`` for a Workday job.

    The Job_Record carries ``company_slug`` in the ``company|wd#|site`` form and
    ``workday_external_path`` from the list response (design Component 6).
    """
    external_path = job.get("workday_external_path")
    slug = job.get("company_slug") or ""
    parts = str(slug).split("|")
    if not external_path or len(parts) != 3:
        return None
    tenant, wd, site = parts
    wd_num = wd.replace("wd", "")
    base = f"https://{tenant}.wd{wd_num}.myworkdayjobs.com"
    return f"{base}/wday/cxs/{tenant}/{site}{external_path}"


def _detail_workday(job: dict, session) -> DetailResult:
    """GET the Workday CXS detail endpoint and read description, req id, and
    location data (Requirement 10.2)."""
    url = _workday_detail_url(job)
    if not url:
        return {}
    resp = session.get(url, timeout=_DETAIL_TIMEOUT)
    resp.raise_for_status()
    data = resp.json() or {}
    posting = data.get("jobPostingInfo") or {}

    locations: List[str] = []
    primary = posting.get("location")
    if primary:
        locations.append(primary)
    for extra in posting.get("additionalLocations") or []:
        if extra:
            locations.append(extra)

    result: DetailResult = {
        "description": html_to_text(posting.get("jobDescription")),
        "locations": locations,
    }

    req_id = posting.get("jobReqId")
    if req_id:
        result["req_id"] = req_id

    country = posting.get("country") or {}
    descriptor = country.get("descriptor") if isinstance(country, dict) else None
    if descriptor:
        result["native_country"] = descriptor

    remote_type = posting.get("remoteType")
    if remote_type:
        result["native_workplace"] = _WORKDAY_REMOTE_MAP.get(remote_type)

    return result


# ------------------------------------------------------------------
# iCIMS
# ------------------------------------------------------------------

# Capture each <script type="application/ld+json"> ... </script> block.
_JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def _iter_jsonld(text: str):
    """Yield parsed JSON-LD objects from the embedded script blocks."""
    for match in _JSONLD_RE.finditer(text or ""):
        raw = match.group(1).strip()
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            continue
        # A block may be a single object, a list, or a @graph container.
        if isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    yield item
        elif isinstance(obj, dict):
            graph = obj.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    if isinstance(item, dict):
                        yield item
            else:
                yield obj


def _find_job_posting(text: str) -> Optional[dict]:
    """Return the first JSON-LD object whose @type is JobPosting."""
    for obj in _iter_jsonld(text):
        typ = obj.get("@type")
        types = typ if isinstance(typ, list) else [typ]
        if any(str(t).lower() == "jobposting" for t in types):
            return obj
    return None


def _icims_address_string(job_location) -> str:
    """Join addressLocality/Region/Country, dropping ``UNAVAILABLE`` placeholders."""
    if isinstance(job_location, list):
        locations = job_location
    elif job_location:
        locations = [job_location]
    else:
        locations = []

    parts: List[str] = []
    for loc in locations:
        if not isinstance(loc, dict):
            continue
        address = loc.get("address")
        if not isinstance(address, dict):
            continue
        for key in ("addressLocality", "addressRegion", "addressCountry"):
            value = address.get(key)
            if isinstance(value, dict):
                value = value.get("name")
            if value and str(value).strip().upper() != _ICIMS_UNAVAILABLE:
                parts.append(str(value).strip())
    return ", ".join(parts)


def _detail_icims(job: dict, session) -> DetailResult:
    """GET the iCIMS posting page with ``?in_iframe=1`` and parse the JSON-LD
    JobPosting (Requirement 4.6)."""
    base_url = job.get("url")
    if not base_url:
        return {}
    joiner = "&" if "?" in base_url else "?"
    url = f"{base_url}{joiner}in_iframe=1"
    resp = session.get(url, timeout=_DETAIL_TIMEOUT)
    resp.raise_for_status()

    posting = _find_job_posting(resp.text)
    if not posting:
        return {}

    result: DetailResult = {
        "description": html_to_text(posting.get("description")),
    }

    # JSON-LD title overrides the URL-derived title.
    title = posting.get("title")
    if title:
        result["title"] = str(title).strip()

    location = _icims_address_string(posting.get("jobLocation"))
    if location:
        result["locations"] = [location]

    if str(posting.get("jobLocationType") or "").upper() == "TELECOMMUTE":
        result["native_workplace"] = "remote"

    date_posted = posting.get("datePosted")
    if date_posted:
        result["published_at"] = date_posted

    return result


# ------------------------------------------------------------------
# Greenhouse
# ------------------------------------------------------------------


def _detail_greenhouse(job: dict, session) -> DetailResult:
    """GET the Greenhouse per-job endpoint and convert the entity-escaped HTML
    ``content`` to plain text (Requirement 10.3)."""
    slug = job.get("company_slug") or job.get("company")
    job_id = job.get("ats_job_id") or job.get("id")
    if not slug or not job_id:
        return {}
    url = (
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
        "?content=true"
    )
    resp = session.get(url, timeout=_DETAIL_TIMEOUT)
    resp.raise_for_status()
    data = resp.json() or {}
    # ``content`` is entity-escaped HTML; html_to_text unescapes once and
    # converts it.
    return {"description": html_to_text(data.get("content"))}


# ------------------------------------------------------------------
# BambooHR
# ------------------------------------------------------------------


def _detail_bamboohr(job: dict, session) -> DetailResult:
    """GET the BambooHR careers detail endpoint and read the opening's
    description and posted date (Requirement 10.3)."""
    slug = job.get("company_slug") or job.get("company")
    job_id = job.get("ats_job_id") or job.get("id")
    if not slug or not job_id:
        return {}
    url = f"https://{slug}.bamboohr.com/careers/{job_id}/detail"
    resp = session.get(url, timeout=_DETAIL_TIMEOUT)
    resp.raise_for_status()
    data = resp.json() or {}
    opening = data.get("result", {}).get("jobOpening", {}) or {}

    result: DetailResult = {
        "description": html_to_text(opening.get("description")),
    }
    date_posted = opening.get("datePosted")
    if date_posted:
        result["published_at"] = date_posted
    return result


# Platform -> detail fetcher. The key matches the Job_Record ``ats`` field.
DETAIL_FETCHERS: Dict[str, Callable[[dict, object], DetailResult]] = {
    "Workday": _detail_workday,
    "iCIMS": _detail_icims,
    "Greenhouse": _detail_greenhouse,
    "BambooHR": _detail_bamboohr,
}


# ------------------------------------------------------------------
# needs_detail
# ------------------------------------------------------------------


def needs_detail(job: dict) -> bool:
    """Decide whether a Candidate_Job needs a detail fetch (Requirement 10.9).

    A fetch is needed when the list response carried no description, when the
    posting spans multiple locations (``location_multi``), when it is an iCIMS
    job (whose list data has no location), or when its Remote_Scope is
    ``unspecified`` (so a US country in the detail data can promote it).
    """
    if not (job.get("description") or "").strip():
        return True
    if job.get("location_multi"):
        return True
    if job.get("ats") == "iCIMS":
        return True
    if job.get("remote_scope") == "unspecified":
        return True
    return False


# ------------------------------------------------------------------
# enrich_details
# ------------------------------------------------------------------


def _apply_detail(job: dict, result: DetailResult) -> None:
    """Merge a DetailResult onto a Job_Record, applying only present keys."""
    if not result:
        return
    description = result.get("description")
    if description:
        job["description"] = description
    title = result.get("title")
    if title:
        job["title"] = title
    locations = result.get("locations")
    if locations:
        job["locations"] = list(locations)
        job["location"] = ", ".join(locations)
        job["location_multi"] = len(locations) > 1
    for key in ("native_workplace", "native_country", "req_id", "published_at"):
        value = result.get(key)
        if value:
            job[key] = value


def _fetch_with_retries(fetcher, job, session, retries: int = 2):
    """Call ``fetcher`` with up to ``retries`` additional attempts and backoff.

    Returns the DetailResult on success. Raises the last exception if every
    attempt fails, so the caller can record a failure (Requirement 10.5).
    """
    attempt = 0
    last_exc: Optional[Exception] = None
    while attempt <= retries:
        try:
            return fetcher(job, session)
        except Exception as exc:  # network error, HTTP error, or parse error
            last_exc = exc
            attempt += 1
            if attempt > retries:
                break
            # Exponential backoff: 0.5s, 1.0s.
            time.sleep(0.5 * attempt)
    assert last_exc is not None
    raise last_exc


def _new_session():
    """Build a requests.Session for a run, or ``None`` if requests is missing."""
    if requests is None:  # pragma: no cover - requests present at runtime
        return None
    return requests.Session()


def enrich_details(candidates: List[dict], ctx, session=None) -> None:
    """Fetch and apply detail data for the Candidate_Jobs that need it.

    Jobs are grouped by ``ats`` and each platform is processed with its own
    ``ThreadPoolExecutor`` bounded by ``DETAIL_MAX_WORKERS`` (Requirement 10.6).
    At most ``DETAIL_MAX_PER_RUN`` jobs per platform are fetched; the rest are
    counted as skipped (Requirement 10.7). Each fetch is retried twice with
    backoff; a job whose fetch ultimately fails is kept without a description,
    marked with ``detail_failed``, and counted per platform (Requirement 10.5,
    10.6). Failures and skips are recorded in ``ctx.diag``.

    ``session`` is injectable so tests can pass a FakeSession; one is created
    per call otherwise and closed at the end.
    """
    diag = getattr(ctx, "diag", None)
    max_workers = int(_config_value(ctx, "DETAIL_MAX_WORKERS", _DEFAULT_MAX_WORKERS))
    if max_workers < 1:
        max_workers = 1
    max_per_run = _config_value(ctx, "DETAIL_MAX_PER_RUN", None)

    owns_session = session is None
    if owns_session:
        session = _new_session()

    # Group the jobs that need a detail fetch and that have a known fetcher.
    by_platform: Dict[str, List[dict]] = {}
    for job in candidates:
        platform = job.get("ats")
        if platform not in DETAIL_FETCHERS:
            continue
        if not needs_detail(job):
            continue
        by_platform.setdefault(platform, []).append(job)

    try:
        for platform, jobs in by_platform.items():
            fetcher = DETAIL_FETCHERS[platform]

            selected = jobs
            if max_per_run is not None:
                limit = int(max_per_run)
                if len(jobs) > limit:
                    selected = jobs[:limit]
                    skipped = len(jobs) - limit
                    if diag is not None:
                        diag.record_detail_skipped(platform, skipped)

            if not selected:
                continue
            if diag is not None:
                diag.record_detail_requested(platform, len(selected))

            workers = min(max_workers, len(selected))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(
                        _fetch_with_retries, fetcher, job, session
                    ): job
                    for job in selected
                }
                for future in as_completed(futures):
                    job = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        # Keep the job without a description (Requirement 10.6).
                        job["detail_failed"] = True
                        if diag is not None:
                            slug = job.get("company_slug") or job.get("company") or ""
                            diag.record_detail_failure(platform, slug, str(exc))
                        continue
                    _apply_detail(job, result)
    finally:
        if owns_session and session is not None and hasattr(session, "close"):
            try:
                session.close()
            except Exception:  # pragma: no cover - defensive
                pass
