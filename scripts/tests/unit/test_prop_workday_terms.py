"""Property-based test for Workday multi-term deduplication.

Property 3: Workday multi-term results are a deduplicated union.

For any list of search terms whose result sets are drawn from a shared pool of
postings with requisition IDs, the Workday Fetcher returns one Job_Record per
distinct requisition ID across all terms, and returns no posting that appeared
in no term's results.

The test builds a fake Workday tenant whose POST response for each ``searchText``
term returns a configurable set of postings from a shared pool, with overlaps
across terms. ``requests.post`` is patched, so no network is used. The fetcher
runs with ``WORKDAY_SEARCH_TERMS`` set to the generated terms, and the result is
asserted to be exactly the deduplicated union of all terms' postings keyed by
requisition ID (or ``externalPath`` when a posting carries no requisition ID).

Validates: Requirements 3.4
"""

from types import SimpleNamespace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import scraper
from diagnostics import DiagnosticsCollector, RunContext

# The Workday API returns one page of up to this many postings per request.
PAGE_LIMIT = scraper._WORKDAY_PAGE_LIMIT

# A fixed tenant whose API URL the fake session answers for. The slug must be a
# valid Workday_Slug: "<tenant>|wd<instance>|<site_id>".
_SLUG = "acme|wd1|acmecareers"
_API_URL = "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/acmecareers/jobs"


def _posting(index):
    """Build one pool posting with a unique requisition ID and externalPath.

    ``bulletFields`` carries the requisition ID in the shape ``extract_req_id``
    recognizes (``^[A-Z]{0,4}-?\\d{3,}[\\w-]*$``), so the fetcher dedupes on it.
    """
    req_id = f"R{1000 + index}"
    return {
        "title": f"Job {index}",
        "externalPath": f"/job/Boston/Job-{index}_{req_id}",
        "locationsText": "Boston, MA",
        "postedOn": "Posted Today",
        "bulletFields": [req_id],
    }


def _req_id_of(posting):
    """The dedup key the fetcher will use for a posting: req_id else externalPath."""
    req_id = scraper.extract_req_id(posting.get("bulletFields"))
    return req_id or posting.get("externalPath")


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# A pool large enough to span several pages so pagination and dedup interact.
_POOL_SIZE = 45


@st.composite
def term_assignments(draw):
    """Draw a list of (term, posting-index-set) pairs over a shared pool.

    Every term maps to a subset of indices into a fixed pool of ``_POOL_SIZE``
    postings. Subsets overlap freely (a posting may appear under many terms or
    none), which is the case the dedup must collapse. Returns the pool and the
    per-term index subsets.
    """
    pool = [_posting(i) for i in range(_POOL_SIZE)]
    n_terms = draw(st.integers(min_value=1, max_value=5))
    indices = st.sets(st.integers(min_value=0, max_value=_POOL_SIZE - 1))
    subsets = [draw(indices) for _ in range(n_terms)]
    # Distinct, non-empty search terms so each maps to its own scripted result.
    terms = [f"term{i}" for i in range(n_terms)]
    return pool, terms, subsets


# ---------------------------------------------------------------------------
# Fake Workday tenant
# ---------------------------------------------------------------------------


def _make_fake_post(pool, term_to_indices):
    """Return a ``requests.post`` replacement for one fake Workday tenant.

    The returned function reads ``searchText`` and ``offset`` from the JSON
    payload and serves that term's assigned postings one page at a time. Page 1
    reports the term's ``total``; later pages report 0 (the real API quirk the
    pagination fix handles). An unknown term yields an empty result.
    """

    class _Resp:
        def __init__(self, body):
            self.status_code = 200
            self._body = body

        def json(self):
            return self._body

        def raise_for_status(self):
            return None

    def fake_post(url, json=None, headers=None, timeout=None, **kwargs):
        assert url == _API_URL, f"unexpected Workday URL: {url}"
        payload = json or {}
        term = payload.get("searchText", "")
        offset = payload.get("offset", 0)
        indices = sorted(term_to_indices.get(term, set()))
        postings = [pool[i] for i in indices]
        page = postings[offset : offset + PAGE_LIMIT]
        body = {"jobPostings": page}
        # Workday reports the total only on the first page (offset 0).
        if offset == 0:
            body["total"] = len(postings)
        return _Resp(body)

    return fake_post


