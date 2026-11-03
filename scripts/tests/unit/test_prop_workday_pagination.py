"""Property 2: Workday pagination is complete and truncation is exact.

For any fake Workday tenant with posting total T (0 <= T <= 2,000), where only
page 1 reports T and later pages report 0 (or omit the total), any page cap M
(1 <= M <= 30), and an optional failing page index F (a page that fails every
retry), the Workday Fetcher returns exactly ``min(T, 20*M, 20*(F-1))`` distinct
postings -- the ``20*(F-1)`` term is dropped when no page fails. ``truncated``
is true exactly when that number is less than T.

This exercises the real ``_fetch_workday``/``_workday_post_with_retry`` paths in
``scraper`` by patching ``requests.post`` (via ``scraper.requests``) to a fake
tenant. No network is used.

Validates: Requirements 3.1, 3.2, 3.3, 3.6, 3.8, 1.4

# Feature: job-aggregator-improvements, Property 2: Workday pagination is complete and truncation is exact
"""

import types

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import scraper
from diagnostics import DiagnosticsCollector, RunContext


# The fetcher's page size is fixed at 20 (scraper._WORKDAY_PAGE_LIMIT).
PAGE = 20

# A single, valid Workday slug. The tenant is not a ``wdN`` segment, so it
# parses cleanly (Requirement 3.11/3.12).
SLUG = "acme|wd1|careers"


# ============================================================
# Fake Workday tenant
# ============================================================


class _FakePost:
    """A callable stand-in for ``requests.post`` modelling one Workday tenant.

    The tenant serves ``total`` postings in pages of 20. Page 1 reports the
    real total; every later page reports ``total: 0`` (Requirement 3.2 -- a
    later page reporting 0 must not stop pagination). Each posting carries a
    globally unique requisition id so dedup never collapses distinct postings.

    When ``fail_page`` is set (1-indexed over the sequence of page requests),
    that page and every retry of it return HTTP 503, so
    ``_workday_post_with_retry`` exhausts its retries and returns ``None`` --
    the partial-failure path (Requirement 3.8). Pages before the failing one
    succeed; the failing page stops the query.
    """

    def __init__(self, total, fail_page=None):
        self.total = total
        self.fail_page = fail_page
        self.page_requests = 0  # distinct pages requested (by offset)
        self._offsets_seen = set()

    def __call__(self, url, json=None, headers=None, timeout=None, **kwargs):
        offset = json.get("offset", 0)
        page_index = offset // PAGE  # 0-based page index
        # Count only the first time we see an offset (retries reuse it).
        if offset not in self._offsets_seen:
            self._offsets_seen.add(offset)
            self.page_requests += 1

        # 1-indexed page number over the request sequence.
        page_number = page_index + 1

        if self.fail_page is not None and page_number == self.fail_page:
            return _FakeResponse(503, {})

        start = offset
        end = min(offset + PAGE, self.total)
        postings = []
        for i in range(start, end):
            postings.append(
                {
                    "title": f"Job {i}",
                    "externalPath": f"/job/Boston/Job-{i}_R{i:06d}",
                    "locationsText": "Boston, MA",
                    "bulletFields": [f"R{i:06d}"],
                    "postedOn": "Posted Today",
                }
            )

        body = {"jobPostings": postings}
        # Page 1 reports the real total; later pages report 0 (3.1, 3.2).
        body["total"] = self.total if page_index == 0 else 0
        return _FakeResponse(200, body)


class _FakeResponse:
    def __init__(self, status_code, json_body):
        self.status_code = status_code
        self._json = json_body

    def json(self):
        return self._json


# ============================================================
# Harness
# ============================================================


def _run(monkeypatch, total, max_pages, fail_page=None):
    """Run ``_fetch_workday`` against a fake tenant and return (jobs, outcome)."""
    fake = _FakePost(total, fail_page=fail_page)
    # Patch the module-level ``requests`` the fetcher POSTs through.
    monkeypatch.setattr(scraper.requests, "post", fake)
    # Keep location lookups offline and instant: never touch locations.json.
    monkeypatch.setattr(scraper, "enrich_location", lambda loc: (False, None))
    # No inter-page sleeping, so the property runs fast.
    monkeypatch.setattr(scraper.time, "sleep", lambda *a, **k: None)

    # A config stand-in with a single search term for determinism.
    config = types.SimpleNamespace(
        WORKDAY_MAX_PAGES=max_pages,
        WORKDAY_SEARCH_TERMS=["data scientist"],
    )
    ctx = RunContext(diag=DiagnosticsCollector(), config=config, scoped=True)

    jobs, outcome = scraper._fetch_workday(SLUG, ctx)
    return jobs, outcome, fake


def _expected(total, max_pages, fail_page):
    """The exact count from the design: min(T, 20*M, 20*(F-1))."""
    bound = min(total, PAGE * max_pages)
    if fail_page is not None:
        bound = min(bound, PAGE * (fail_page - 1))
    return bound


