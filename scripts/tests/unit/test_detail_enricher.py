"""Unit tests for per-platform detail enrichment, driven offline.

These exercise the detail fetchers and ``enrich_details`` orchestration in
``scripts/detail_enricher.py`` with no network: every HTTP call goes through
the scripted ``FakeSession`` from ``conftest.py`` (or a small purpose-built
stub), keyed by the exact URL each fetcher builds.

Covered:
- Workday detail: ``jobDescription`` -> plain text, ``jobReqId``, primary plus
  additional locations, country, and the ``remoteType`` mapping (Requirement 10.2).
- iCIMS detail: the JSON-LD ``JobPosting`` is parsed, ``UNAVAILABLE`` address
  parts are dropped, ``TELECOMMUTE`` becomes ``remote``, and the JSON-LD title
  overrides the URL-derived title (Requirement 4.6).
- Greenhouse detail: entity-escaped HTML ``content`` becomes plain text
  (Requirement 10.3).
- BambooHR detail: description and ``datePosted`` are read (Requirement 10.3).
- ``enrich_details``: a failing fetch keeps the job without a description, marks
  it ``detail_failed``, and is counted in diagnostics (Requirements 10.5, 10.6);
  a concurrency-tracking fetcher never exceeds ``DETAIL_MAX_WORKERS``
  (Requirement 10.6); the ``DETAIL_MAX_PER_RUN`` cap counts the skipped jobs
  (Requirement 10.7); and ``needs_detail`` is true for a no-description,
  ``location_multi``, iCIMS, or ``remote_scope == "unspecified"`` job
  (Requirement 10.9).

Validates: Requirements 4.6, 10.1, 10.2, 10.3, 10.5, 10.6, 10.7, 10.9
"""

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import detail_enricher
from detail_enricher import (
    DETAIL_FETCHERS,
    _detail_bamboohr,
    _detail_greenhouse,
    _detail_icims,
    _detail_workday,
    enrich_details,
    needs_detail,
)
from diagnostics import DiagnosticsCollector, RunContext


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"


def _load_json(platform, name):
    return json.loads((FIXTURE_ROOT / platform / name).read_text(encoding="utf-8"))


def _load_text(platform, name):
    return (FIXTURE_ROOT / platform / name).read_text(encoding="utf-8")


# ============================================================
# Workday detail (Requirement 10.2)
# ============================================================


def test_workday_detail_reads_description_reqid_locations_country_remote(fake_session):
    """The Workday fetcher reads jobDescription (as plain text), jobReqId, the
    primary plus additional locations, the country, and maps remoteType
    (Requirement 10.2)."""
    detail = _load_json("workday", "detail.json")
    # Add a second (additional) location so both branches are covered.
    detail["jobPostingInfo"]["additionalLocations"] = ["Boston, MA"]
    detail["jobPostingInfo"]["remoteType"] = "Hybrid"

    job = {
        "company_slug": "iqvia|wd1|iqvia",
        "workday_external_path": (
            "/IQVIA/job/Dublin-Ireland/"
            "Senior-Data-Scientist--Epidemiology---RWE-Analytics---FSP_R1573054"
        ),
    }
    url = detail_enricher._workday_detail_url(job)
    # {base}/wday/cxs/{tenant}/{site}{externalPath}; here tenant and site are
    # both "iqvia" and the externalPath already starts with "/IQVIA/...".
    assert url == (
        "https://iqvia.wd1.myworkdayjobs.com/wday/cxs/iqvia/iqvia/IQVIA/job/"
        "Dublin-Ireland/"
        "Senior-Data-Scientist--Epidemiology---RWE-Analytics---FSP_R1573054"
    )
    fake_session.add_response("GET", url, json_data=detail)

    result = _detail_workday(job, fake_session)

    # Description is plain text: no tags, and known content is present.
    assert "<p>" not in result["description"]
    assert "Senior Data Scientist" in result["description"]
    assert result["req_id"] == "R1573054"
    assert result["locations"] == ["Dublin, Ireland", "Boston, MA"]
    assert result["native_country"] == "Ireland"
    assert result["native_workplace"] == "hybrid"


@pytest.mark.parametrize(
    "remote_type,expected",
    [("Remote", "remote"), ("Hybrid", "hybrid"), ("On-site", "onsite")],
)
def test_workday_remote_type_mapping(fake_session, remote_type, expected):
    """Each Workday remoteType maps to the expected Workplace_Type
    (Requirement 10.2)."""
    detail = _load_json("workday", "detail.json")
    detail["jobPostingInfo"]["remoteType"] = remote_type

    job = {
        "company_slug": "iqvia|wd1|iqvia",
        "workday_external_path": "/IQVIA/job/x/Role_R1",
    }
    url = detail_enricher._workday_detail_url(job)
    fake_session.add_response("GET", url, json_data=detail)

    result = _detail_workday(job, fake_session)
    assert result["native_workplace"] == expected


