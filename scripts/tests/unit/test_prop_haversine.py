"""Property-based test for the haversine distance metric.

Property 16: Haversine distance behaves as a metric on the sphere.

For any coordinate pairs a and b with valid latitude (in [-90, 90]) and
longitude (in [-180, 180]), ``location_filter.haversine_miles`` SHALL satisfy:

- ``haversine_miles(a, b)`` is >= 0 (non-negativity);
- ``haversine_miles(a, a)`` is 0 within 1e-9 (identity of indiscernibles);
- ``haversine_miles(a, b)`` equals ``haversine_miles(b, a)`` within 1e-9
  (symmetry);
- the distance never exceeds half the Earth's circumference, which with the
  mean Earth radius R = 3958.8 miles used by the implementation is
  ``pi * R`` ~= 12,437 miles. The design bounds this at 12,450 miles, so that
  is the ceiling asserted here.

Validates: Requirements 9.1, 9.9
"""

import math

from hypothesis import given, settings
from hypothesis import strategies as st

import location_filter


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# Valid latitudes and longitudes, excluding NaN/inf so the metric laws hold.
_LAT = st.floats(
    min_value=-90.0, max_value=90.0, allow_nan=False, allow_infinity=False
)
_LNG = st.floats(
    min_value=-180.0, max_value=180.0, allow_nan=False, allow_infinity=False
)

_POINT = st.tuples(_LAT, _LNG)

# Half the Earth's circumference ceiling from the design (12,450 miles); the
# implementation's exact maximum is pi * 3958.8 ~= 12,437 miles.
_MAX_DISTANCE_MILES = 12450.0


# ---------------------------------------------------------------------------
# Property 16
# ---------------------------------------------------------------------------


@settings(max_examples=400)
@given(a=_POINT, b=_POINT)
def test_distance_is_non_negative(a, b):
    """haversine_miles never returns a negative distance (9.1, 9.9)."""
    assert location_filter.haversine_miles(a, b) >= 0.0


@settings(max_examples=400)
@given(a=_POINT)
def test_distance_to_self_is_zero(a):
    """A point is zero miles from itself, within 1e-9 (9.1, 9.9)."""
    assert math.isclose(
        location_filter.haversine_miles(a, a), 0.0, abs_tol=1e-9
    )


@settings(max_examples=400)
@given(a=_POINT, b=_POINT)
def test_distance_is_symmetric(a, b):
    """d(a, b) equals d(b, a) within 1e-9 (9.1, 9.9)."""
    d_ab = location_filter.haversine_miles(a, b)
    d_ba = location_filter.haversine_miles(b, a)
    assert math.isclose(d_ab, d_ba, abs_tol=1e-9)


@settings(max_examples=400)
@given(a=_POINT, b=_POINT)
def test_distance_is_bounded_by_half_circumference(a, b):
    """No distance exceeds half the Earth's circumference (9.1, 9.9)."""
    assert location_filter.haversine_miles(a, b) <= _MAX_DISTANCE_MILES
