"""Parametrized location case tests (task 6.12).

A table-driven suite that runs each raw-location case from
``scripts/tests/fixtures/locations_cases.json`` through the real
Location_Classifier (``classify_location``) and Location_Filter
(``apply_location_rule``), plus a few explicit cases for the
``finalize_decision`` Match_Type, the config override, and the
reclassify-after-detail behaviour.

Coordinates resolve through ``small_location_maps`` (the same rows as
``scripts/tests/fixtures/locations_small.json``), so no network and no
29 MB ``locations.json`` are needed.

The fixture cases cover:
  - Greater-Boston onsite/hybrid cities kept primary-eligible (9.11).
  - "Remote - US" / "Remote, United States" kept, scope ``us`` (9.12).
  - San Francisco / New York / Chicago onsite -> ``outside_radius`` (9.13).
  - "Remote, Canada" / "Remote - UK" / "Remote (EMEA)" -> ``non_us`` (9.14).
  - Worcester / Providence / Hartford onsite -> ``location_new_england`` and
    bare "Remote" -> ``remote_unspecified`` (9.17, 9.18), also checked through
    ``finalize_decision`` so the Match_Type is ``maybe``.

The explicit cases cover:
  - Config override with ``SECONDARY_REGION_STATES = set()`` (9.8).
  - Reclassification once detail data supplies ``native_country`` US (8.9).

Validates: Requirements 8.9, 9.8, 9.11, 9.12, 9.13, 9.14, 9.17, 9.18
"""

import json
from pathlib import Path

import pytest

import filters
import location_filter as lf
import personal_config


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "locations_cases.json"
)


def _load_cases():
    with _FIXTURE_PATH.open(encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload["cases"]


_CASES = _load_cases()

BOSTON = (personal_config.BOSTON_CENTER_LAT, personal_config.BOSTON_CENTER_LNG)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verdict_for_case(case, maps, **overrides):
    """Classify a fixture case and run it through the Location_Filter."""
    result = lf.classify_location(
        case["location"],
        native_workplace=case.get("native_workplace"),
        native_country=case.get("native_country"),
        maps=maps,
    )
    return lf.apply_location_rule([result], **overrides)


def _bucket(verdict):
    """Map a LocationVerdict to the fixture's expected_bucket vocabulary."""
    if not verdict.keep:
        return "excluded"
    if verdict.maybe_reason is not None:
        return "maybe"
    return "primary_eligible"


def _finalize_loc(verdict):
    """Adapt a LocationVerdict to the shape ``finalize_decision`` reads.

    ``finalize_decision`` consumes ``kept``, ``exclusion_reason``,
    ``secondary_region``, ``remote_scope`` and ``location_status`` from the
    location verdict, while ``apply_location_rule`` produces ``keep``,
    ``maybe_reason``, ``exclusion_reason``, ``remote_scope`` and ``status``.
    This adapter bridges the two without touching either implementation.
    """
    return {
        "kept": verdict.keep,
        "exclusion_reason": verdict.exclusion_reason,
        "secondary_region": verdict.maybe_reason == "location_new_england",
        "remote_scope": verdict.remote_scope,
        "location_status": (
            verdict.status if verdict.maybe_reason == "location_unknown" else None
        ),
    }


# ---------------------------------------------------------------------------
# Table-driven cases from locations_cases.json (9.11-9.14, 9.17, 9.18)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_location_case(case, small_location_maps):
    """Every fixture case produces the expected keep / bucket / reason."""
    verdict = _verdict_for_case(case, small_location_maps)

    assert verdict.keep is case["expected_keep"], case["name"]
    assert _bucket(verdict) == case["expected_bucket"], case["name"]
    assert verdict.maybe_reason == case["expected_reason"], case["name"]
    assert verdict.exclusion_reason == case["expected_exclusion"], case["name"]
    assert verdict.remote_scope == case["remote_scope"], case["name"]


# ---------------------------------------------------------------------------
# finalize_decision Match_Type for the maybe cases (9.17, 9.18)
# ---------------------------------------------------------------------------

_MAYBE_CASE_NAMES = (
    "worcester_ma_onsite",
    "providence_ri_onsite",
    "hartford_ct_onsite",
    "bare_remote",
)

_MAYBE_CASES = [c for c in _CASES if c["name"] in _MAYBE_CASE_NAMES]


@pytest.mark.parametrize(
    "case", _MAYBE_CASES, ids=[c["name"] for c in _MAYBE_CASES]
)
def test_maybe_case_match_type_through_finalize_decision(case, small_location_maps):
    """A primary-title job on a New England / bare-remote location is a maybe.

    Worcester / Providence / Hartford yield ``location_new_england`` and bare
    "Remote" yields ``remote_unspecified``; both resolve to Match_Type
    ``maybe`` through ``finalize_decision`` (Requirements 9.17, 9.18).
    """
    verdict = _verdict_for_case(case, small_location_maps)
    job = {"title": "Data Scientist"}

    decision = filters.finalize_decision(
        job,
        title_class="primary",
        tier=None,
        loc_result=_finalize_loc(verdict),
    )

    assert decision.match_type == "maybe", case["name"]
    assert decision.match_reason == case["expected_reason"], case["name"]
    assert decision.exclusion_reason is None, case["name"]


# ---------------------------------------------------------------------------
# Config override: SECONDARY_REGION_STATES = set() (9.8)
# ---------------------------------------------------------------------------


def test_config_override_disables_new_england(small_location_maps):
    """With the Secondary_Region disabled, Worcester falls to outside_radius.

    Passing an explicit center, radius, and an empty
    ``secondary_region_states`` overrides the ``personal_config`` defaults
    (Requirement 9.8). Worcester (New England, outside the radius) is then no
    longer a maybe; it is excluded as ``outside_radius``.
    """
    worcester = lf.classify_location(
        "Worcester, MA", native_workplace="onsite", maps=small_location_maps
    )

    # Default config keeps Worcester as a New England maybe.
    kept = lf.apply_location_rule([worcester])
    assert kept.keep is True
    assert kept.maybe_reason == "location_new_england"

    # Override with the region disabled -> excluded, outside_radius.
    overridden = lf.apply_location_rule(
        [worcester],
        center=BOSTON,
        radius_miles=personal_config.BOSTON_RADIUS_MILES,
        secondary_region_states=set(),
    )
    assert overridden.keep is False
    assert overridden.exclusion_reason == "outside_radius"


# ---------------------------------------------------------------------------
# Reclassification after detail data (8.9)
# ---------------------------------------------------------------------------


def test_reclassify_bare_remote_gains_us_country(small_location_maps):
    """A bare-remote job reclassifies to primary-eligible once it learns US.

    Before detail enrichment the job is bare "Remote" with no country, so it
    is a ``remote_unspecified`` maybe. After detail data supplies a US
    ``native_country``, re-running the classifier makes it a clean
    primary-eligible US-remote job (Requirement 8.9).
    """
    before = lf.classify_location(
        "Remote", native_workplace="remote", native_country=None,
        maps=small_location_maps,
    )
    before_verdict = lf.apply_location_rule([before])
    assert before_verdict.keep is True
    assert before_verdict.maybe_reason == "remote_unspecified"
    assert before_verdict.remote_scope == "unspecified"

    # Detail enrichment supplies the country; re-classify the same location.
    after = lf.classify_location(
        "Remote", native_workplace="remote", native_country="United States",
        maps=small_location_maps,
    )
    after_verdict = lf.apply_location_rule([after])
    assert after_verdict.keep is True
    assert after_verdict.maybe_reason is None
    assert after_verdict.remote_scope == "us"