def _run_fetch(terms, pool, term_to_indices):
    """Run ``_fetch_workday`` for the fake tenant with the given search terms.

    All patches are applied inside a ``MonkeyPatch`` context so they are set up
    and torn down per generated Hypothesis input (a function-scoped ``monkeypatch``
    fixture is not reset between ``@given`` examples).
    """
    config = SimpleNamespace(WORKDAY_SEARCH_TERMS=terms, WORKDAY_MAX_PAGES=25)
    ctx = RunContext(diag=DiagnosticsCollector(), config=config, scoped=True)
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(scraper.requests, "post", _make_fake_post(pool, term_to_indices))
        # Location resolution is irrelevant to dedup and would load the 29 MB
        # locations.json; stub it so the property test stays offline and fast.
        mp.setattr(scraper, "enrich_location", lambda loc: (False, None))
        # No jitter sleeps during the test.
        mp.setattr(scraper.time, "sleep", lambda *a, **k: None)
        return scraper._fetch_workday(_SLUG, ctx)


# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(data=term_assignments())
def test_multi_term_result_is_deduplicated_union(data):
    pool, terms, subsets = data
    term_to_indices = {term: subset for term, subset in zip(terms, subsets)}

    jobs, outcome = _run_fetch(terms, pool, term_to_indices)

    # Expected: the set of dedup keys for every posting that appeared under at
    # least one term. The union of all per-term index sets.
    union_indices = set().union(*subsets) if subsets else set()
    expected_keys = {_req_id_of(pool[i]) for i in union_indices}
    # Each returned record exposes req_id; fall back to the externalPath-derived
    # key (``workday_external_path``) when a posting carried no requisition ID.
    returned_keys = [
        job.get("req_id") or job.get("workday_external_path") for job in jobs
    ]

    # One Job_Record per distinct requisition ID across all terms (the union).
    assert set(returned_keys) == expected_keys
    # No duplicates: the result count equals the number of distinct keys.
    assert len(returned_keys) == len(expected_keys)
    assert len(returned_keys) == len(set(returned_keys))
    # No posting is returned that appeared in no term's results.
    pool_absent = {_req_id_of(pool[i]) for i in range(_POOL_SIZE)} - expected_keys
    assert pool_absent.isdisjoint(set(returned_keys))


@settings(max_examples=50, deadline=None)
@given(
    shared=st.integers(min_value=1, max_value=PAGE_LIMIT),
    extra_a=st.integers(min_value=0, max_value=PAGE_LIMIT),
    extra_b=st.integers(min_value=0, max_value=PAGE_LIMIT),
)
def test_overlapping_terms_collapse_shared_postings(shared, extra_a, extra_b):
    """A targeted case: two terms sharing ``shared`` postings return the union
    exactly once each, never the shared postings twice (3.4)."""
    total = shared + extra_a + extra_b
    if total > _POOL_SIZE:
        shared = min(shared, _POOL_SIZE)
        extra_a = min(extra_a, _POOL_SIZE - shared)
        extra_b = min(extra_b, _POOL_SIZE - shared - extra_a)
        total = shared + extra_a + extra_b

    pool = [_posting(i) for i in range(_POOL_SIZE)]
    shared_idx = set(range(shared))
    a_only = set(range(shared, shared + extra_a))
    b_only = set(range(shared + extra_a, shared + extra_a + extra_b))

    terms = ["alpha", "beta"]
    term_to_indices = {"alpha": shared_idx | a_only, "beta": shared_idx | b_only}

    jobs, outcome = _run_fetch(terms, pool, term_to_indices)

    expected_keys = {_req_id_of(pool[i]) for i in (shared_idx | a_only | b_only)}
    returned_keys = [job.get("req_id") or job.get("workday_external_path")
                     for job in jobs]

    assert set(returned_keys) == expected_keys
    assert len(returned_keys) == len(set(returned_keys))
    assert len(returned_keys) == shared + extra_a + extra_b
