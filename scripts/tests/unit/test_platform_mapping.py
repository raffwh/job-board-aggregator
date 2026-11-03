"""Per-platform field-mapping tests for the Ashby, Lever, Greenhouse, and
BambooHR list fetchers (task 4.6, Requirements 5.2-5.6).

``test_list_fetchers.py`` (task 4.4) already covers the headline mapping for
each platform: the native workplace/country fields, the posting date source,
``ats_job_id``, and the removal of the old ``[:50]`` location slice. This file
fills the gaps that file leaves open, driven by the same captured fixtures and
with no network:

- Multi-location storage (Requirement 5.6) exercised per posting, not just on
  the first job: Ashby postings each keep their own ordered
  ``secondaryLocations``, Lever keeps every ``categories.allLocations`` entry,
  and a posting with no extra locations keeps a single-entry list.
- Native fields read off more than one posting (Requirements 5.2, 5.3, 5.5):
  Lever carries a non-US ``country`` (GB) and an ``onsite`` ``workplaceType``;
  Ashby reads each posting's own ``address`` country; BambooHR builds a
  country-only location string when the city and state are blank.

These cases do not repeat assertions already made in ``test_list_fetchers.py``.
"""

import json
from pathlib import Path

import pytest

import scraper


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def _load(platform, name):
    return json.loads((FIXTURE_DIR / platform / name).read_text(encoding="utf-8"))


class _Resp:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        return self._json


@pytest.fixture(autouse=True)
def _no_network_or_sleep(monkeypatch):
    """Keep every test offline and instant."""
    monkeypatch.setattr(scraper, "enrich_location", lambda loc: (False, None))
    monkeypatch.setattr(scraper.time, "sleep", lambda *a, **k: None)


def _patch_get(monkeypatch, response):
    monkeypatch.setattr(scraper.requests, "get", lambda *a, **k: response)


def _by_title(jobs):
    return {j["title"].strip(): j for j in jobs}


# ============================================================
# Ashby: per-posting multi-location storage (Requirements 5.2, 5.6)
# ============================================================


def test_ashby_each_posting_keeps_its_own_ordered_locations(monkeypatch):
    """Every posting stores its primary plus its own secondary locations, in
    order (Requirement 5.6). The captured board has postings with differing
    secondary lists, so the mapping must not leak one posting's locations into
    another."""
    _patch_get(monkeypatch, _Resp(200, _load("ashby", "board.json")))

    jobs, outcome = scraper._fetch_ashby("ramp", scraper.default_context())

    assert outcome.status == "ok"
    by_title = _by_title(jobs)

    # Three postings share the same HQ primary but differ in their secondaries.
    assert by_title["Mobile Engineer, Android"]["locations"] == [
        "New York, NY (HQ)",
        "Remote (Canada)",
        "San Francisco, CA",
        "Remote (US)",
    ]
    assert by_title["Software Engineer, Frontend"]["locations"] == [
        "New York, NY (HQ)",
        "Remote (Canada)",
        "San Francisco, CA",
        "Remote (US)",
        "Miami, FL",
    ]
    # The primary is always the first stored location.
    for job in jobs:
        assert job["locations"][0] == job["location"]


def test_ashby_posting_without_secondaries_stores_single_location(monkeypatch):
    """A posting with an empty ``secondaryLocations`` keeps a one-entry list,
    not an empty one (Requirement 5.6)."""
    _patch_get(monkeypatch, _Resp(200, _load("ashby", "board.json")))

    jobs, _ = scraper._fetch_ashby("ramp", scraper.default_context())

    credit = _by_title(jobs)["Credit Risk Associate"]
    assert credit["locations"] == ["New York, NY (HQ)"]
    assert credit["location"] == "New York, NY (HQ)"
    # native_country comes from this posting's own address block.
    assert credit["native_country"] == "USA"


# ============================================================
# Lever: non-US country and multi-location storage (Requirements 5.3, 5.6)
# ============================================================


def test_lever_captures_non_us_country_and_onsite_workplace(monkeypatch):
    """The GB posting keeps its native ``country`` and ``onsite``
    ``workplaceType`` untranslated (Requirement 5.3). The list classifier, not
    the fetcher, decides what a non-US country means."""
    _patch_get(monkeypatch, _Resp(200, _load("lever", "postings.json")))

    jobs, _ = scraper._fetch_lever("leverdemo", scraper.default_context())

    gb = _by_title(jobs)["Director of Diversity, Equity, and Inclusion"]
    assert gb["native_country"] == "GB"
    assert gb["native_workplace"] == "onsite"
    assert gb["locations"] == ["London, UK"]


def test_lever_stores_every_all_locations_entry(monkeypatch):
    """A posting with several ``categories.allLocations`` keeps all of them, in
    order (Requirements 5.3, 5.6), with the primary as the first location."""
    _patch_get(monkeypatch, _Resp(200, _load("lever", "postings.json")))

    jobs, _ = scraper._fetch_lever("leverdemo", scraper.default_context())

    multi = _by_title(jobs)["Sales Engineer AH"]
    assert multi["locations"] == ["Atlanta, Georgia", "Arlington, TX", "Boston, MA"]
    assert multi["location"] == "Atlanta, Georgia"
    assert multi["native_workplace"] == "remote"
    assert multi["native_country"] == "US"


# ============================================================
# Greenhouse: first_published read off multiple postings (Requirement 5.4)
# ============================================================


def test_greenhouse_reads_first_published_per_posting(monkeypatch):
    """``first_published`` is the posting date for every job, independent of
    ``updated_at`` (Requirement 5.4). The captured list has postings whose
    ``first_published`` differs from ``updated_at``."""
    _patch_get(monkeypatch, _Resp(200, _load("greenhouse", "list.json")))

    jobs, _ = scraper._fetch_greenhouse("greenhouse", scraper.default_context())

    by_id = {j["ats_job_id"]: j for j in jobs}
    # first_published (2024-08-26) is older than updated_at (2026-10-30).
    sdr = by_id["6180015"]
    assert sdr["published_at"] == "2024-08-26T23:15:34-04:00"
    assert sdr["updated_at"] == "2026-10-30T15:54:05-04:00"
    # Each posting carries its own single, untruncated location.
    assert by_id["8234044"]["locations"] == ["Anywhere in the United States"]


# ============================================================
# BambooHR: country-only location and remote mapping (Requirement 5.5)
# ============================================================


def test_bamboohr_builds_country_only_location(monkeypatch):
    """When the city and state are blank, the location string is just the
    country, with no stray separators, and the posting still maps to its native
    workplace (Requirements 5.1, 5.5)."""
    monkeypatch.setattr(
        scraper.requests, "get", lambda *a, **k: _Resp(200, _load("bamboohr", "list.json"))
    )

    jobs, _ = scraper._fetch_bamboohr("acme", scraper.default_context())

    remote_job = {j["ats_job_id"]: j for j in jobs}["102"]
    assert remote_job["location"] == "United States"
    assert remote_job["locations"] == ["United States"]
    assert remote_job["native_workplace"] == "remote"
    assert remote_job["native_country"] == "United States"
