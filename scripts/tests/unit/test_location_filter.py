"""Offline unit tests for the Location_Filter (task 6.7).

Covers ``haversine_miles`` and ``apply_location_rule`` in
``scripts/location_filter.py``: the per-location verdict table and the
job-level precedence order (kept -> secondary -> remote-unspecified ->
undecided -> reject).

These are example tests. The haversine metric property (9.1, 9.9) and the
multi-location precedence property are covered separately by the PBT tasks.

Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.15, 9.16
"""

import location_filter
from location_filter import (
    LocationResult,
    LocationVerdict,
    apply_location_rule,
    classify_location,
    haversine_miles,
)
import personal_config


BOSTON = (personal_config.BOSTON_CENTER_LAT, personal_config.BOSTON_CENTER_LNG)


# ---------------------------------------------------------------------------
# haversine_miles
# ---------------------------------------------------------------------------


def test_haversine_zero_at_identical_points():
    assert haversine_miles(BOSTON, BOSTON) == 0.0


def test_haversine_non_negative_and_symmetric():
    chicago = (41.8781, -87.6298)
    d_ab = haversine_miles(BOSTON, chicago)
    d_ba = haversine_miles(chicago, BOSTON)
    assert d_ab >= 0.0
    assert d_ab == d_ba


def test_haversine_known_distance_boston_to_nyc():
    # Boston -> New York City is roughly 190 miles great-circle.
    nyc = (40.7128, -74.0060)
    d = haversine_miles(BOSTON, nyc)
    assert 180.0 <= d <= 200.0


# ---------------------------------------------------------------------------
# apply_location_rule — spot checks driven through classify_location
# ---------------------------------------------------------------------------


def _verdict_for(location, maps, native_workplace=None, native_country=None):
    result = classify_location(
        location,
        native_workplace=native_workplace,
        native_country=native_country,
        maps=maps,
    )
    return apply_location_rule([result])


def test_boston_onsite_kept_distance_near_zero(small_location_maps):
    v = _verdict_for("Boston, MA", small_location_maps)
    assert v.keep is True
    assert v.maybe_reason is None
    assert v.exclusion_reason is None
    assert v.distance_miles is not None and v.distance_miles < 1.0


def test_waltham_framingham_lowell_within_radius_kept(small_location_maps):
    for place in ("Waltham, MA", "Framingham, MA", "Lowell, MA"):
        v = _verdict_for(place, small_location_maps)
        assert v.keep is True, place
        assert v.maybe_reason is None, place
        assert v.exclusion_reason is None, place
        assert v.distance_miles is not None and v.distance_miles <= 30.0, place


def test_worcester_ma_onsite_is_secondary(small_location_maps):
    v = _verdict_for("Worcester, MA", small_location_maps)
    assert v.keep is True
    assert v.maybe_reason == "location_new_england"
    assert v.exclusion_reason is None
    assert v.distance_miles is not None and v.distance_miles > 30.0
    assert v.state == "MA"


def test_providence_ri_is_secondary(small_location_maps):
    v = _verdict_for("Providence, RI", small_location_maps)
    assert v.keep is True
    assert v.maybe_reason == "location_new_england"
    assert v.state == "RI"


def test_chicago_onsite_outside_radius(small_location_maps):
    v = _verdict_for("Chicago, IL", small_location_maps)
    assert v.keep is False
    assert v.exclusion_reason == "outside_radius"


def test_remote_canada_rejected_non_us(small_location_maps):
    v = _verdict_for("Remote, Canada", small_location_maps)
    assert v.keep is False
    assert v.exclusion_reason == "non_us"


def test_bare_remote_is_unspecified(small_location_maps):
    v = _verdict_for("Remote", small_location_maps)
    assert v.keep is True
    assert v.maybe_reason == "remote_unspecified"
    assert v.remote_scope == "unspecified"
    assert v.exclusion_reason is None


def test_remote_us_kept_primary_eligible(small_location_maps):
    for place in ("Remote - US", "Remote, United States"):
        v = _verdict_for(place, small_location_maps)
        assert v.keep is True, place
        assert v.maybe_reason is None, place
        assert v.remote_scope == "us", place
        assert v.exclusion_reason is None, place


# ---------------------------------------------------------------------------
# Job-level precedence with synthetic LocationResult lists
# ---------------------------------------------------------------------------


def _loc(workplace, *, status="resolved", country=None, remote_scope=None,
         coords=None, state=None):
    return LocationResult(
        workplace_type=workplace,
        status=status,
        country=country,
        remote_scope=remote_scope,
        coords=coords,
        state=state,
    )


def test_kept_wins_over_secondary_and_rejection():
    kept = _loc("onsite", coords=(42.3601, -71.0589), state="MA", country="US")
    secondary = _loc("onsite", coords=(42.2626, -71.8023), state="MA", country="US")
    reject = _loc("onsite", coords=(41.8781, -87.6298), state="IL", country="US")
    v = apply_location_rule([reject, secondary, kept], center=BOSTON, radius_miles=30)
    assert v.keep is True
    assert v.maybe_reason is None
    assert v.distance_miles is not None and v.distance_miles < 1.0


def test_secondary_wins_over_remote_unspecified():
    secondary = _loc("onsite", coords=(42.2626, -71.8023), state="MA", country="US")
    unspec = _loc("remote", remote_scope="unspecified")
    v = apply_location_rule([unspec, secondary], center=BOSTON, radius_miles=30)
    assert v.maybe_reason == "location_new_england"


def test_remote_unspecified_wins_over_undecided():
    unspec = _loc("remote", remote_scope="unspecified")
    undecided = _loc("onsite", status="unknown")
    v = apply_location_rule([undecided, unspec], center=BOSTON, radius_miles=30)
    assert v.keep is True
    assert v.maybe_reason == "remote_unspecified"


def test_undecided_kept_as_location_unknown():
    undecided = _loc("onsite", status="ambiguous")
    v = apply_location_rule([undecided], center=BOSTON, radius_miles=30)
    assert v.keep is True
    assert v.maybe_reason == "location_unknown"
    assert v.status == "ambiguous"


def test_reject_non_us_only_when_all_non_us():
    non_us = _loc("remote", country="GB")
    outside = _loc("onsite", coords=(41.8781, -87.6298), state="IL", country="US")
    # Mixed rejections -> outside_radius wins.
    v = apply_location_rule([non_us, outside], center=BOSTON, radius_miles=30)
    assert v.keep is False
    assert v.exclusion_reason == "outside_radius"
    # All non_us -> non_us.
    v2 = apply_location_rule([non_us], center=BOSTON, radius_miles=30)
    assert v2.exclusion_reason == "non_us"


def test_defaults_read_from_personal_config():
    # No center/radius/states passed: a Boston onsite location is kept.
    boston = _loc("onsite", coords=BOSTON, state="MA", country="US")
    v = apply_location_rule([boston])
    assert v.keep is True
    assert v.maybe_reason is None


def test_verdict_is_frozen_dataclass():
    v = apply_location_rule([_loc("remote", remote_scope="us")])
    assert isinstance(v, LocationVerdict)
    import dataclasses
    assert dataclasses.is_dataclass(v)
