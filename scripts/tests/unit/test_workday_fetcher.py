"""Unit tests for the Workday fetcher, driven by the captured CXS fixtures.

These exercise :func:`scraper._fetch_workday`,
:func:`scraper._workday_post_with_retry`, and
:func:`scraper._normalize_workday` with no network: every ``requests.post`` is
replaced by a scripted stand-in built from the fixtures under
``scripts/tests/fixtures/workday/``, and the location lookup is stubbed so the
29 MB ``locations.json`` is never touched.

Covered:
- Page retries: 503, 503, 200 succeeds; a non-retryable status surfaces an
  ``http_error`` Fetch_Outcome (Requirement 3.7).
- "N Locations" handling: ``location_multi`` is set and the location is derived
  from ``externalPath`` (Requirement 3.10).
- ``req_id`` extraction from ``bulletFields`` (Requirement 3.9).
- Empty ``WORKDAY_SEARCH_TERMS`` runs a single empty-``searchText`` query
  (Requirement 3.5).
- The default ``WORKDAY_SEARCH_TERMS`` lists the 13 CV terms in order
  (Requirement 18.11).
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import personal_config
import requests
import scraper
from diagnostics import DiagnosticsCollector, RunContext


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "workday"


def _load_fixture(name):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


# ============================================================
# Scripted POST stand-in
# ============================================================


class _FakePostResponse:
    """Minimal ``requests.Response`` stand-in for the Workday POST path.

    ``_workday_post_with_retry`` reads only ``status_code`` and ``json()``.
    """

    def __init__(self, status_code, json_data=None, *, bad_json=False):
        self.status_code = status_code
        self._json_data = json_data
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._json_data


def _scripted_post(responses):
    """Return a ``requests.post`` replacement that yields ``responses`` in order.

    Each entry is a ``_FakePostResponse`` (or a callable raising to model a
    network error). The last entry repeats once the script is exhausted, so a
    paginated query that asks for more pages than scripted still terminates on
    the final (typically empty) page. Every call is recorded on ``.calls``.
    """
    state = {"i": 0}
    calls = []

    def post(url, json=None, headers=None, timeout=None):  # noqa: A002
        calls.append({"url": url, "json": json, "headers": headers})
        idx = min(state["i"], len(responses) - 1)
        state["i"] += 1
        item = responses[idx]
        if callable(item):
            return item()
        return item

    post.calls = calls
    return post


@pytest.fixture(autouse=True)
def _no_network_or_sleep(monkeypatch):
    """Keep every test offline and instant.

    ``enrich_location`` would otherwise load the 29 MB ``locations.json``; the
    backoff ``time.sleep`` would otherwise stall the retry tests.
    """
    monkeypatch.setattr(scraper, "enrich_location", lambda loc: (False, None))
    monkeypatch.setattr(scraper.time, "sleep", lambda *a, **k: None)


def _ctx(config=None):
    return RunContext(diag=DiagnosticsCollector(), config=config, scoped=True)


# ============================================================
# Requirement 3.7: page retries and http_error
# ============================================================


def test_post_with_retry_503_503_then_200_succeeds(monkeypatch):
    """Two 503s followed by a 200 returns the 200 body (Requirement 3.7)."""
    page = _load_fixture("list_page1.json")
    post = _scripted_post(
        [
            _FakePostResponse(503),
            _FakePostResponse(503),
            _FakePostResponse(200, page),
        ]
    )
    monkeypatch.setattr(scraper.requests, "post", post)

    data = scraper._workday_post_with_retry(
        "https://x/wday/cxs/iqvia/IQVIA/jobs", {}, {}, max_attempts=3
    )

    assert data is not None
    assert data["total"] == 68
    # All three attempts were made before the 200 succeeded.
    assert len(post.calls) == 3


def test_post_with_retry_exhausts_retryable_status_returns_none(monkeypatch):
    """Retryable statuses that never recover exhaust to ``None`` (Req 3.7/3.8)."""
    post = _scripted_post([_FakePostResponse(503)])
    monkeypatch.setattr(scraper.requests, "post", post)

    data = scraper._workday_post_with_retry(
        "https://x/wday/cxs/iqvia/IQVIA/jobs", {}, {}, max_attempts=3
    )

    assert data is None
    assert len(post.calls) == 3


def test_post_with_retry_non_retryable_status_raises(monkeypatch):
    """A non-retryable non-200 status raises ``_WorkdayHTTPError`` (Req 3.7)."""
    post = _scripted_post([_FakePostResponse(500)])
    monkeypatch.setattr(scraper.requests, "post", post)

    with pytest.raises(scraper._WorkdayHTTPError) as exc:
        scraper._workday_post_with_retry(
            "https://x/wday/cxs/iqvia/IQVIA/jobs", {}, {}, max_attempts=3
        )

    assert exc.value.status_code == 500
    # Non-retryable: surfaced on the first attempt, no retries.
    assert len(post.calls) == 1


def test_post_with_retry_network_errors_then_none(monkeypatch):
    """Four consecutive failures (network errors) give up with ``None``.

    With ``max_attempts=4`` every attempt raises a ``RequestException``; the
    fetcher retries three times and returns ``None`` on the fourth, which the
    caller records as a truncated/empty outcome (Requirement 3.7/3.8).
    """

    def _boom():
        raise requests.RequestException("connection reset")

    post = _scripted_post([_boom, _boom, _boom, _boom])
    monkeypatch.setattr(scraper.requests, "post", post)

    data = scraper._workday_post_with_retry(
        "https://x/wday/cxs/iqvia/IQVIA/jobs", {}, {}, max_attempts=4
    )

    assert data is None
    assert len(post.calls) == 4


def test_fetch_workday_non_retryable_status_is_http_error(monkeypatch):
    """A non-retryable page status yields an ``http_error`` outcome (Req 3.7)."""
    post = _scripted_post([_FakePostResponse(500)])
    monkeypatch.setattr(scraper.requests, "post", post)

    cfg = SimpleNamespace(WORKDAY_MAX_PAGES=5, WORKDAY_SEARCH_TERMS=[])
    jobs, outcome = scraper._fetch_workday("iqvia|wd1|iqvia", _ctx(cfg))

    assert jobs == []
    assert outcome.status == "http_error"
    assert outcome.http_status == 500


# ============================================================
# Requirement 3.10: "N Locations" handling
# ============================================================


def test_normalize_sets_location_multi_and_derives_from_path():
    """A "N Locations" posting sets ``location_multi`` and derives the location
    from ``externalPath`` (Requirement 3.10)."""
    posting = {
        "title": "Sr Data Science Consultant",
        "externalPath": "/job/So-Paulo-Brazil/Sr-Data-Science-Consultant_R1554072-1",
        "locationsText": "2 Locations",
        "bulletFields": ["R1554072"],
    }

    record = scraper._normalize_workday(
        posting, "iqvia|wd1|iqvia", "iqvia",
        "https://iqvia.wd1.myworkdayjobs.com", "IQVIA",
    )

    assert record["location_multi"] is True
    assert record["location"] == "So Paulo Brazil"


def test_normalize_single_location_is_not_multi():
    """A plain city/country location is stored verbatim and not multi."""
    posting = {
        "title": "Senior Data Scientist",
        "externalPath": "/job/Dublin-Ireland/Senior-Data-Scientist_R1573054",
        "locationsText": "Dublin, Ireland",
        "bulletFields": ["R1573054"],
    }

    record = scraper._normalize_workday(
        posting, "iqvia|wd1|iqvia", "iqvia",
        "https://iqvia.wd1.myworkdayjobs.com", "IQVIA",
    )

    assert record["location_multi"] is False
    assert record["location"] == "Dublin, Ireland"


def test_fetch_workday_marks_multi_location_posting(monkeypatch):
    """End to end, a "N Locations" posting from the fixture is flagged multi."""
    page1 = _load_fixture("list_page1.json")
    empty = {"total": 0, "jobPostings": []}
    post = _scripted_post([_FakePostResponse(200, page1), _FakePostResponse(200, empty)])
    monkeypatch.setattr(scraper.requests, "post", post)

    cfg = SimpleNamespace(WORKDAY_MAX_PAGES=5, WORKDAY_SEARCH_TERMS=[])
    jobs, outcome = scraper._fetch_workday("iqvia|wd1|iqvia", _ctx(cfg))

    multi = [j for j in jobs if j["location_multi"]]
    # "Sr Data Science Consultant" (2 Locations) and
    # "Chief Scientist, Modern Drug Discovery" (8 Locations) are the multi ones.
    assert {j["req_id"] for j in multi} == {"R1554072", "R1570752"}
    # Its location is derived from the path, not the literal "N Locations".
    consultant = next(j for j in multi if j["req_id"] == "R1554072")
    assert "Location" not in consultant["location"]


# ============================================================
# Requirement 3.9: req_id extraction from bulletFields
# ============================================================


def test_fetch_workday_extracts_req_id_from_bullet_fields(monkeypatch):
    """Each posting's ``bulletFields`` req id lands on the Job_Record (Req 3.9)."""
    page1 = _load_fixture("list_page1.json")
    empty = {"total": 0, "jobPostings": []}
    post = _scripted_post([_FakePostResponse(200, page1), _FakePostResponse(200, empty)])
    monkeypatch.setattr(scraper.requests, "post", post)

    cfg = SimpleNamespace(WORKDAY_MAX_PAGES=5, WORKDAY_SEARCH_TERMS=[])
    jobs, outcome = scraper._fetch_workday("iqvia|wd1|iqvia", _ctx(cfg))

    by_title = {j["title"]: j for j in jobs}
    assert by_title["Senior Data Scientist, Epidemiology & RWE Analytics - FSP"]["req_id"] == "R1573054"
    assert by_title["Scientist"]["req_id"] == "R1242206"
    # Every posting in page 1 carries a requisition id.
    assert all(j["req_id"] for j in jobs)