# ============================================================
# iCIMS detail (Requirement 4.6)
# ============================================================


def _icims_url(job):
    base = job["url"]
    joiner = "&" if "?" in base else "?"
    return f"{base}{joiner}in_iframe=1"


def test_icims_detail_parses_jsonld_drops_unavailable_and_overrides_title(fake_session):
    """The iCIMS fetcher parses the JSON-LD JobPosting, drops UNAVAILABLE
    address parts, and lets the JSON-LD title override the URL title
    (Requirement 4.6)."""
    html = _load_text("icims", "jobpage_theharrispoll.html")
    job = {
        "ats": "iCIMS",
        "url": "https://careers-theharrispoll.icims.com/jobs/1234/data-analyst/job",
        "title": "data-analyst",  # URL-derived placeholder, should be overridden
    }
    fake_session.add_response("GET", _icims_url(job), text=html)

    result = _detail_icims(job, fake_session)

    # JSON-LD title overrides the URL-derived title.
    assert result["title"] == "Data Analyst"
    # Description converted to plain text.
    assert "<p>" not in result["description"]
    assert "Data Analyst" in result["description"]
    # Only the usable address survives; the UNAVAILABLE placeholder is dropped.
    assert result["locations"] == ["Boston, MA, US"]
    assert "UNAVAILABLE" not in result["locations"][0]


def test_icims_telecommute_is_remote(fake_session):
    """A JSON-LD jobLocationType of TELECOMMUTE classifies the job as remote
    (Requirement 4.6)."""
    jsonld = {
        "@context": "https://schema.org/",
        "@type": "JobPosting",
        "title": "Remote Data Scientist",
        "description": "<p>Work from anywhere.</p>",
        "jobLocationType": "TELECOMMUTE",
    }
    html = (
        "<!doctype html><html><head>"
        '<script type="application/ld+json">' + json.dumps(jsonld) + "</script>"
        "</head><body></body></html>"
    )
    job = {
        "ats": "iCIMS",
        "url": "https://careers-x.icims.com/jobs/5/remote/job",
        "title": "placeholder",
    }
    fake_session.add_response("GET", _icims_url(job), text=html)

    result = _detail_icims(job, fake_session)

    assert result["native_workplace"] == "remote"
    assert result["title"] == "Remote Data Scientist"


# ============================================================
# Greenhouse detail (Requirement 10.3)
# ============================================================


def test_greenhouse_detail_converts_escaped_html_to_plain_text(fake_session):
    """The Greenhouse fetcher converts the entity-escaped HTML content to plain
    text (Requirement 10.3)."""
    detail = _load_json("greenhouse", "detail.json")
    job = {"company_slug": "greenhouse", "ats_job_id": 8126988}
    url = (
        "https://boards-api.greenhouse.io/v1/boards/greenhouse/jobs/8126988"
        "?content=true"
    )
    fake_session.add_response("GET", url, json_data=detail)

    result = _detail_greenhouse(job, fake_session)

    text = result["description"]
    # The fixture content is entity-escaped HTML (&lt;div&gt;...). After
    # conversion, no markup (literal or escaped) remains and the prose is there.
    assert "&lt;" not in text
    assert "<div" not in text
    assert "<p>" not in text
    assert "Commercial Counsel" in text
    assert "Who will love this job" in text


# ============================================================
# BambooHR detail (Requirement 10.3)
# ============================================================


def test_bamboohr_detail_reads_description_and_date(fake_session):
    """The BambooHR fetcher reads the opening's description (as plain text) and
    its datePosted (Requirement 10.3)."""
    detail = _load_json("bamboohr", "detail.json")
    job = {"company_slug": "acme", "ats_job_id": 101}
    url = "https://acme.bamboohr.com/careers/101/detail"
    fake_session.add_response("GET", url, json_data=detail)

    result = _detail_bamboohr(job, fake_session)

    assert "<p>" not in result["description"]
    assert "Analyze operational data with SQL" in result["description"]
    assert result["published_at"] == "2024-05-20"


# ============================================================
# needs_detail (Requirement 10.9)
# ============================================================


@pytest.mark.parametrize(
    "job",
    [
        {"ats": "Greenhouse", "description": ""},  # no description
        {"ats": "Greenhouse", "description": "   "},  # whitespace only
        {"ats": "Greenhouse", "description": "x", "location_multi": True},
        {"ats": "iCIMS", "description": "x"},  # iCIMS always needs detail
        {"ats": "Workday", "description": "x", "remote_scope": "unspecified"},
    ],
)
def test_needs_detail_true_cases(job):
    """needs_detail is true for no-description, location_multi, iCIMS, and
    remote_scope == 'unspecified' jobs (Requirement 10.9)."""
    assert needs_detail(job) is True


