"""Offline unit tests for scripts/ats_helpers.py (Requirements 3.9-3.12, 7.17)."""

from datetime import datetime, timezone

import pytest

from ats_helpers import (
    extract_req_id,
    format_workday_slug,
    parse_posted,
    parse_workday_slug,
    validate_workday_slug,
    workday_location_from_path,
)


# --- Workday slug parse / format / validate (Requirements 3.11, 3.12) -------


def test_parse_workday_slug_returns_parts_lowercased():
    assert parse_workday_slug("IQVIA|WD1|IQVIA") == ("iqvia", 1, "iqvia")


def test_parse_workday_slug_high_instance_number():
    assert parse_workday_slug("9dot|wd503|ofl_careers") == ("9dot", 503, "ofl_careers")


def test_parse_workday_slug_round_trip_is_lowercase_original():
    slug = "23andMe|WD5|23"
    tenant, instance, site_id = parse_workday_slug(slug)
    assert format_workday_slug(tenant, instance, site_id) == slug.lower()


def test_format_workday_slug_builds_expected_shape():
    assert format_workday_slug("kohls", 1, "kohlscareers") == "kohls|wd1|kohlscareers"


@pytest.mark.parametrize(
    "slug",
    [
        "iqvia|wd1|iqvia",
        "2020companies|wd1|external_careers",
        "a1group|wd3|a1_jobs-at",
    ],
)
def test_validate_workday_slug_accepts_valid(slug):
    assert validate_workday_slug(slug) is True


@pytest.mark.parametrize(
    "slug",
    [
        "wd1|wd1|iqvia",       # tenant equals wdN segment (Requirement 3.11)
        "wd503|wd503|careers",
        "iqvia|iqvia|iqvia",   # middle segment is not wdN
        "iqvia|wd1",           # missing site id
        "iqvia|wdX|iqvia",     # non-numeric instance
        "iqvia wd1 iqvia",     # no pipe separators
        "",
        None,
    ],
)
def test_validate_workday_slug_rejects_invalid(slug):
    assert validate_workday_slug(slug) is False


def test_parse_workday_slug_returns_none_for_invalid():
    assert parse_workday_slug("wd1|wd1|iqvia") is None
    assert parse_workday_slug(None) is None


# --- Requisition ID extraction (Requirement 3.9) ----------------------------


def test_extract_req_id_finds_first_matching_field():
    assert extract_req_id(["Posted 2 Days Ago", "R1575320"]) == "R1575320"


@pytest.mark.parametrize(
    "fields,expected",
    [
        (["JR-12345"], "JR-12345"),
        (["REQ-000123"], "REQ-000123"),
        (["1234567"], "1234567"),
        (["Full time", "Posted Today"], None),
        ([], None),
        (None, None),
        ("R1575320", None),  # not a list
    ],
)
def test_extract_req_id_cases(fields, expected):
    assert extract_req_id(fields) == expected


# --- Location from externalPath (Requirement 3.10) --------------------------


def test_workday_location_from_path_dashes_to_spaces():
    assert (
        workday_location_from_path("/job/So-Paulo-Brazil/Analyst_R123")
        == "So Paulo Brazil"
    )


def test_workday_location_from_path_single_city():
    assert workday_location_from_path("/job/Boston/Data-Scientist_R9") == "Boston"


@pytest.mark.parametrize("path", ["", None, "/", 123])
def test_workday_location_from_path_returns_none(path):
    assert workday_location_from_path(path) is None


# --- Posting-date parsing (Requirement 7.17) --------------------------------


def test_parse_posted_iso_with_z():
    assert parse_posted("2024-01-02T03:04:05Z") == datetime(
        2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc
    )


def test_parse_posted_iso_with_offset_converts_to_utc():
    assert parse_posted("2024-01-02T03:04:05+02:00") == datetime(
        2024, 1, 2, 1, 4, 5, tzinfo=timezone.utc
    )


def test_parse_posted_iso_without_offset_assumed_utc():
    assert parse_posted("2024-01-02T03:04:05") == datetime(
        2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc
    )


def test_parse_posted_date_only():
    assert parse_posted("2024-01-02") == datetime(
        2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc
    )


def test_parse_posted_epoch_millis_int_and_str_match():
    millis = 1704164645000
    expected = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert parse_posted(millis) == expected
    assert parse_posted(str(millis)) == expected


def test_parse_posted_returns_utc_aware():
    dt = parse_posted("2024-01-02T03:04:05Z")
    assert dt.tzinfo is not None
    assert dt.utcoffset() == timezone.utc.utcoffset(None)


@pytest.mark.parametrize("value", [None, "", "garbage", True, False, [], {}])
def test_parse_posted_returns_none_for_unparseable(value):
    assert parse_posted(value) is None
