"""Shared fixtures for the offline unit suite.

Provides:
- ``FakeSession``: a scripted stand-in for ``requests.Session`` that returns
  canned responses keyed by (method, url) and records every call it receives,
  so fetcher tests run with no network (design section 12).
- ``small_location_maps``: a lookup built from a handful of GeoNames-shaped rows
  instead of the 29 MB ``locations.json``, so location tests run in seconds.
"""

import json
import tempfile
from pathlib import Path

import pytest

import geolocation


# ============================================================
# Fake HTTP session
# ============================================================


class FakeResponse:
    """Minimal stand-in for ``requests.Response``.

    Supports the attributes and methods the fetchers read: ``status_code``,
    ``text``, ``content``, ``headers``, ``json()`` and ``raise_for_status()``.
    """

    def __init__(self, status_code=200, *, json_data=None, text=None,
                 headers=None, content=None):
        self.status_code = status_code
        self._json_data = json_data
        if text is None and json_data is not None:
            text = json.dumps(json_data)
        self.text = text if text is not None else ""
        if content is None:
            content = self.text.encode("utf-8")
        elif isinstance(content, str):
            content = content.encode("utf-8")
        self.content = content
        default_ct = "application/json" if json_data is not None else "text/plain"
        self.headers = {"Content-Type": default_ct}
        if headers:
            self.headers.update(headers)

    def json(self):
        if self._json_data is not None:
            return self._json_data
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.exceptions.HTTPError(
                f"{self.status_code} error", response=self
            )

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"<FakeResponse {self.status_code}>"


class FakeSession:
    """Scripted ``requests.Session`` replacement.

    Register responses with :meth:`add` keyed by (method, url). Each matching
    call pops the next scripted response for that key, so a sequence such as
    503, 503, 200 can be scripted to test retries. The last scripted response
    for a key repeats once exhausted. Every call (method, url, kwargs) is
    appended to :attr:`calls` for assertions.

    A callable may be registered instead of a FakeResponse; it is invoked with
    (method, url, **kwargs) and must return a FakeResponse, which lets tests
    model request-dependent behavior (for example paginated Workday queries).
    """

    def __init__(self):
        # (method, url) -> list of FakeResponse or callables, consumed in order
        self._scripts = {}
        self.calls = []  # list of dicts: {method, url, kwargs}

    def add(self, method, url, response):
        """Append a scripted response (or callable) for (method, url)."""
        key = (method.upper(), url)
        self._scripts.setdefault(key, []).append(response)
        return self

    def add_response(self, method, url, status_code=200, **kwargs):
        """Convenience wrapper that builds and registers a FakeResponse."""
        return self.add(method, url, FakeResponse(status_code, **kwargs))

    def _resolve(self, method, url, kwargs):
        self.calls.append({"method": method.upper(), "url": url, "kwargs": kwargs})
        key = (method.upper(), url)
        scripted = self._scripts.get(key)
        if not scripted:
            raise AssertionError(
                f"FakeSession received an unscripted request: {method.upper()} {url}"
            )
        # Pop the next response; keep the last one so repeats reuse it.
        response = scripted[0] if len(scripted) == 1 else scripted.pop(0)
        if callable(response):
            return response(method.upper(), url, **kwargs)
        return response

    def get(self, url, **kwargs):
        return self._resolve("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._resolve("POST", url, kwargs)

    def request(self, method, url, **kwargs):
        return self._resolve(method, url, kwargs)

    def call_count(self, method=None, url=None):
        """Count recorded calls, optionally filtered by method and/or url."""
        count = 0
        for call in self.calls:
            if method is not None and call["method"] != method.upper():
                continue
            if url is not None and call["url"] != url:
                continue
            count += 1
        return count


@pytest.fixture
def fake_session():
    """A fresh, empty FakeSession for a test to script."""
    return FakeSession()


# ============================================================
# Small location maps
# ============================================================

# A handful of GeoNames-shaped rows, enough to exercise location logic without
# the 29 MB file. Columns match data/locations.json: city, admin, country,
# lat, lng. Admin is the GeoNames admin1 code (two-letter state code for US).
_SMALL_LOCATION_ROWS = [
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

_SMALL_LOCATION_SCHEMA = ["city", "admin", "country", "lat", "lng"]


@pytest.fixture(scope="session")
def small_location_maps():
    """Lookup maps built from a few dozen rows via the real ``build_lookup``.

    Written through a temp file so the returned structure is byte-for-byte the
    same shape the scraper uses at runtime, just small enough to load instantly.
    """
    payload = {"schema": _SMALL_LOCATION_SCHEMA, "data": _SMALL_LOCATION_ROWS}
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as fh:
        json.dump(payload, fh)
        temp_path = fh.name
    try:
        return geolocation.build_lookup(temp_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)
