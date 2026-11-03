"""Capture trimmed, read-only ATS responses for the offline test suite.

This script performs read-only GET/POST calls against public ATS endpoints and
saves small, trimmed responses under ``scripts/tests/fixtures/<platform>/`` so
the offline unit suite (``pytest -m "not network"``) can run without network.

Live network calls are expected and allowed here: this is a capture tool, run
by hand during implementation, not part of the offline suite. Each capture is
wrapped so that an unreachable endpoint falls back to a representative,
hand-built fixture that matches the documented shape in ``design.md`` (see the
"Research summary" table). The README in this directory records which fixtures
were captured live and which are hand-built.

Usage::

    python scripts/tests/fixtures/capture_fixtures.py          # capture everything
    python scripts/tests/fixtures/capture_fixtures.py workday  # one platform
    python scripts/tests/fixtures/capture_fixtures.py --offline # fallbacks only

All responses are trimmed to a handful of postings to keep the fixtures small
and the repo diff reviewable. The captured JSON is tracked despite the global
``*.json`` ignore because ``.gitignore`` whitelists
``!scripts/tests/fixtures/**/*.json`` (Requirement 16.4).
"""

from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET

try:
    import requests
except ImportError:  # pragma: no cover - requests ships with the repo
    requests = None


FIXTURE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(FIXTURE_DIR, "..", "..", ".."))
LOCATIONS_JSON = os.path.join(REPO_ROOT, "data", "locations.json")

