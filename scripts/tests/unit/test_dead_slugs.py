"""Tests for Dead_Slug_Cache safety (Requirement 6).

Covers:

- Property 7 (Only permanent failures enter the dead-slug cache): for any
  FetchOutcome, ``should_cache_dead`` is true exactly when the status is
  ``http_error`` with an HTTP status of 404 or 410, and false for every other
  status or HTTP code. Validates Requirements 6.3, 6.4.
- Unit tests for the versioned cache file format: a legacy plain-list file is
  ignored on load, a version-mismatch file is ignored, a matching-version file
  is loaded, and ``save_dead_slugs`` / ``load_dead_slugs`` round-trip in the
  versioned format. Validates Requirements 6.1, 6.2.
"""

import json
import os

from hypothesis import given, settings
from hypothesis import strategies as st

import scraper
from diagnostics import FETCH_STATUSES, FetchOutcome


# ---------------------------------------------------------------------------
# Property 7: Only permanent failures enter the dead-slug cache
# ---------------------------------------------------------------------------

# HTTP codes span the permanent-failure pair (404, 410) and a spread of other
# codes so the generator exercises both the true and false sides of the rule.
http_status = st.one_of(
    st.none(),
    st.sampled_from([200, 204, 301, 400, 401, 403, 404, 410, 429, 500, 502, 503]),
    st.integers(min_value=100, max_value=599),
)


@st.composite
def fetch_outcomes(draw):
    """An arbitrary FetchOutcome across all statuses and HTTP codes."""
    status = draw(st.sampled_from(list(FETCH_STATUSES)))
    return FetchOutcome(
        platform=draw(st.sampled_from(
            ["workday", "icims", "ashby", "lever", "greenhouse", "bamboohr"]
        )),
        slug=draw(st.text(min_size=1, max_size=20)),
        status=status,
        http_status=draw(http_status),
    )


@settings(max_examples=300)
@given(outcome=fetch_outcomes())
def test_should_cache_dead_matches_permanent_failure_rule(outcome):
    """should_cache_dead is True iff http_error with a 404/410 HTTP status.

    **Validates: Requirements 6.3, 6.4**
    """
    expected = outcome.status == "http_error" and outcome.http_status in (404, 410)
    assert scraper.should_cache_dead(outcome) is expected


def test_should_cache_dead_none_outcome_is_false():
    """A missing outcome is never cached."""
    assert scraper.should_cache_dead(None) is False


def test_should_cache_dead_true_for_404_and_410():
    """Both permanent HTTP codes cache the slug."""
    for code in (404, 410):
        outcome = FetchOutcome(
            platform="icims", slug="acme", status="http_error", http_status=code
        )
        assert scraper.should_cache_dead(outcome) is True


def test_should_cache_dead_false_for_non_permanent_statuses():
    """blocked, exception, invalid_slug, empty, and ok are never cached."""
    for status in ("ok", "empty", "blocked", "exception", "invalid_slug"):
        outcome = FetchOutcome(
            platform="icims", slug="acme", status=status, http_status=None
        )
        assert scraper.should_cache_dead(outcome) is False


def test_should_cache_dead_false_for_other_http_errors():
    """An http_error with a non-404/410 code (e.g. 403, 500) is not cached."""
    for code in (400, 401, 403, 429, 500, 502, 503):
        outcome = FetchOutcome(
            platform="icims", slug="acme", status="http_error", http_status=code
        )
        assert scraper.should_cache_dead(outcome) is False


# ---------------------------------------------------------------------------
# Versioned cache file format (Requirements 6.1, 6.2)
# ---------------------------------------------------------------------------

def _cache_path(tmp_path, platform):
    return os.path.join(str(tmp_path), f"{platform}.json")


def test_legacy_plain_list_file_is_ignored_on_load(tmp_path, monkeypatch):
    """A legacy plain-list cache (no version) is ignored (Requirement 6.2)."""
    monkeypatch.setattr(scraper, "DEAD_SLUG_DIR", str(tmp_path))
    with open(_cache_path(tmp_path, "icims"), "w", encoding="utf-8") as f:
        json.dump(["acme", "globex", "initech"], f)

    assert scraper.load_dead_slugs("icims") == set()
    assert scraper.dead_slug_file_is_current("icims") is False


def test_version_mismatch_file_is_ignored_on_load(tmp_path, monkeypatch):
    """A cache whose stored version differs from current is ignored (6.2)."""
    monkeypatch.setattr(scraper, "DEAD_SLUG_DIR", str(tmp_path))
    current = scraper.dead_slug_version("icims")
    stale = "0" if current != "0" else "99"
    with open(_cache_path(tmp_path, "icims"), "w", encoding="utf-8") as f:
        json.dump({"version": stale, "slugs": ["acme", "globex"]}, f)

    assert scraper.load_dead_slugs("icims") == set()
    assert scraper.dead_slug_file_is_current("icims") is False


def test_matching_version_file_is_loaded(tmp_path, monkeypatch):
    """A cache stored at the current version is loaded (Requirement 6.1)."""
    monkeypatch.setattr(scraper, "DEAD_SLUG_DIR", str(tmp_path))
    version = scraper.dead_slug_version("icims")
    with open(_cache_path(tmp_path, "icims"), "w", encoding="utf-8") as f:
        json.dump({"version": version, "slugs": ["acme", "globex"]}, f)

    assert scraper.load_dead_slugs("icims") == {"acme", "globex"}
    assert scraper.dead_slug_file_is_current("icims") is True


def test_save_load_round_trips_in_versioned_format(tmp_path, monkeypatch):
    """save_dead_slugs writes the versioned format load_dead_slugs reads (6.1)."""
    monkeypatch.setattr(scraper, "DEAD_SLUG_DIR", str(tmp_path))
    slugs = {"acme", "globex", "initech"}

    scraper.save_dead_slugs("workday", slugs)

    # On-disk payload is the versioned dict, not a bare list.
    with open(_cache_path(tmp_path, "workday"), "r", encoding="utf-8") as f:
        payload = json.load(f)
    assert isinstance(payload, dict)
    assert str(payload["version"]) == scraper.dead_slug_version("workday")
    assert set(payload["slugs"]) == slugs

    # And it round-trips back through the loader.
    assert scraper.load_dead_slugs("workday") == slugs
    assert scraper.dead_slug_file_is_current("workday") is True


def test_missing_cache_file_loads_empty(tmp_path, monkeypatch):
    """No cache file yields an empty set and is not current."""
    monkeypatch.setattr(scraper, "DEAD_SLUG_DIR", str(tmp_path))
    assert scraper.load_dead_slugs("lever") == set()
    assert scraper.dead_slug_file_is_current("lever") is False
