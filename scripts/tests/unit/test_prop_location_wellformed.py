"""Property-based test that ``classify_location`` output is always well formed.

Property 15: Location classification output is well formed.

For any input at all - arbitrary text, ``None``, empty strings, very long
strings, Unicode, numbers rendered as strings, and well-structured
``"{city}, {state}"`` strings - ``classify_location`` SHALL return a
``LocationResult`` whose fields obey the invariants below, whether or not a
location map is supplied:

- ``workplace_type`` in {remote, hybrid, onsite, unknown};
- ``status`` in {resolved, ambiguous, unknown};
- ``remote_scope`` in {us, unspecified, None};
- ``coords`` is either ``None`` or a (lat, lng) pair with latitude in
  [-90, 90] and longitude in [-180, 180] (Requirement 8.10).

Plus the structural rules the design spells out:

- a ``remote`` result with no recorded non-US country has status ``resolved``
  and a ``remote_scope`` in {us, unspecified} (Requirement 8.7);
- a non-remote result (hybrid / onsite / unknown) has no ``remote_scope``;
- a non-remote result with neither coordinates nor a country has status
  ``unknown`` (Requirement 8.8).

Each input is run twice: once with ``maps=None`` (text-signal-only path) and
once against the shared ``small_location_maps`` fixture (the full lookup path),
so both branches of the classifier are exercised on every generated input.

Validates: Requirements 8.7, 8.8, 8.10
"""

from hypothesis import given, settings
from hypothesis import strategies as st

import location_filter


# ---------------------------------------------------------------------------
# Generators: deliberately arbitrary inputs
# ---------------------------------------------------------------------------

# US state codes / names so some structured city-state strings actually resolve.
_STATES = ["MA", "CA", "NY", "IL", "RI", "CT", "Massachusetts", "California"]

# Plausible city tokens, a mix of fixture cities and unknown ones.
_CITIES = [
    "Boston", "Cambridge", "Worcester", "San Francisco", "Chicago",
    "New York City", "Nowhereville", "Zzz", "Springfield",
]


@st.composite
def _structured_city(draw):
    """A ``"{city}, {state}"`` style string, sometimes with a remote/hybrid tag."""
    city = draw(st.sampled_from(_CITIES))
    state = draw(st.sampled_from(_STATES))
    prefix = draw(st.sampled_from(["", "Remote - ", "Hybrid in ", "Onsite - "]))
    suffix = draw(st.sampled_from(["", " (Remote)", ", United States", ""]))
    return "{p}{c}, {s}{x}".format(p=prefix, c=city, s=state, x=suffix)


# Arbitrary free text: anything Hypothesis can draw, including control chars,
# punctuation, and whitespace-only strings.
_free_text = st.text(max_size=500)

# Very long strings (push past any reasonable buffer size).
_long_text = st.text(min_size=200, max_size=500)

# Unicode-heavy strings (accents, CJK, emoji, combining marks).
_unicode_text = st.text(
    alphabet=st.characters(min_codepoint=0x80, max_codepoint=0x2FFF),
    max_size=120,
)

# Numbers rendered as strings, and bare non-string natives.
_number_strings = st.one_of(
    st.integers().map(str),
    st.floats(allow_nan=False, allow_infinity=False).map(str),
    st.integers(),            # a bare int (classify_location str()s it)
    st.floats(allow_nan=False, allow_infinity=False),
)

# The full input space for the location argument.
_LOCATION = st.one_of(
    st.none(),
    st.just(""),
    st.just("   "),
    _free_text,
    _long_text,
    _unicode_text,
    _number_strings,
    _structured_city(),
)

# Native workplace / country values the caller might attach, including junk.
_NATIVE_WORKPLACE = st.one_of(
    st.none(),
    st.sampled_from(
        ["remote", "Remote", "hybrid", "onsite", "on-site", "fully remote",
         "", "gibberish", "WORK FROM MARS"]
    ),
)
_NATIVE_COUNTRY = st.one_of(
    st.none(),
    st.sampled_from(["US", "United States", "us", "Canada", "GB", "", "???"]),
)


# ---------------------------------------------------------------------------
# The invariant: one place that every example checks
# ---------------------------------------------------------------------------

_VALID_WORKPLACE = {"remote", "hybrid", "onsite", "unknown"}
_VALID_STATUS = {"resolved", "ambiguous", "unknown"}
_VALID_SCOPE = {"us", "unspecified", None}


def _assert_well_formed(result):
    """Assert a LocationResult satisfies Property 15 (8.7, 8.8, 8.10)."""
    # Core enumerations.
    assert result.workplace_type in _VALID_WORKPLACE, result.workplace_type
    assert result.status in _VALID_STATUS, result.status
    assert result.remote_scope in _VALID_SCOPE, result.remote_scope

    # Coordinates: absent, or an in-range (lat, lng) pair (8.10).
    coords = result.coords
    if coords is not None:
        assert isinstance(coords, tuple) and len(coords) == 2, coords
        lat, lng = coords
        assert -90.0 <= lat <= 90.0, lat
        assert -180.0 <= lng <= 180.0, lng

    is_remote = result.workplace_type == "remote"
    is_non_us_country = result.country is not None and result.country != "US"

    if is_remote and not is_non_us_country:
        # Remote with no recorded non-US country -> resolved, scoped (8.7).
        assert result.status == "resolved", result
        assert result.remote_scope in {"us", "unspecified"}, result
    else:
        # Non-remote results carry no remote_scope.
        if not is_remote:
            assert result.remote_scope is None, result
            # Neither coordinates nor a country -> unknown status (8.8).
            if result.coords is None and result.country is None:
                assert result.status == "unknown", result


# ---------------------------------------------------------------------------
# Property 15
# ---------------------------------------------------------------------------


@settings(max_examples=400)
@given(
    location=_LOCATION,
    native_workplace=_NATIVE_WORKPLACE,
    native_country=_NATIVE_COUNTRY,
)
def test_classification_well_formed_without_maps(
    location, native_workplace, native_country
):
    """With no location map, the text-signal-only path stays well formed."""
    result = location_filter.classify_location(
        location,
        native_workplace=native_workplace,
        native_country=native_country,
        maps=None,
    )
    _assert_well_formed(result)


@settings(max_examples=400)
@given(
    location=_LOCATION,
    native_workplace=_NATIVE_WORKPLACE,
    native_country=_NATIVE_COUNTRY,
)
def test_classification_well_formed_with_maps(
    location, native_workplace, native_country, small_location_maps
):
    """With the full lookup path, classification stays well formed too."""
    result = location_filter.classify_location(
        location,
        native_workplace=native_workplace,
        native_country=native_country,
        maps=small_location_maps,
    )
    _assert_well_formed(result)
