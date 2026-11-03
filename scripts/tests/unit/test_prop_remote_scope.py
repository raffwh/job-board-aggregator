"""Property-based test for remote-scope handling in the Location_Filter.

Property 18: Remote jobs are primary only for US scope.

For any remote ``LocationResult`` and any native country value (none, a US
alias, or a non-US country/region), ``apply_location_rule`` SHALL:

- keep a remote job with ``remote_scope == "us"`` as primary-eligible: it is
  kept with no ``maybe_reason`` and no ``exclusion_reason`` (Requirements 9.2,
  8.7);
- reject a remote job whose recorded country/region is outside the US with
  ``exclusion_reason == "non_us"`` (Requirements 8.3, 9.3);
- keep a remote job with no country information as a maybe, reporting
  ``remote_scope == "unspecified"`` and ``maybe_reason == "remote_unspecified"``
  (Requirement 9.15);
- never keep a remote job whose scope is anything other than US or unspecified:
  the only kept-primary scope is ``us`` and the only kept-maybe scope is
  ``unspecified`` (Requirement 9.2).

This drives ``apply_location_rule`` directly with ``LocationResult`` objects
that mirror the shapes ``classify_location`` produces for remote postings,
which isolates the scope rule from the city/region parser. The verdict is
checked against an explicit Boston center and radius so the remote branch never
depends on coordinate resolution.

Validates: Requirements 8.3, 8.7, 9.2, 9.3, 9.15
"""

from hypothesis import given, settings
from hypothesis import strategies as st

import location_filter
from location_filter import LocationResult


# ---------------------------------------------------------------------------
# Fixed filter inputs
# ---------------------------------------------------------------------------

# A concrete Boston center / radius / Secondary_Region set so the test does not
# depend on personal_config. Remote verdicts ignore these, but passing them
# explicitly keeps the remote path independent of runtime configuration.
_CENTER = (42.3601, -71.0589)
_RADIUS = 30.0
_SECONDARY_STATES = frozenset({"RI", "CT", "NH", "ME", "VT"})


def _apply(result):
    return location_filter.apply_location_rule(
        [result],
        center=_CENTER,
        radius_miles=_RADIUS,
        secondary_region_states=_SECONDARY_STATES,
    )


# ---------------------------------------------------------------------------
# Generators for remote LocationResult objects
# ---------------------------------------------------------------------------

# Non-US country (ISO-2) and region codes that the classifier records on a
# remote result, drawn from the configured region set and common countries.
_NON_US_COUNTRIES = st.sampled_from(
    [
        "CA",  # Canada
        "GB",  # United Kingdom
        "IN",  # India
        "DE",  # Germany
        "FR",
        "AU",
        "EMEA",
        "APAC",
        "LATAM",
        "EU",
    ]
)

# A US state code occasionally recorded alongside a US remote result.
_US_STATE = st.sampled_from([None, "MA", "CA", "NY", "TX", "WA"])

# Coordinates are irrelevant to the remote branch; vary them (including None)
# to prove the scope rule does not depend on a resolved place.
_COORDS = st.one_of(
    st.none(),
    st.tuples(
        st.floats(min_value=-89.0, max_value=89.0, allow_nan=False),
        st.floats(min_value=-179.0, max_value=179.0, allow_nan=False),
    ),
)


@st.composite
def _remote_us(draw):
    """A remote result scoped to the US (names the US or a US state)."""
    return LocationResult(
        workplace_type="remote",
        status="resolved",
        country="US",
        remote_scope="us",
        coords=draw(_COORDS),
        state=draw(_US_STATE),
    )


@st.composite
def _remote_non_us(draw):
    """A remote result carrying a recorded non-US country or region."""
    return LocationResult(
        workplace_type="remote",
        status="resolved",
        country=draw(_NON_US_COUNTRIES),
        remote_scope=None,
        coords=draw(_COORDS),
        state=None,
    )


@st.composite
def _remote_unspecified(draw):
    """A remote result with no country information at all."""
    return LocationResult(
        workplace_type="remote",
        status="resolved",
        country=None,
        remote_scope="unspecified",
        coords=draw(_COORDS),
        state=None,
    )


# ---------------------------------------------------------------------------
# Property 18
# ---------------------------------------------------------------------------


@settings(max_examples=200)
@given(result=_remote_us())
def test_remote_us_is_primary_eligible(result):
    """Remote + US scope is kept as primary-eligible, no maybe (9.2, 8.7)."""
    verdict = _apply(result)
    assert verdict.keep is True
    assert verdict.maybe_reason is None
    assert verdict.exclusion_reason is None
    assert verdict.remote_scope == "us"


@settings(max_examples=200)
@given(result=_remote_non_us())
def test_remote_non_us_is_rejected(result):
    """Remote + a non-US country/region is rejected non_us (8.3, 9.3)."""
    verdict = _apply(result)
    assert verdict.keep is False
    assert verdict.exclusion_reason == "non_us"
    assert verdict.maybe_reason is None


@settings(max_examples=200)
@given(result=_remote_unspecified())
def test_remote_unspecified_is_kept_as_maybe(result):
    """Remote with no country is kept as maybe remote_unspecified (9.15)."""
    verdict = _apply(result)
    assert verdict.keep is True
    assert verdict.maybe_reason == "remote_unspecified"
    assert verdict.exclusion_reason is None
    assert verdict.remote_scope == "unspecified"


@settings(max_examples=300)
@given(
    result=st.one_of(_remote_us(), _remote_non_us(), _remote_unspecified()),
)
def test_only_us_or_unspecified_scope_is_ever_kept(result):
    """Across every remote shape, only US or unspecified scope is kept (9.2).

    A kept remote verdict is primary-eligible only with ``remote_scope == "us"``
    and no ``maybe_reason``; the only other kept shape reports
    ``remote_scope == "unspecified"`` with ``maybe_reason ==
    "remote_unspecified"``. Any recorded non-US scope must be rejected.
    """
    verdict = _apply(result)
    if verdict.keep:
        if verdict.maybe_reason is None:
            # Primary-eligible remote: scope must be exactly US.
            assert verdict.remote_scope == "us"
        else:
            # The only kept-maybe remote scope is unspecified.
            assert verdict.maybe_reason == "remote_unspecified"
            assert verdict.remote_scope == "unspecified"
    else:
        # The only rejection for a remote job is non_us.
        assert verdict.exclusion_reason == "non_us"
