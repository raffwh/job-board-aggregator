"""Unit tests for the Ashby, Lever, Greenhouse, and BambooHR list fetchers.

Driven by the captured fixtures under ``scripts/tests/fixtures/{ashby,lever,
greenhouse,bamboohr}/`` with no network: every ``requests.get`` is replaced by
a scripted stand-in and the location lookup is stubbed so the 29 MB
``locations.json`` is never touched.

Covered (task 4.4 / Requirements 5.1-5.6):
- Ashby uses the public posting API and stores description, every location,
  native workplace/country, published_at, and jobUrl.
- Lever stores native workplace/country, categories.allLocations, and a
  plain-text description built from descriptionPlain plus the lists.
- Greenhouse stores first_published as published_at and the ats_job_id.
- BambooHR maps locationType -> native_workplace and stores native_country.
- No fetcher truncates the location string (the old ``[:50]`` slice is gone).
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
    """Make ``scraper.requests.get`` return ``response`` for any URL."""
    monkeypatch.setattr(scraper.requests, "get", lambda *a, **k: response)


# ============================================================
# Ashby (Requirement 5.2)
# ============================================================


def test_ashby_uses_public_posting_api_url(monkeypatch):
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        return _Resp(200, _load("ashby", "board.json"))

    monkeypatch.setattr(scraper.requests, "get", fake_get)

    scraper._fetch_ashby("ramp", scraper.default_context())

    assert captured["url"] == "https://api.ashbyhq.com/posting-api/job-board/ramp"


def test_ashby_maps_native_fields_and_all_locations(monkeypatch):
    _patch_get(monkeypatch, _Resp(200, _load("ashby", "board.json")))

    jobs, outcome = scraper._fetch_ashby("ramp", scraper.default_context())

    assert outcome.status == "ok"
    job = jobs[0]  # "Security Engineer, Cloud"
    # Full primary location, untruncated (Requirement 5.1).
    assert job["location"] == "New York, NY (HQ)"
    # Primary + every secondary location (Requirements 5.2, 5.6).
    assert job["locations"] == [
        "New York, NY (HQ)",
        "Remote (Canada)",
        "Remote (US)",
        "Miami, FL",
    ]
    # workplaceType wins over isRemote.
    assert job["native_workplace"] == "Hybrid"
    assert job["native_country"] == "USA"
    assert job["published_at"] == "2026-04-07T17:12:35.753+00:00"
    assert job["url"] == (
        "https://jobs.ashbyhq.com/ramp/34413f8d-26bf-4bbc-8ade-eb309a0e2245"
    )
    assert job["description"].startswith("ABOUT RAMP")
    assert job["ats"] == "Ashby"
    assert job["ats_job_id"] == "34413f8d-26bf-4bbc-8ade-eb309a0e2245"


def test_ashby_falls_back_to_pattern_url_when_no_job_url(monkeypatch):
    board = {"jobs": [{"id": "abc", "title": "X", "location": "Boston, MA"}]}
    _patch_get(monkeypatch, _Resp(200, board))

    jobs, _ = scraper._fetch_ashby("acme", scraper.default_context())

    assert jobs[0]["url"] == "https://jobs.ashbyhq.com/acme/abc"


def test_ashby_workplace_falls_back_to_remote_when_is_remote(monkeypatch):
    board = {
        "jobs": [
            {"id": "1", "title": "X", "location": "Remote", "isRemote": True}
        ]
    }
    _patch_get(monkeypatch, _Resp(200, board))

    jobs, _ = scraper._fetch_ashby("acme", scraper.default_context())

    assert jobs[0]["native_workplace"] == "remote"


def test_ashby_non_200_is_http_error(monkeypatch):
    _patch_get(monkeypatch, _Resp(404, None))

    jobs, outcome = scraper._fetch_ashby("missing", scraper.default_context())

    assert jobs == []
    assert outcome.status == "http_error"
    assert outcome.http_status == 404


# ============================================================
# Lever (Requirement 5.3)
# ============================================================


def test_lever_maps_native_fields_and_all_locations(monkeypatch):
    _patch_get(monkeypatch, _Resp(200, _load("lever", "postings.json")))

    jobs, outcome = scraper._fetch_lever("leverdemo", scraper.default_context())

    assert outcome.status == "ok"
    job = jobs[0]  # "Customer Success Manager"
    assert job["native_workplace"] == "hybrid"
    assert job["native_country"] == "US"
    assert job["location"] == "Atlanta, Georgia"
    assert job["locations"] == ["Atlanta, Georgia"]
    # descriptionPlain plus the plain-text lists (Requirement 5.3).
    assert "This is a test job" in job["description"]
    assert "Skill Set:" in job["description"]
    assert "SaaS" in job["description"]
    # The lists' HTML tags are gone.
    assert "<li>" not in job["description"]
    assert job["ats_job_id"] == "ad208490-4052-4f91-a57e-433f2d1e484b"


def test_lever_remote_posting_keeps_workplace_and_country(monkeypatch):
    _patch_get(monkeypatch, _Resp(200, _load("lever", "postings.json")))

    jobs, _ = scraper._fetch_lever("leverdemo", scraper.default_context())

    sales_eng = next(j for j in jobs if j["title"] == "Sales Engineer")
    assert sales_eng["native_workplace"] == "remote"
    assert sales_eng["native_country"] == "US"


# ============================================================
# Greenhouse (Requirement 5.4)
# ============================================================


def test_greenhouse_uses_first_published_and_ats_job_id(monkeypatch):
    _patch_get(monkeypatch, _Resp(200, _load("greenhouse", "list.json")))

    jobs, outcome = scraper._fetch_greenhouse("greenhouse", scraper.default_context())

    assert outcome.status == "ok"
    job = jobs[0]  # "Commercial Counsel"
    assert job["published_at"] == "2026-08-24T13:52:04-04:00"
    assert job["ats_job_id"] == "8126988"
    # Full location, untruncated and carried in locations.
    assert job["location"] == "Eastern Timezone"
    assert job["locations"] == ["Eastern Timezone"]


def test_greenhouse_published_at_empty_when_no_first_published(monkeypatch):
    data = {"jobs": [{"id": 5, "title": "X", "location": {"name": "Remote"}}]}
    _patch_get(monkeypatch, _Resp(200, data))

    jobs, _ = scraper._fetch_greenhouse("acme", scraper.default_context())

    assert jobs[0]["published_at"] == ""
    assert jobs[0]["ats_job_id"] == "5"


# ============================================================
# BambooHR (Requirement 5.5)
# ============================================================


def _bamboo_response(monkeypatch, data):
    """BambooHR reads via ``requests.get``; script it with a JSON content type."""
    monkeypatch.setattr(scraper.requests, "get", lambda *a, **k: _Resp(200, data))


def test_bamboohr_maps_location_type_to_native_workplace(monkeypatch):
    _bamboo_response(monkeypatch, _load("bamboohr", "list.json"))

    jobs, outcome = scraper._fetch_bamboohr("acme", scraper.default_context())

    assert outcome.status == "ok"
    by_id = {j["ats_job_id"]: j for j in jobs}
    # "0" -> onsite, "1" -> remote, "2" -> hybrid (pinned mapping).
    assert by_id["101"]["native_workplace"] == "onsite"
    assert by_id["102"]["native_workplace"] == "remote"
    assert by_id["103"]["native_workplace"] == "hybrid"
    # native_country comes from atsLocation.country.
    assert by_id["101"]["native_country"] == "United States"
    # Full location string: city, state, country.
    assert by_id["101"]["location"] == "Boston, MA, United States"
    assert by_id["101"]["locations"] == ["Boston, MA, United States"]
    assert by_id["101"]["published_at"] == "2024-05-20"


def test_bamboohr_unknown_location_type_falls_back_to_is_remote(monkeypatch):
    data = {
        "result": [
            {
                "id": 9,
                "jobOpeningName": "X",
                "atsLocation": {"country": "United States"},
                "isRemote": True,
                "locationType": "9",
            }
        ]
    }
    _bamboo_response(monkeypatch, data)

    jobs, _ = scraper._fetch_bamboohr("acme", scraper.default_context())

    assert jobs[0]["native_workplace"] == "remote"


def test_bamboohr_workplace_helper_direct():
    assert scraper._bamboohr_workplace("0", False) == "onsite"
    assert scraper._bamboohr_workplace("1", False) == "remote"
    assert scraper._bamboohr_workplace("2", False) == "hybrid"
    assert scraper._bamboohr_workplace(None, True) == "remote"
    assert scraper._bamboohr_workplace(None, False) is None
    assert scraper._bamboohr_workplace("99", False) is None


# ============================================================
# Requirement 5.1: no fetcher truncates the location string
# ============================================================


def test_no_location_slice_in_any_fetcher(monkeypatch):
    long_loc = "Research Triangle Park, North Carolina, United States of America"

    # Ashby
    _patch_get(monkeypatch, _Resp(200, {"jobs": [
        {"id": "a", "title": "X", "location": long_loc}
    ]}))
    ashby_jobs, _ = scraper._fetch_ashby("acme", scraper.default_context())
    assert ashby_jobs[0]["location"] == long_loc

    # Lever
    _patch_get(monkeypatch, _Resp(200, [
        {"id": "b", "text": "X", "categories": {"location": long_loc,
                                                 "allLocations": [long_loc]}}
    ]))
    lever_jobs, _ = scraper._fetch_lever("acme", scraper.default_context())
    assert lever_jobs[0]["location"] == long_loc

    # Greenhouse
    _patch_get(monkeypatch, _Resp(200, {"jobs": [
        {"id": 1, "title": "X", "location": {"name": long_loc}}
    ]}))
    gh_jobs, _ = scraper._fetch_greenhouse("acme", scraper.default_context())
    assert gh_jobs[0]["location"] == long_loc

    # BambooHR
    _bamboo_response(monkeypatch, {"result": [
        {"id": 1, "jobOpeningName": "X",
         "atsLocation": {"city": "Research Triangle Park",
                         "state": "North Carolina",
                         "country": "United States of America"}}
    ]})
    bamboo_jobs, _ = scraper._fetch_bamboohr("acme", scraper.default_context())
    assert bamboo_jobs[0]["location"] == (
        "Research Triangle Park, North Carolina, United States of America"
    )