def test_needs_detail_false_when_satisfied():
    """A job with a description, single location, non-iCIMS platform, and a
    specified remote scope does not need a detail fetch (Requirement 10.9)."""
    job = {
        "ats": "Greenhouse",
        "description": "A real description.",
        "location_multi": False,
        "remote_scope": "us",
    }
    assert needs_detail(job) is False


# ============================================================
# enrich_details orchestration (Requirements 10.5, 10.6, 10.7)
# ============================================================


def _ctx(config=None):
    return RunContext(diag=DiagnosticsCollector(), config=config, scoped=True)


def test_enrich_details_failure_keeps_job_and_counts_it(monkeypatch):
    """A fetch that fails every attempt keeps the job (no description), marks it
    detail_failed, and is counted per platform in diagnostics
    (Requirements 10.5, 10.6)."""
    # Speed: skip the retry backoff sleeps.
    monkeypatch.setattr(detail_enricher.time, "sleep", lambda *_: None)

    def boom(job, session):
        raise RuntimeError("network down")

    monkeypatch.setitem(DETAIL_FETCHERS, "Greenhouse", boom)

    job = {"ats": "Greenhouse", "company_slug": "acme", "description": ""}
    ctx = _ctx()

    enrich_details([job], ctx, session=object())

    # The job is kept, has no description, and is flagged.
    assert "description" not in job or not job["description"]
    assert job["detail_failed"] is True

    # The failure (and the request) are tallied per platform in diagnostics.
    assert ctx.diag._detail_failed["Greenhouse"] == 1
    assert ctx.diag._detail_requested["Greenhouse"] == 1


def test_enrich_details_respects_max_workers(monkeypatch):
    """A concurrency-tracking fetcher never sees more than DETAIL_MAX_WORKERS
    in flight at once (Requirement 10.6)."""
    max_workers = 3
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def tracking_fetcher(job, session):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            # Hold the slot long enough that extra jobs would overlap if the
            # pool were unbounded.
            time.sleep(0.02)
            return {"description": "done"}
        finally:
            with lock:
                state["active"] -= 1

    monkeypatch.setitem(DETAIL_FETCHERS, "Greenhouse", tracking_fetcher)

    jobs = [
        {"ats": "Greenhouse", "company_slug": f"c{i}", "description": ""}
        for i in range(12)
    ]
    config = SimpleNamespace(DETAIL_MAX_WORKERS=max_workers, DETAIL_MAX_PER_RUN=None)
    ctx = _ctx(config=config)

    enrich_details(jobs, ctx, session=object())

    assert state["peak"] <= max_workers
    # Every job was still processed.
    assert all(job.get("description") == "done" for job in jobs)


def test_enrich_details_per_run_cap_counts_skips(monkeypatch):
    """DETAIL_MAX_PER_RUN limits fetches per platform and counts the rest as
    skipped (Requirement 10.7)."""
    fetched = []

    def counting_fetcher(job, session):
        fetched.append(job["company_slug"])
        return {"description": "done"}

    monkeypatch.setitem(DETAIL_FETCHERS, "Greenhouse", counting_fetcher)

    jobs = [
        {"ats": "Greenhouse", "company_slug": f"c{i}", "description": ""}
        for i in range(10)
    ]
    config = SimpleNamespace(DETAIL_MAX_WORKERS=4, DETAIL_MAX_PER_RUN=4)
    ctx = _ctx(config=config)

    enrich_details(jobs, ctx, session=object())

    assert len(fetched) == 4
    assert ctx.diag._detail_requested["Greenhouse"] == 4
    assert ctx.diag._detail_skipped["Greenhouse"] == 6


def test_enrich_details_applies_results_to_jobs(monkeypatch):
    """A successful fetch applies the description and locations back onto the
    Job_Record (Requirements 10.1, 10.2)."""

    def fetcher(job, session):
        return {
            "description": "Full job description.",
            "locations": ["Dublin, Ireland", "Boston, MA"],
            "req_id": "R1573054",
            "native_country": "Ireland",
        }

    monkeypatch.setitem(DETAIL_FETCHERS, "Workday", fetcher)

    job = {"ats": "Workday", "company_slug": "iqvia|wd1|iqvia", "description": ""}
    ctx = _ctx()

    enrich_details([job], ctx, session=object())

    assert job["description"] == "Full job description."
    assert job["location"] == "Dublin, Ireland, Boston, MA"
    assert job["location_multi"] is True
    assert job["req_id"] == "R1573054"
    assert job["native_country"] == "Ireland"
