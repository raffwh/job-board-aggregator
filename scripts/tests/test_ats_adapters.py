"""Adapter tests — no network, requests.get is mocked.
Run: python -m pytest scripts/tests/test_new_ats_adapters.py -v
"""

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import requests, threading, json, random, time, re, os, gzip, argparse, html, logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote
# from geolocation import build_lookup, lookup_location
from requests.adapters import HTTPAdapter
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scraper import (
    fetch_company_jobs_breezy,
    fetch_company_jobs_recruitee,
    fetch_company_jobs_workable
)

COMPANY = {"name": "Acme", "slug": "acme"}


def make_response(payload):
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


@patch("scraper.requests.get")
def test_workable_happy_path(mock_get):
    mock_get.return_value = make_response({"jobs": [{
        "title": "Data Scientist",
        "url": "https://apply.workable.com/acme/j/ABC123/",
        "workplace_type": "remote",
        "created_at": "2026-08-01T10:00:00Z",
        "location": {"city": "Boston", "region": "Massachusetts",
                     "country": "United States"},
    }]})
    jobs = fetch_company_jobs_workable(COMPANY)
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Data Scientist"
    assert jobs[0]["ats"] == "workable"
    assert jobs[0]["remote"] is True
    assert jobs[0]["location"] == "Boston, Massachusetts, United States"


@patch("scraper.requests.get")
def test_workable_url(mock_get):
    mock_get.return_value = make_response({"jobs": []})
    fetch_company_jobs_workable(COMPANY)
    assert mock_get.call_args[0][0] == \
        "https://apply.workable.com/api/v1/widget/accounts/acme"


@patch("scraper.requests.get")
def test_workable_error_returns_empty(mock_get):
    mock_get.side_effect = requests.ConnectionError("dead")
    assert fetch_company_jobs_workable(COMPANY) == []


@patch("scraper.requests.get")
def test_workable_bad_shape_returns_empty(mock_get):
    mock_get.return_value = make_response({"jobs": {"not": "a list"}})
    assert fetch_company_jobs_workable(COMPANY) == []


@patch("scraper.requests.get")
def test_recruitee_happy_path(mock_get):
    mock_get.return_value = make_response({"offers": [{
        "title": "ML Engineer",
        "careers_url": "https://acme.recruitee.com/o/ml-engineer",
        "remote": True,
        "published_at": "2026-08-02T08:00:00Z",
        "location": {"city": "Remote", "state": "", "country": "United States"},
    }]})
    jobs = fetch_company_jobs_recruitee(COMPANY)
    assert len(jobs) == 1
    assert jobs[0]["ats"] == "recruitee"
    assert jobs[0]["remote"] is True
    assert jobs[0]["url"] == "https://acme.recruitee.com/o/ml-engineer"


@patch("scraper.requests.get")
def test_recruitee_url(mock_get):
    mock_get.return_value = make_response({"offers": []})
    fetch_company_jobs_recruitee(COMPANY)
    assert mock_get.call_args[0][0] == "https://acme.recruitee.com/api/offers/"


@patch("scraper.requests.get")
def test_recruitee_error_returns_empty(mock_get):
    mock_get.side_effect = requests.Timeout("slow")
    assert fetch_company_jobs_recruitee(COMPANY) == []


@patch("scraper.requests.get")
def test_breezy_happy_path(mock_get):
    mock_get.return_value = make_response({"positions": [{
        "name": "Analytics Engineer",
        "url": "https://acme.breezy.hr/p/abc123-analytics-engineer",
        "updated_date": "2026-07-30",
        "location": {"city": "Cambridge", "state": "MA", "country": "USA"},
    }]})
    jobs = fetch_company_jobs_breezy(COMPANY)
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Analytics Engineer"
    assert jobs[0]["ats"] == "breezy"
    assert jobs[0]["location"] == "Cambridge, MA, USA"


@patch("scraper.requests.get")
def test_breezy_bare_list_payload(mock_get):
    mock_get.return_value = make_response([{
        "name": "Data Analyst", "url": "", "location": {},
    }])
    jobs = fetch_company_jobs_breezy(COMPANY)
    assert len(jobs) == 1


@patch("scraper.requests.get")
def test_breezy_error_returns_empty(mock_get):
    mock_get.side_effect = requests.HTTPError("404")
    assert fetch_company_jobs_breezy(COMPANY) == []