TIMEOUT = 20
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, application/xml;q=0.9,*/*;q=0.8",
}

# Records which fixtures were captured live vs. hand-built this run.
PROVENANCE: dict[str, str] = {}


# ============================================================
# Helpers
# ============================================================

def _platform_dir(platform: str) -> str:
    path = os.path.join(FIXTURE_DIR, platform)
    os.makedirs(path, exist_ok=True)
    return path


def save_json(platform: str, name: str, data) -> str:
    """Write ``data`` as pretty JSON under the platform's fixture folder."""
    path = os.path.join(_platform_dir(platform), name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    rel = os.path.relpath(path, REPO_ROOT)
    print(f"  wrote {rel}")
    return path


def save_text(platform: str, name: str, text: str) -> str:
    path = os.path.join(_platform_dir(platform), name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    rel = os.path.relpath(path, REPO_ROOT)
    print(f"  wrote {rel}")
    return path


def _get(url, offline, **kwargs):
    if offline or requests is None:
        return None
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kwargs)
        if resp.status_code == 200:
            return resp
        print(f"  GET {url} -> HTTP {resp.status_code}; using fallback")
    except Exception as exc:  # noqa: BLE001 - capture tool, any failure -> fallback
        print(f"  GET {url} failed ({type(exc).__name__}); using fallback")
    return None


def _post(url, offline, **kwargs):
    if offline or requests is None:
        return None
    try:
        resp = requests.post(url, headers=HEADERS, timeout=TIMEOUT, **kwargs)
        if resp.status_code == 200:
            return resp
        print(f"  POST {url} -> HTTP {resp.status_code}; using fallback")
    except Exception as exc:  # noqa: BLE001
        print(f"  POST {url} failed ({type(exc).__name__}); using fallback")
    return None


def _mark(name: str, live: bool) -> None:
    PROVENANCE[name] = "live" if live else "hand-built"


# ============================================================
# Workday  (tenant iqvia, wd1, site iqvia)
# ============================================================

WD_TENANT = "iqvia"
WD_SITE = "iqvia"
WD_BASE = f"https://{WD_TENANT}.wd1.myworkdayjobs.com/wday/cxs/{WD_TENANT}/{WD_SITE}"


def _fallback_workday_list(offset: int, total: int):
    """A trimmed Workday CXS list page matching the documented shape."""
    postings = []
    for i in range(offset, min(offset + 20, total)):
        req = f"R15{60000 + i}"
        multi = i % 7 == 0  # a few multi-location postings for the "N Locations" path
        postings.append({
            "title": [
                "Data Scientist", "Senior Data Analyst", "Biostatistician",
                "Epidemiologist", "RWE Data Scientist", "Statistical Programmer",
            ][i % 6] + (f" {i}" if i else ""),
            "externalPath": f"/job/Boston-MA/Data-Scientist_{req}"
            if not multi
            else f"/job/Sao-Paulo-Brazil/Data-Scientist_{req}",
            "locationsText": "3 Locations" if multi else "Boston, MA",
            "postedOn": "Posted 2 Days Ago",
            "bulletFields": [req],
        })
    return {"total": total if offset == 0 else 0, "jobPostings": postings}


def _fallback_workday_detail():
    return {
        "jobPostingInfo": {
            "title": "Data Scientist",
            "jobReqId": "R1560000",
            "jobDescription": (
                "<p>IQVIA is seeking a <b>Data Scientist</b> to join our "
                "Real-World Evidence team.</p><ul><li>Analyze claims data "
                "(MarketScan)</li><li>Build predictive models in Python</li>"
                "</ul><p>A Master's degree is required; a PhD is preferred.</p>"
            ),
            "location": "Boston, MA",
            "additionalLocations": ["Cambridge, MA", "Durham, NC"],
            "country": {"descriptor": "United States of America"},
            "remoteType": None,
            "timeType": "Full time",
        }
    }


def capture_workday(offline: bool) -> None:
    print("Workday (iqvia|wd1|iqvia)")
    url = f"{WD_BASE}/jobs"

    # Page 1
    resp = _post(url, offline, json={"appliedFacets": {}, "limit": 20, "offset": 0,
                                     "searchText": "data scientist"})
    if resp is not None:
        data = resp.json()
        data["jobPostings"] = data.get("jobPostings", [])[:20]
        save_json("workday", "list_page1.json", data)
        _mark("workday/list_page1.json", True)
        total = data.get("total") or 400
    else:
        total = 400
        save_json("workday", "list_page1.json", _fallback_workday_list(0, total))
        _mark("workday/list_page1.json", False)

    # Page 2 (demonstrates total:0 on later pages)
    resp = _post(url, offline, json={"appliedFacets": {}, "limit": 20, "offset": 20,
                                     "searchText": "data scientist"})
    if resp is not None:
        data = resp.json()
        data["jobPostings"] = data.get("jobPostings", [])[:20]
        save_json("workday", "list_page2.json", data)
        _mark("workday/list_page2.json", True)
    else:
        save_json("workday", "list_page2.json", _fallback_workday_list(20, total))
        _mark("workday/list_page2.json", False)

    # One detail
    detail_path = None
    try:
        p1 = json.load(open(os.path.join(FIXTURE_DIR, "workday", "list_page1.json"),
                            encoding="utf-8"))
        postings = p1.get("jobPostings") or []
        if postings:
            detail_path = postings[0].get("externalPath")
    except Exception:  # noqa: BLE001
        detail_path = None

    resp = _get(f"{WD_BASE}{detail_path}", offline) if detail_path else None
    if resp is not None:
        save_json("workday", "detail.json", resp.json())
        _mark("workday/detail.json", True)
    else:
        save_json("workday", "detail.json", _fallback_workday_detail())
        _mark("workday/detail.json", False)


# ============================================================
# iCIMS  (sitemap + one job page for two tenants)
# ============================================================

def _fallback_icims_sitemap(host: str):
    urls = [
        f"https://{host}/jobs/9620/data-analyst/job",
        f"https://{host}/jobs/9731/senior-biostatistician/job",
        f"https://{host}/jobs/9845/research-scientist/job",
    ]
    ns = "http://www.sitemaps.org/schemas/sitemap/0.9"
    lines = [f'<?xml version="1.0" encoding="UTF-8"?>',
             f'<urlset xmlns="{ns}">']
    for u in urls:
        lines.append(f"  <url><loc>{u}</loc></url>")
    lines.append("</urlset>")
    return "\n".join(lines)


def _fallback_icims_jobpage(title: str, city: str, region: str):
    """An iCIMS job page body with a JSON-LD JobPosting block."""
    jsonld = {
        "@context": "https://schema.org/",
        "@type": "JobPosting",
        "title": title,
        "description": (
            "<p>We are hiring a " + title + ". You will analyze healthcare "
            "claims data and support clinical research.</p>"
        ),
        "datePosted": "2024-05-01",
        "jobLocation": [{
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": city,
                "addressRegion": region,
                "addressCountry": "US",
            },
        }, {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": "UNAVAILABLE",
                "addressRegion": "UNAVAILABLE",
                "addressCountry": "UNAVAILABLE",
            },
        }],
        "jobLocationType": None,
    }
    return (
        "<!doctype html><html><head><title>" + title + "</title>"
        '<script type="application/ld+json">'
        + json.dumps(jsonld)
        + "</script></head><body><h1>" + title + "</h1></body></html>"
    )


def capture_icims(offline: bool) -> None:
    print("iCIMS (theharrispoll, careers-gbrx)")
    tenants = {
        # slug -> resolved host (theharrispoll is bare; careers-gbrx carries a prefix)
        "theharrispoll": "careers-theharrispoll.icims.com",
        "careers-gbrx": "careers-gbrx.icims.com",
    }
    for slug, host in tenants.items():
        sm_url = f"https://{host}/sitemap.xml"
        resp = _get(sm_url, offline)
        if resp is not None:
            # Trim to the first few <url> entries to keep the fixture small.
            try:
                text = _trim_sitemap(resp.text, keep=5)
            except Exception:  # noqa: BLE001
                text = resp.text[:4000]
            save_text("icims", f"sitemap_{slug}.xml", text)
            _mark(f"icims/sitemap_{slug}.xml", True)
            job_url = _first_sitemap_loc(text)
        else:
            save_text("icims", f"sitemap_{slug}.xml", _fallback_icims_sitemap(host))
            _mark(f"icims/sitemap_{slug}.xml", False)
            job_url = f"https://{host}/jobs/9620/data-analyst/job"

        page_url = job_url + ("&" if "?" in job_url else "?") + "in_iframe=1"
        resp = _get(page_url, offline)
        if resp is not None:
            save_text("icims", f"jobpage_{slug}.html", resp.text[:20000])
            _mark(f"icims/jobpage_{slug}.html", True)
        else:
            title = "Data Analyst" if slug == "theharrispoll" else "Research Scientist"
            save_text("icims", f"jobpage_{slug}.html",
                      _fallback_icims_jobpage(title, "Boston", "MA"))
            _mark(f"icims/jobpage_{slug}.html", False)


def _trim_sitemap(text: str, keep: int = 5) -> str:
    ns = "http://www.sitemaps.org/schemas/sitemap/0.9"
    root = ET.fromstring(text.encode("utf-8"))
    tag_url = f"{{{ns}}}url"
    kept = 0
    for child in list(root):
        if child.tag == tag_url:
            kept += 1
            if kept > keep:
                root.remove(child)
    return ET.tostring(root, encoding="unicode")


def _first_sitemap_loc(text: str):
    ns = "http://www.sitemaps.org/schemas/sitemap/0.9"
    root = ET.fromstring(text.encode("utf-8"))
    loc = root.find(f".//{{{ns}}}loc")
    if loc is not None and loc.text:
        return loc.text.strip()
    return None


# ============================================================
# Ashby posting-api board
# ============================================================

def _fallback_ashby():
    return {
        "jobs": [
            {
                "title": "Data Scientist",
                "location": "Remote - US",
                "secondaryLocations": [{"location": "Boston, MA"}],
                "isRemote": True,
                "workplaceType": "Remote",
                "descriptionPlain": "Build models in Python over claims data.",
                "address": {"postalAddress": {"addressCountry": "US"}},
                "publishedAt": "2024-06-01T00:00:00Z",
                "jobUrl": "https://jobs.ashbyhq.com/example/1",
            },
            {
                "title": "Biostatistician",
                "location": "Cambridge, MA",
                "secondaryLocations": [],
                "isRemote": False,
                "workplaceType": "Onsite",
                "descriptionPlain": "SAS, GEE, Kaplan-Meier for clinical trials.",
                "address": {"postalAddress": {"addressCountry": "US"}},
                "publishedAt": "2024-06-02T00:00:00Z",
                "jobUrl": "https://jobs.ashbyhq.com/example/2",
            },
        ]
    }


def capture_ashby(offline: bool) -> None:
    print("Ashby (posting-api job-board)")
    slug = "ramp"  # a well-known public Ashby board
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    resp = _get(url, offline)
    if resp is not None:
        data = resp.json()
        if isinstance(data.get("jobs"), list):
            data["jobs"] = data["jobs"][:5]
        save_json("ashby", "board.json", data)
        _mark("ashby/board.json", True)
    else:
        save_json("ashby", "board.json", _fallback_ashby())
        _mark("ashby/board.json", False)


# ============================================================
# Lever postings
# ============================================================

def _fallback_lever():
    return [
        {
            "text": "Data Analyst",
            "categories": {
                "location": "Boston, MA",
                "allLocations": ["Boston, MA", "Remote - US"],
            },
            "workplaceType": "hybrid",
            "country": "US",
            "descriptionPlain": "SQL, Tableau, and reporting for a health plan.",
            "createdAt": 1717200000000,
            "hostedUrl": "https://jobs.lever.co/example/1",
        },
        {
            "text": "Machine Learning Engineer",
            "categories": {
                "location": "Remote",
                "allLocations": ["Remote"],
            },
            "workplaceType": "remote",
            "country": None,
            "descriptionPlain": "PyTorch, deep learning, NLP.",
            "createdAt": 1717286400000,
            "hostedUrl": "https://jobs.lever.co/example/2",
        },
    ]


def capture_lever(offline: bool) -> None:
    print("Lever (postings)")
    slug = "leverdemo"
    url = f"https://api.lever.co/v0/postings/{slug}"
    resp = _get(url, offline, params={"mode": "json"})
    if resp is not None:
        data = resp.json()
        if isinstance(data, list):
            data = data[:5]
        save_json("lever", "postings.json", data)
        _mark("lever/postings.json", True)
    else:
        save_json("lever", "postings.json", _fallback_lever())
        _mark("lever/postings.json", False)


# ============================================================
# Greenhouse list + one detail
# ============================================================

def _fallback_greenhouse_list():
    return {
        "jobs": [
            {
                "id": 4000001,
                "title": "Data Scientist",
                "location": {"name": "Boston, MA"},
                "first_published": "2024-05-15T12:00:00-04:00",
                "absolute_url": "https://boards.greenhouse.io/example/jobs/4000001",
            },
            {
                "id": 4000002,
                "title": "HEOR Analyst",
                "location": {"name": "Remote - US"},
                "first_published": "2024-05-16T12:00:00-04:00",
                "absolute_url": "https://boards.greenhouse.io/example/jobs/4000002",
            },
        ],
        "meta": {"total": 2},
    }


def _fallback_greenhouse_detail():
    return {
        "id": 4000001,
        "title": "Data Scientist",
        "location": {"name": "Boston, MA"},
        "first_published": "2024-05-15T12:00:00-04:00",
        "absolute_url": "https://boards.greenhouse.io/example/jobs/4000001",
        # Greenhouse returns entity-escaped HTML in `content`.
        "content": (
            "&lt;p&gt;Join our HEOR team. You will work with ICD-10 and CPT "
            "codes and MarketScan claims data.&lt;/p&gt;"
        ),
    }


def capture_greenhouse(offline: bool) -> None:
    print("Greenhouse (list + detail)")
    slug = "greenhouse"  # Greenhouse's own public board
    base = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    resp = _get(base, offline)
    job_id = None
    if resp is not None:
        data = resp.json()
        if isinstance(data.get("jobs"), list):
            data["jobs"] = data["jobs"][:5]
            if data["jobs"]:
                job_id = data["jobs"][0].get("id")
        save_json("greenhouse", "list.json", data)
        _mark("greenhouse/list.json", True)
    else:
        save_json("greenhouse", "list.json", _fallback_greenhouse_list())
        _mark("greenhouse/list.json", False)

    resp = _get(f"{base}/{job_id}", offline, params={"content": "true"}) if job_id else None
    if resp is not None:
        save_json("greenhouse", "detail.json", resp.json())
        _mark("greenhouse/detail.json", True)
    else:
        save_json("greenhouse", "detail.json", _fallback_greenhouse_detail())
        _mark("greenhouse/detail.json", False)


# ============================================================
# BambooHR list + one detail
# ============================================================

# locationType code mapping, pinned from captured data. See README.
BAMBOOHR_LOCATION_TYPE = {"0": "onsite", "1": "remote", "2": "hybrid"}


def _fallback_bamboohr_list():
    return {
        "result": [
            {
                "id": 101,
                "jobOpeningName": "Data Analyst",
                "atsLocation": {"city": "Boston", "state": "MA", "country": "United States"},
                "isRemote": False,
                "locationType": "0",
                "datePosted": "2024-05-20",
            },
            {
                "id": 102,
                "jobOpeningName": "Clinical Data Manager",
                "atsLocation": {"city": "", "state": "", "country": "United States"},
                "isRemote": True,
                "locationType": "1",
                "datePosted": "2024-05-21",
            },
            {
                "id": 103,
                "jobOpeningName": "Biostatistician",
                "atsLocation": {"city": "Cambridge", "state": "MA", "country": "United States"},
                "isRemote": False,
                "locationType": "2",
                "datePosted": "2024-05-22",
            },
        ]
    }


def _fallback_bamboohr_detail():
    return {
        "result": {
            "jobOpening": {
                "id": 101,
                "jobOpeningName": "Data Analyst",
                "description": (
                    "<p>Analyze operational data with SQL and build Tableau "
                    "dashboards. A bachelor's or master's degree is required.</p>"
                ),
                "datePosted": "2024-05-20",
                "atsLocation": {"city": "Boston", "state": "MA", "country": "United States"},
                "isRemote": False,
                "locationType": "0",
            }
        }
    }


def capture_bamboohr(offline: bool) -> None:
    print("BambooHR (list + detail)")
    slug = "bamboohr"
    list_url = f"https://{slug}.bamboohr.com/careers/list"
    resp = _get(list_url, offline)
    job_id = None
    if resp is not None and "application/json" in resp.headers.get("Content-Type", ""):
        data = resp.json()
        if isinstance(data.get("result"), list):
            data["result"] = data["result"][:5]
            if data["result"]:
                job_id = data["result"][0].get("id")
        save_json("bamboohr", "list.json", data)
        _mark("bamboohr/list.json", True)
    else:
        save_json("bamboohr", "list.json", _fallback_bamboohr_list())
        _mark("bamboohr/list.json", False)
        job_id = 101

    resp = _get(f"https://{slug}.bamboohr.com/careers/{job_id}/detail", offline) if job_id else None
    if resp is not None and "application/json" in resp.headers.get("Content-Type", ""):
        save_json("bamboohr", "detail.json", resp.json())
        _mark("bamboohr/detail.json", True)
    else:
        save_json("bamboohr", "detail.json", _fallback_bamboohr_detail())
        _mark("bamboohr/detail.json", False)


# ============================================================
# Small location map extracted from data/locations.json
# ============================================================

# The small map mirrors conftest's _SMALL_LOCATION_ROWS so the fixture and the
# session fixture agree. Admin is the GeoNames admin1 code (two-letter US state
# code for US rows), kept on every row per the task.
SMALL_LOCATION_ROWS = [
    # Greater Boston (inside the 30-mile radius)
    ["Boston", "MA", "US", 42.3601, -71.0589],
    ["Cambridge", "MA", "US", 42.3736, -71.1097],
    ["Waltham", "MA", "US", 42.3765, -71.2356],
    ["Burlington", "MA", "US", 42.5048, -71.1956],
    ["Quincy", "MA", "US", 42.2529, -71.0023],
    ["Framingham", "MA", "US", 42.2793, -71.4162],
    ["Lowell", "MA", "US", 42.6334, -71.3162],
    # New England outside the radius (Secondary_Region)
    ["Worcester", "MA", "US", 42.2626, -71.8023],
    ["Providence", "RI", "US", 41.8240, -71.4128],
    ["Hartford", "CT", "US", 41.7658, -72.6734],
    # US outside New England
    ["San Francisco", "CA", "US", 37.7749, -122.4194],
    ["New York City", "NY", "US", 40.7128, -74.0060],
    ["Chicago", "IL", "US", 41.8781, -87.6298],
    # Ambiguous cities: same name exists in another country too
    ["Cambridge", "ENG", "GB", 52.2053, 0.1218],
    ["Quincy", "B8", "FR", 48.7167, 1.9667],
    ["Burlington", "ON", "CA", 43.3255, -79.7990],
    # Non-US cities
    ["Toronto", "ON", "CA", 43.6532, -79.3832],
    ["London", "ENG", "GB", 51.5074, -0.1278],
    ["Berlin", "BE", "DE", 52.5200, 13.4050],
]
SMALL_LOCATION_SCHEMA = ["city", "admin", "country", "lat", "lng"]


def _verify_rows_against_locations_json() -> None:
    """Best-effort check that the small-map coordinates are plausible.

    Reads ``data/locations.json`` if present and prints a note for any US row
    whose city is absent. This does not fail the capture; the curated rows are
    authoritative because ``locations.json`` holds many same-named cities.
    """
    if not os.path.exists(LOCATIONS_JSON):
        print("  note: data/locations.json not found; using curated rows")
        return
    try:
        with open(LOCATIONS_JSON, encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        print(f"  note: could not read locations.json ({type(exc).__name__})")
        return
    cities = {row[0] for row in payload.get("data", [])}
    missing = sorted({r[0] for r in SMALL_LOCATION_ROWS if r[0] not in cities})
    if missing:
        print(f"  note: cities not found verbatim in locations.json: {missing}")
    else:
        print("  all small-map cities appear in locations.json")


def capture_locations() -> None:
    print("Locations (small map from data/locations.json)")
    _verify_rows_against_locations_json()
    payload = {"schema": SMALL_LOCATION_SCHEMA, "data": SMALL_LOCATION_ROWS}
    path = os.path.join(FIXTURE_DIR, "locations_small.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print(f"  wrote {os.path.relpath(path, REPO_ROOT)}")
    _mark("locations_small.json", False)


# ============================================================
# Entry point
# ============================================================

CAPTURERS = {
    "workday": capture_workday,
    "icims": capture_icims,
    "ashby": capture_ashby,
    "lever": capture_lever,
    "greenhouse": capture_greenhouse,
    "bamboohr": capture_bamboohr,
}


def main(argv):
    offline = "--offline" in argv
    args = [a for a in argv if not a.startswith("-")]
    platforms = args or list(CAPTURERS)

    if offline:
        print("Running in --offline mode: writing hand-built fallbacks only.\n")

    for name in platforms:
        fn = CAPTURERS.get(name)
        if fn is None:
            print(f"Unknown platform: {name}")
            continue
        fn(offline)

    # Always (re)write the small location map.
    if not args or "locations" in args:
        capture_locations()

    print("\nProvenance:")
    for name, kind in sorted(PROVENANCE.items()):
        print(f"  {kind:10} {name}")


if __name__ == "__main__":
    main(sys.argv[1:])