def test_normalize_req_id_none_when_bullet_fields_missing():
    """A posting with no usable ``bulletFields`` gets ``req_id`` None."""
    posting = {
        "title": "No Req",
        "externalPath": "/job/Boston/No-Req",
        "locationsText": "Boston",
        "bulletFields": [],
    }
    record = scraper._normalize_workday(
        posting, "iqvia|wd1|iqvia", "iqvia",
        "https://iqvia.wd1.myworkdayjobs.com", "IQVIA",
    )
    assert record["req_id"] is None


# ============================================================
# Requirement 3.5: empty WORKDAY_SEARCH_TERMS => single empty-searchText query
# ============================================================


def test_empty_search_terms_runs_single_empty_searchtext_query(monkeypatch):
    """With no terms configured, one query runs with ``searchText == ""`` (Req 3.5)."""
    page1 = _load_fixture("list_page1.json")
    empty = {"total": 0, "jobPostings": []}
    post = _scripted_post([_FakePostResponse(200, page1), _FakePostResponse(200, empty)])
    monkeypatch.setattr(scraper.requests, "post", post)

    cfg = SimpleNamespace(WORKDAY_MAX_PAGES=5, WORKDAY_SEARCH_TERMS=[])
    jobs, outcome = scraper._fetch_workday("iqvia|wd1|iqvia", _ctx(cfg))

    # Exactly one searchText value was used, and it was empty.
    search_texts = {c["json"]["searchText"] for c in post.calls}
    assert search_texts == {""}
    assert outcome.status == "ok"
    assert jobs


# ============================================================
# Requirement 18.11: default WORKDAY_SEARCH_TERMS lists the 13 CV terms in order
# ============================================================


def test_default_workday_search_terms_are_the_13_cv_terms_in_order():
    """The shipped default lists the 13 CV terms in the required order (Req 18.11)."""
    assert personal_config.WORKDAY_SEARCH_TERMS == [
        "data scientist",
        "data science",
        "data analyst",
        "analytics",
        "analyst",
        "statistic",
        "biostatistic",
        "epidemiolog",
        "outcomes research",
        "real world",
        "heor",
        "insights",
        "data",
    ]
