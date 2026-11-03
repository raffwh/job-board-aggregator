"""Property-based test that fetchers never truncate the location string.

Property 6: Location strings are stored untruncated.

For any location string of length 0 to 500 placed in a captured Greenhouse,
Lever, Ashby, or BambooHR response, the corresponding normalizer stores a
``location`` equal to the source string (and carries that same full string in
``locations``), never clipping it to 50 characters as the old ``[:50]`` slice
did.

Each list fetcher is driven offline: ``scraper.requests.get`` is replaced with
a scripted stand-in that returns the generated response, and
``scraper.enrich_location`` is patched so the 29 MB ``locations.json`` is never
loaded. Patching is done with ``mock.patch`` context managers inside each test
body (not a function-scoped fixture), so it is applied cleanly for every input
Hypothesis generates.

Validates: Requirements 5.1
"""

from contextlib import contextmanager
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

import scraper


# ---------------------------------------------------------------------------
# Generator: non-empty location strings, deliberately spanning past 50 chars
# ---------------------------------------------------------------------------

# A mix of plausible location characters: letters, digits, spaces, commas,
# parentheses, hyphens and slashes, the kinds of things real postings carry
# ("Research Triangle Park, North Carolina, United States of America").
_LOC_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    " ,.()-/"
)

# Length 51..500 so every generated string is strictly longer than the old
# 50-char cap; a fetcher that still truncated would fail immediately.
_long_locations = st.text(alphabet=_LOC_ALPHABET, min_size=51, max_size=500)


class _Resp:
    """Minimal ``requests.Response`` stand-in with a JSON content type."""

    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        return self._json


@contextmanager
def _offline(response):
    """Patch the fetchers offline and return ``response`` from any GET.

    Applied inside each test body (not via a function-scoped fixture) so it is
    set up and torn down cleanly for every Hypothesis example. ``enrich_location``
    is stubbed so ``locations.json`` is never loaded, and ``time.sleep`` is a
    no-op so the jitter in the Ashby/BambooHR fetchers is instant.
    """
    with mock.patch.object(scraper, "enrich_location", lambda loc: (False, None)), \
            mock.patch.object(scraper.time, "sleep", lambda *a, **k: None), \
            mock.patch.object(scraper.requests, "get",
                              lambda *a, **k: response):
        yield


# ---------------------------------------------------------------------------
# One property per platform. Each places the generated string as the posting's
# sole location and asserts the stored location is byte-for-byte identical.
# ---------------------------------------------------------------------------


@settings(max_examples=200)
@given(location=_long_locations)
def test_greenhouse_stores_location_untruncated(location):
    response = _Resp(200, {"jobs": [{"id": 1, "title": "X",
                                     "location": {"name": location}}]})
    with _offline(response):
        jobs, _ = scraper._fetch_greenhouse("acme", scraper.default_context())

    assert jobs[0]["location"] == location
    assert jobs[0]["locations"] == [location]


@settings(max_examples=200)
@given(location=_long_locations)
def test_lever_stores_location_untruncated(location):
    response = _Resp(200, [{"id": "b", "text": "X",
                            "categories": {"location": location,
                                           "allLocations": [location]}}])
    with _offline(response):
        jobs, _ = scraper._fetch_lever("acme", scraper.default_context())

    assert jobs[0]["location"] == location
    assert jobs[0]["locations"] == [location]


@settings(max_examples=200)
@given(location=_long_locations)
def test_ashby_stores_location_untruncated(location):
    response = _Resp(200, {"jobs": [{"id": "a", "title": "X",
                                     "location": location}]})
    with _offline(response):
        jobs, _ = scraper._fetch_ashby("acme", scraper.default_context())

    assert jobs[0]["location"] == location
    assert jobs[0]["locations"] == [location]


@settings(max_examples=200)
@given(location=_long_locations)
def test_bamboohr_stores_location_untruncated(location):
    # BambooHR joins city/state/country; placing the whole string in ``city``
    # (state/country empty) makes the assembled location equal the source.
    response = _Resp(200, {"result": [{"id": 1, "jobOpeningName": "X",
                                       "atsLocation": {"city": location}}]})
    with _offline(response):
        jobs, _ = scraper._fetch_bamboohr("acme", scraper.default_context())

    assert jobs[0]["location"] == location
    assert jobs[0]["locations"] == [location]
