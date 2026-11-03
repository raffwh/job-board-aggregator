"""Property-based test for the Greater-Boston radius rule.

Property 17: The radius rule keeps exactly the jobs within the radius, with
New England as maybe.

For any coordinates c, any center, any radius r >= 0, any US state code s (or
none), and any set S of secondary states (including the empty set), a single
non-remote ``LocationResult`` at c with state s SHALL be:

- kept as primary-eligible exactly when ``haversine_miles(center, c) <= r``,
  with ``distance_miles`` equal to that distance rounded to one decimal;
- otherwise, kept with ``maybe_reason == "location_new_england"`` and the same
  distance when s is in S;
- otherwise, rejected with ``outside_radius``.

It follows that a job kept as primary-eligible at radius r1 is kept as
primary-eligible at every r2 >= r1 (monotonicity), and that with S empty no job
gets ``location_new_england``.

The test builds single-location onsite ``LocationResult`` objects directly
(bypassing the classifier) so it exercises the radius rule in isolation against
generated coordinates, centers, radii, states, and secondary-state sets.

Validates: Requirements 9.4, 9.5, 9.10, 9.16
"""

from hypothesis import given, settings
from hypothesis import strategies as st

import location_filter
from location_filter import LocationResult, apply_location_rule, haversine_miles


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# Valid latitude / longitude ranges (Requirement 8.10). Exclude NaN/inf so the
# haversine distance is a finite comparable number.
_lat = st.floats(min_value=-90.0, max_value=90.0, allow_nan=False, allow_infinity=False)
_lng = st.floats(min_value=-180.0, max_value=180.0, allow_nan=False, allow_infinity=False)
_coords = st.tuples(_lat, _lng)

# Radius in miles: non-negative, finite, up to more than half the Earth's
# circumference so every point can fall inside for a large enough radius.
_radius = st.floats(min_value=0.0, max_value=13000.0, allow_nan=False, allow_infinity=False)

# Every two-letter US state code, plus None (no state).
_ALL_STATE_CODES = sorted(location_filter.personal_config.US_STATE_ABBREVIATIONS) \
    if hasattr(location_filter.personal_config, "US_STATE_ABBREVIATIONS") else [
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
        "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
        "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
        "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
        "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
    ]

_state = st.one_of(st.none(), st.sampled_from(_ALL_STATE_CODES))

# A set of secondary states drawn from the full code list, including the empty
# set (which turns the Secondary_Region off).
_secondary_states = st.sets(st.sampled_from(_ALL_STATE_CODES), max_size=6)

# A center can be anywhere valid, not just Boston.
_center = st.tuples(_lat, _lng)


def _onsite_result(coords, state):
    """A single resolved onsite LocationResult at ``coords`` with ``state``."""
    return LocationResult(
        workplace_type="onsite",
        status="resolved",
        country="US",
        remote_scope=None,
        coords=coords,
        state=state,
    )


# ---------------------------------------------------------------------------
# Property 17 — exact keep/maybe/reject classification
# ---------------------------------------------------------------------------


@settings(max_examples=400)
@given(
    coords=_coords,
    center=_center,
    radius=_radius,
    state=_state,
    secondary=_secondary_states,
)
def test_radius_rule_classifies_single_onsite_location(
    coords, center, radius, state, secondary
):
    result = _onsite_result(coords, state)
    verdict = apply_location_rule(
        [result],
        center=center,
        radius_miles=radius,
        secondary_region_states=secondary,
    )

    distance = haversine_miles(center, coords)
    expected_distance = round(distance, 1)

    if distance <= radius:
        # Inside the radius: kept as primary-eligible, no maybe reason, with the
        # distance recorded to one decimal (Requirements 9.4, 9.16).
        assert verdict.keep is True
        assert verdict.maybe_reason is None
        assert verdict.exclusion_reason is None
        assert verdict.distance_miles == expected_distance
    elif state is not None and state in secondary:
        # Outside the radius but in the Secondary_Region: kept as "maybe" with
        # the New England reason and the same distance (Requirement 9.16).
        assert verdict.keep is True
        assert verdict.maybe_reason == "location_new_england"
        assert verdict.exclusion_reason is None
        assert verdict.distance_miles == expected_distance
    else:
        # Outside the radius and not a secondary-region state: rejected
        # (Requirement 9.5).
        assert verdict.keep is False
        assert verdict.maybe_reason is None
        assert verdict.exclusion_reason == "outside_radius"


# ---------------------------------------------------------------------------
# Property 17 (corollary) — monotonicity in the radius (Requirement 9.10)
# ---------------------------------------------------------------------------


@settings(max_examples=400)
@given(
    coords=_coords,
    center=_center,
    r1=_radius,
    r2=_radius,
    state=_state,
    secondary=_secondary_states,
)
def test_radius_rule_is_monotonic_in_radius(
    coords, center, r1, r2, state, secondary
):
    # Order the two radii so r_small <= r_large.
    r_small, r_large = sorted((r1, r2))

    result = _onsite_result(coords, state)

    def _kept_as_primary(radius):
        verdict = apply_location_rule(
            [_onsite_result(coords, state)],
            center=center,
            radius_miles=radius,
            secondary_region_states=secondary,
        )
        # "Primary-eligible" means kept by the radius check itself: kept with no
        # maybe reason. A New England "maybe" is kept but not primary-eligible.
        return verdict.keep and verdict.maybe_reason is None

    # Monotonicity: a location kept as primary-eligible at the smaller radius is
    # still kept as primary-eligible at the larger radius.
    if _kept_as_primary(r_small):
        assert _kept_as_primary(r_large)


# ---------------------------------------------------------------------------
# Property 17 (corollary) — empty Secondary_Region produces no New England maybe
# ---------------------------------------------------------------------------


@settings(max_examples=300)
@given(
    coords=_coords,
    center=_center,
    radius=_radius,
    state=_state,
)
def test_empty_secondary_region_never_yields_new_england(
    coords, center, radius, state
):
    verdict = apply_location_rule(
        [_onsite_result(coords, state)],
        center=center,
        radius_miles=radius,
        secondary_region_states=set(),
    )

    # With no secondary states configured, no job can be kept as
    # ``location_new_england``.
    assert verdict.maybe_reason != "location_new_england"
    # It is either kept inside the radius or rejected outside it.
    if verdict.keep:
        assert verdict.maybe_reason is None
        assert haversine_miles(center, coords) <= radius
    else:
        assert verdict.exclusion_reason == "outside_radius"
