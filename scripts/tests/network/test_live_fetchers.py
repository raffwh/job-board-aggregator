"""Network-marked live fetcher tests.

These tests hit live ATS endpoints and are therefore marked ``network``. The
default pytest invocation uses ``addopts = -m "not network"`` (see
``pytest.ini``), so pytest *collects* these tests but *skips* them unless the
suite is run explicitly with ``pytest -m network``.

They are deliberately kept out of the scheduled workflows (the workflows run
``pytest -m "not network"``). The user runs them on demand.

Validates Requirements 3.13, 4.7, 10.2, 10.3.
"""

import pytest

import scraper
from detail_enricher import DETAIL_FETCHERS, enrich_details, needs_detail
from diagnostics import DiagnosticsCollector, RunContext

pytestmark = pytest.mark.network


def _ctx():
    """A throwaway, scoped context so nothing touches shared caches."""
    return RunContext(
        diag=DiagnosticsCollector(),
        config=scraper.personal_config,
        scoped=True,
    )


# ---------------------------------------------------------------------------
# Workday: complete pagination returns more than 20 jobs (Requirement 3.13).
# This is the IQVIA fix. The old code capped every tenant at the first page of
# 20 because it re-read ``total`` from page 2 (which Workday reports as 0).
# ---------------------------------------------------------------------------

# IQVIA must pass. The other two large tenants are checked too, but are allowed
# to xfail/skip if their site_id has drifted, rather than hard-failing the suite.
_WORKDAY_TENANTS = [
    pytest.param("iqvia|wd1|iqvia", False, id="iqvia"),
    pytest.param("cvshealth|wd1|cvs_health_careers", True, id="cvshealth"),
    pytest.param("pfizer|wd1|pfizercareers", True, id="pfizer"),
]


@pytest.mark.parametrize("slug, tolerant", _WORKDAY_TENANTS)
def test_workday_returns_more_than_twenty(slug, tolerant):
    """A Workday tenant whose page 1 reports a total above 20 returns more
    than 20 Job_Records (Requirement 3.13)."""
    try:
        jobs, outcome = scraper._fetch_workday(slug, _ctx())
    except Exception as exc:  # pragma: no cover - live endpoint drift
        if tolerant:
            pytest.xfail(f"{slug}: fetch errored, site_id may have changed: {exc}")
        raise

    if outcome.status != "ok":
        if tolerant:
            pytest.xfail(f"{slug}: non-ok fetch outcome {outcome.status!r}")
        assert outcome.status == "ok", f"{slug}: expected ok, got {outcome.status!r}"

    reported_total = outcome.reported_total or 0
    if reported_total <= 20:
        # The tenant does not currently advertise more than 20 postings, so the
        # ">20" expectation does not apply. IQVIA is expected to exceed 20.
        if tolerant:
            pytest.skip(f"{slug}: page 1 total is {reported_total} (not above 20)")
        pytest.fail(f"{slug}: expected page-1 total above 20, got {reported_total}")

    assert len(jobs) > 20, (
        f"{slug}: page-1 total is {reported_total} but only {len(jobs)} jobs "
        "were returned (pagination regression)"
    )


# ---------------------------------------------------------------------------
# iCIMS: a known-good company returns at least 1 job (Requirement 4.7).
# ``theharrispoll`` is a bare slug (resolved via ``careers-theharrispoll``);
# ``careers-gbrx`` already carries the ``careers-`` prefix.
# ---------------------------------------------------------------------------

_ICIMS_SLUGS = [
    pytest.param("theharrispoll", id="theharrispoll-bare"),
    pytest.param("careers-gbrx", id="careers-gbrx-prefixed"),
]


@pytest.mark.parametrize("slug", _ICIMS_SLUGS)
def test_icims_returns_at_least_one_job(slug):
    """A known-good iCIMS company returns at least 1 Job_Record
    (Requirement 4.7)."""
    jobs, outcome = scraper._fetch_icims(slug, _ctx())
    assert outcome.status == "ok", f"{slug}: expected ok, got {outcome.status!r}"
    assert len(jobs) >= 1, f"{slug}: expected at least 1 job, got {len(jobs)}"


# ---------------------------------------------------------------------------
# Detail fetch per platform returns a non-empty description
# (Requirements 10.2 Workday, 10.3 Greenhouse/BambooHR; iCIMS detail is 4.6
# but is exercised here as the iCIMS-platform detail path).
# ---------------------------------------------------------------------------


def _first_with_description(jobs, ctx):
    """Enrich ``jobs`` one at a time and return the first with a description."""
    for job in jobs:
        enrich_details([job], ctx)
        if (job.get("description") or "").strip():
            return job
    return None


def test_workday_detail_description_non_empty():
    """One Workday detail fetch yields a non-empty description
    (Requirement 10.2)."""
    ctx = _ctx()
    jobs, outcome = scraper._fetch_workday("iqvia|wd1|iqvia", ctx)
    if outcome.status != "ok" or not jobs:
        pytest.skip(f"iqvia: no jobs to enrich (status {outcome.status!r})")

    assert DETAIL_FETCHERS.get("Workday") is not None
    enriched = _first_with_description(jobs[:5], ctx)
    assert enriched is not None, "no Workday job returned a non-empty description"
    assert enriched.get("description", "").strip()


def test_icims_detail_description_non_empty():
    """One iCIMS detail fetch yields a non-empty description
    (Requirement 10.3 detail path for iCIMS)."""
    ctx = _ctx()
    jobs = None
    for slug in ("theharrispoll", "careers-gbrx"):
        fetched, outcome = scraper._fetch_icims(slug, ctx)
        if outcome.status == "ok" and fetched:
            jobs = fetched
            break
    if not jobs:
        pytest.skip("no iCIMS jobs available to enrich")

    assert DETAIL_FETCHERS.get("iCIMS") is not None
    # Every iCIMS list record lacks a description, so needs_detail is true.
    assert needs_detail(jobs[0])
    enriched = _first_with_description(jobs[:5], ctx)
    assert enriched is not None, "no iCIMS job returned a non-empty description"
    assert enriched.get("description", "").strip()
