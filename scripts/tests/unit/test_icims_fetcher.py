"""Unit tests for the iCIMS fetcher, driven by the captured sitemap fixtures.

These exercise :func:`scraper._fetch_icims` and
:func:`scraper._parse_icims_sitemap` with no network: every ``requests.get`` is
replaced by a scripted stand-in keyed by URL, built from the fixtures under
``scripts/tests/fixtures/icims/``.

Covered:
- A 404 on the first host candidate falls back to the alternate host once, and
  that second candidate succeeds (Requirement 4.3).
- An HTTP 403 records status ``blocked``; that outcome is deliberately left out
  of the Dead_Slug_Cache (``should_cache_dead`` is False) (Requirement 4.4).
- The sitemap fixture parses into Job_Records whose location is empty and whose
  Location_Status is ``unknown`` -- no hardcoded "United States" (Requirement 4.5).
- Both host candidates returning 404 records an ``http_error`` on the last host,
  so a genuinely dead slug can be dead-cached (Requirements 4.3, 6.3, 6.4).
"""

from pathlib import Path

import pytest

import scraper
from ats_helpers import icims_host_candidates
from diagnostics import DiagnosticsCollector, RunContext


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "icims"


def _load_sitemap(name):
    return (FIXTURE_DIR / name).read_bytes()


# ============================================================
# Scripted GET stand-in
# ============================================================


class _FakeGetResponse:
    """Minimal ``requests.Response`` stand-in for the iCIMS GET path.

    ``_fetch_icims`` reads only ``status_code`` and ``content``.
    """

    def __init__(self, status_code, content=b""):
        self.status_code = status_code
        if isinstance(content, str):
            content = content.encode("utf-8")
        self.content = content


def _scripted_get(by_url):
    """Return a ``requests.get`` replacement keyed by URL.

    ``by_url`` maps a sitemap URL to a ``_FakeGetResponse``. Every call is
    recorded on ``.calls`` (a list of URLs) so tests can assert how many hosts
    were tried and in what order. An unscripted URL fails loudly.
    """
    calls = []

    def get(url, headers=None, timeout=None):
        calls.append(url)
        if url not in by_url:
            raise AssertionError(f"unscripted iCIMS request: {url}")
        return by_url[url]

    get.calls = calls
    return get


def _ctx():
    return RunContext(diag=DiagnosticsCollector(), config=None, scoped=True)


def _sitemap_url(host):
    return f"https://{host}/sitemap.xml"


# ============================================================
# Requirement 4.3: 404 on candidate 1, success on candidate 2
# ============================================================


def test_404_on_first_candidate_falls_back_to_alternate_host(monkeypatch):
    """A 404 on candidate 1 tries the alternate host once, which succeeds
    (Requirement 4.3)."""
    slug = "theharrispoll"
    first, second = icims_host_candidates(slug)
    # Sanity: a bare slug tries ``careers-<slug>`` first, then ``<slug>``.
    assert first == "careers-theharrispoll.icims.com"
    assert second == "theharrispoll.icims.com"

    get = _scripted_get(
        {
            _sitemap_url(first): _FakeGetResponse(404),
            _sitemap_url(second): _FakeGetResponse(
                200, _load_sitemap("sitemap_theharrispoll.xml")
            ),
        }
    )
    monkeypatch.setattr(scraper.requests, "get", get)

    jobs, outcome = scraper._fetch_icims(slug, _ctx())

    # Both hosts were tried, in order, exactly once each.
    assert get.calls == [_sitemap_url(first), _sitemap_url(second)]
    assert outcome.status == "ok"
    assert outcome.host == second
    assert len(jobs) == 3


# ============================================================
# Requirement 4.4: 403 => blocked, not dead-cacheable
# ============================================================


def test_403_yields_blocked_and_is_not_dead_cacheable(monkeypatch):
    """An HTTP 403 records status ``blocked`` and is kept out of the
    Dead_Slug_Cache (Requirement 4.4)."""
    slug = "theharrispoll"
    first = icims_host_candidates(slug)[0]

    get = _scripted_get({_sitemap_url(first): _FakeGetResponse(403)})
    monkeypatch.setattr(scraper.requests, "get", get)

    jobs, outcome = scraper._fetch_icims(slug, _ctx())

    assert jobs == []
    assert outcome.status == "blocked"
    assert outcome.http_status == 403
    # A 403 is transient: the alternate host is not tried, and the slug must
    # not be dead-cached so a temporary block cannot hide a company.
    assert get.calls == [_sitemap_url(first)]
    assert scraper.should_cache_dead(outcome) is False


# ============================================================
# Requirement 4.5: sitemap parses with empty location and unknown status
# ============================================================


def test_sitemap_parses_with_empty_location_and_unknown_status(monkeypatch):
    """Sitemap entries become Job_Records with ``location == ""`` and
    ``location_status == "unknown"`` -- no hardcoded "United States"
    (Requirement 4.5)."""
    slug = "careers-gbrx"
    first = icims_host_candidates(slug)[0]
    # A slug that already carries a known prefix is tried at ``<slug>`` first.
    assert first == "careers-gbrx.icims.com"

    get = _scripted_get(
        {
            _sitemap_url(first): _FakeGetResponse(
                200, _load_sitemap("sitemap_careers-gbrx.xml")
            )
        }
    )
    monkeypatch.setattr(scraper.requests, "get", get)

    jobs, outcome = scraper._fetch_icims(slug, _ctx())

    assert outcome.status == "ok"
    assert len(jobs) == 3

    titles = {j["title"] for j in jobs}
    assert titles == {"Data Analyst", "Senior Biostatistician", "Research Scientist"}

    for job in jobs:
        assert job["location"] == ""
        assert job["location_status"] == "unknown"
        assert job["coords"] is None
        assert job["ats"] == "iCIMS"
        # The old fabricated "United States" value must not reappear anywhere.
        assert job["location"] != "United States"


# ============================================================
# Requirement 4.3 / 6.3 / 6.4: both candidates 404 => http_error, dead-cacheable
# ============================================================


def test_both_candidates_404_record_http_error_on_last_host(monkeypatch):
    """When both host candidates return 404, the outcome is an ``http_error``
    on the last host tried, so the slug can be dead-cached (Requirement 4.3;
    Requirements 6.3, 6.4)."""
    slug = "theharrispoll"
    first, second = icims_host_candidates(slug)

    get = _scripted_get(
        {
            _sitemap_url(first): _FakeGetResponse(404),
            _sitemap_url(second): _FakeGetResponse(404),
        }
    )
    monkeypatch.setattr(scraper.requests, "get", get)

    jobs, outcome = scraper._fetch_icims(slug, _ctx())

    assert jobs == []
    assert outcome.status == "http_error"
    assert outcome.http_status == 404
    assert outcome.host == second
    # Both candidates were tried exactly once, in order.
    assert get.calls == [_sitemap_url(first), _sitemap_url(second)]
    # Every candidate was a 404, so this permanent failure is dead-cacheable.
    assert scraper.should_cache_dead(outcome) is True