# ============================================================
# Property 2
# ============================================================


@st.composite
def _scenario(draw):
    """A (total, max_pages, fail_page) scenario.

    ``fail_page``, when present, is constrained to a page the fetcher would
    actually request: ``1 <= F <= ceil(T/20)`` (at least 1 when T is 0, since
    page 1 is always requested). This keeps F a page that exists in the result
    stream, matching the design's intent for the ``20*(F-1)`` term. Failing a
    page past the natural end of the data is a separate, uninteresting case.
    """
    total = draw(st.integers(min_value=0, max_value=2000))
    max_pages = draw(st.integers(min_value=1, max_value=30))
    data_pages = -(-total // PAGE)  # ceil division: pages that carry postings
    if data_pages == 0:
        # An empty tenant has no page worth failing; the always-requested page
        # 1 carries no data, so a "partial failure" is not meaningful here.
        fail_page = None
    else:
        fail_page = draw(
            st.one_of(
                st.none(),
                st.integers(min_value=1, max_value=data_pages),
            )
        )
    return total, max_pages, fail_page


@given(_scenario())
@settings(max_examples=250, deadline=None)
def test_pagination_count_and_truncation_are_exact(scenario):
    """Fetched count equals min(T, 20M, 20(F-1)); truncated iff count < T."""
    total, max_pages, fail_page = scenario

    class _MonkeyPatch:
        # A tiny monkeypatch context so the property can patch/restore itself
        # without pytest's function-scoped fixture (which cannot drive @given).
        def __init__(self):
            self._undo = []

        def setattr(self, target, name, value):
            old = getattr(target, name)
            self._undo.append((target, name, old))
            setattr(target, name, value)

        def undo(self):
            for target, name, old in reversed(self._undo):
                setattr(target, name, old)

    mp = _MonkeyPatch()
    try:
        jobs, outcome, fake = _run(mp, total, max_pages, fail_page)
    finally:
        mp.undo()

    expected = _expected(total, max_pages, fail_page)

    # Exactly the expected number of distinct postings (3.1, 3.3, 3.6, 3.8).
    assert len(jobs) == expected
    assert outcome.fetched == expected
    # Distinct requisition ids: no duplicates leaked through.
    assert len({j["req_id"] for j in jobs}) == expected

    # Truncation is exact: true iff fewer than T postings came back (1.4).
    assert outcome.truncated is (expected < total)


# ============================================================
# Targeted examples pinning each clause of the property
# ============================================================


def test_no_truncation_when_total_fits(monkeypatch):
    """T <= 20*M with no failure: all T fetched, not truncated (3.1, 3.3)."""
    jobs, outcome, _ = _run(monkeypatch, total=55, max_pages=10)
    assert len(jobs) == 55
    assert outcome.truncated is False
    assert outcome.status == "ok"


def test_later_pages_reporting_zero_do_not_stop_early(monkeypatch):
    """Pages 2+ report total 0 yet pagination continues to the real end (3.2)."""
    # 3 full pages of 20 -> 60 postings; later pages report total 0.
    jobs, outcome, fake = _run(monkeypatch, total=60, max_pages=25)
    assert len(jobs) == 60
    assert outcome.truncated is False
    # More than one page was actually requested (the old bug stopped at 20).
    assert fake.page_requests >= 3
    assert len(jobs) > PAGE


def test_page_cap_truncates_exactly(monkeypatch):
    """T > 20*M: exactly 20*M fetched and truncated is true (3.6, 1.4)."""
    # total 200, cap 3 pages -> 60 fetched, truncated.
    jobs, outcome, _ = _run(monkeypatch, total=200, max_pages=3)
    assert len(jobs) == 60
    assert outcome.truncated is True


def test_failing_page_keeps_partial_and_truncates(monkeypatch):
    """A page that fails every retry keeps prior pages and marks truncated (3.8)."""
    # Page 3 fails -> pages 1 and 2 kept -> 40 postings, truncated.
    jobs, outcome, _ = _run(monkeypatch, total=200, max_pages=25, fail_page=3)
    assert len(jobs) == 40
    assert outcome.truncated is True


def test_failing_first_page_yields_nothing(monkeypatch):
    """When page 1 fails every retry, no postings and truncated stays false.

    With zero postings fetched the query produced nothing to truncate, so the
    count is 0 == min(T, .., 20*(1-1)) and truncated is false only when T is 0;
    here T>0 so expected is 0 < T and truncated is true per the property.
    """
    jobs, outcome, _ = _run(monkeypatch, total=50, max_pages=25, fail_page=1)
    assert len(jobs) == 0
    # expected = min(50, 500, 0) = 0 < 50 -> truncated true.
    assert outcome.truncated is True


def test_empty_tenant(monkeypatch):
    """T == 0: no postings, not truncated, status empty (3.3)."""
    jobs, outcome, _ = _run(monkeypatch, total=0, max_pages=25)
    assert jobs == []
    assert outcome.truncated is False
    assert outcome.status == "empty"
