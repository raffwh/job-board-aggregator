"""Property-based test for city-only location resolution.

Property 14: City-only resolution follows the count of US matches.

For any generated location map and any city name that is not one of the
famous-city defaults, a bare city-only lookup through
``geolocation.lookup_location_detailed`` SHALL:

- resolve to the single US entry (its coordinates, with the two-letter state
  code in ``admin`` and ``country == "US"``) when the map holds exactly one US
  entry for that city (Requirement 8.5);
- report ``ambiguous=True`` with ``coords=None`` when the map holds more than
  one US entry for that city (Requirement 8.6);
- fall back to the existing global city behavior when the map holds zero US
  entries for that city (the same coordinates ``lookup_location`` returns).

Each example builds its own small location map from generated GeoNames-shaped
rows through the real ``geolocation.build_lookup`` (written through a temp file
so the structure is byte-for-byte the shape the scraper uses at runtime), with
a controlled number of distinct US entries for the city under test.

Validates: Requirements 8.5, 8.6
"""

import json
import tempfile
from pathlib import Path

from hypothesis import assume, given, settings
from hypothesis import strategies as st

import geolocation


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# Names that parse_job_location would reinterpret (aliases, states, countries,
# boroughs, junk suffixes, garbage) must be excluded so a bare city string is
# treated as a plain city-only lookup rather than something else.
_RESERVED_CITY_NAMES = (
    set(geolocation.FAMOUS_CITY_DEFAULTS)
    | set(geolocation.CITY_ALIASES)
    | set(geolocation.US_STATES)
    | set(geolocation.COUNTRY_ALIASES)
    | set(geolocation.NYC_BOROUGHS)
    | set(geolocation.GARBAGE_LOCATIONS)
    | set(geolocation.JUNK_TOKEN_SUFFIXES)
)

# Simple single-word ASCII city names keep normalization a no-op, so the
# generated name equals its normalized key in the lookup maps.
_city_names = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz", min_size=3, max_size=10
)


def _is_safe_city(name):
    """True when ``name`` is a plain city-only token for the parser.

    The parser must see this string as a bare city with no admin, no country,
    and no remote/timezone signal, so that the city-only resolution branch is
    the one under test. We confirm that directly against the parser rather than
    enumerating every reserved substring.
    """
    normalized = geolocation.normalize(name)
    if not normalized or normalized != name:
        return False
    if " " in normalized:
        return False
    if normalized in _RESERVED_CITY_NAMES:
        return False
    # Guard against trailing junk-token stripping changing the token.
    if geolocation.clean_token(normalized) != normalized:
        return False
    # Any remote or timezone keyword as a substring makes the parser treat the
    # string as remote (no city), so exclude those names outright.
    if any(kw in normalized for kw in geolocation.REMOTE_KEYWORDS):
        return False
    if any(kw in normalized for kw in geolocation.TIMEZONE_KEYWORDS):
        return False
    # Final confirmation: the parser yields exactly this city, nothing else.
    parsed = geolocation.parse_job_location(name)
    if parsed["remote"]:
        return False
    if parsed["city"] != normalized:
        return False
    if parsed["admin"] is not None or parsed["country"] is not None:
        return False
    return True


# Distinct US states so each US entry for the city is a distinct (admin, coords)
# pair. GeoNames admin1 for US rows is the two-letter state code.
_US_STATES_CODES = sorted({code for code in geolocation.US_STATES.values()})

# Latitudes / longitudes kept in valid ranges and rounded so equality is exact.
_lat = st.floats(min_value=-89.0, max_value=89.0).map(lambda x: round(x, 4))
_lng = st.floats(min_value=-179.0, max_value=179.0).map(lambda x: round(x, 4))


def _build_maps(rows):
    """Build lookup maps from GeoNames-shaped rows via the real build_lookup."""
    payload = {
        "schema": ["city", "admin", "country", "lat", "lng"],
        "data": rows,
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as fh:
        json.dump(payload, fh)
        temp_path = fh.name
    try:
        return geolocation.build_lookup(temp_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Property 14
# ---------------------------------------------------------------------------


@settings(max_examples=200)
@given(
    city=_city_names,
    us_count=st.integers(min_value=0, max_value=5),
    us_states=st.lists(
        st.sampled_from(_US_STATES_CODES), min_size=0, max_size=5, unique=True
    ),
    coords=st.lists(st.tuples(_lat, _lng), min_size=6, max_size=6, unique=True),
    include_non_us=st.booleans(),
    non_us_coord=st.tuples(_lat, _lng),
)
def test_city_only_resolution_follows_us_match_count(
    city, us_count, us_states, coords, include_non_us, non_us_coord
):
    assume(_is_safe_city(city))

    # How many distinct US entries this city will have.
    n_us = min(us_count, len(us_states), len(coords))

    # The non-US coords must not collide with any US coords, so a zero-US
    # fallback has an unambiguous target.
    assume(non_us_coord not in set(coords[:n_us]))

    rows = []
    for i in range(n_us):
        lat, lng = coords[i]
        rows.append([city, us_states[i], "US", lat, lng])

    # Optionally add a non-US row for the same city so the zero-US branch has a
    # global fallback to land on (Requirement 8.5 "existing global behavior").
    if include_non_us:
        rows.append([city, "ENG", "GB", non_us_coord[0], non_us_coord[1]])

    maps = _build_maps(rows)

    result = geolocation.lookup_location_detailed(city, maps)

    if n_us == 1:
        # Exactly one US entry: resolve to it with its state and coords.
        expected_state, (lat, lng) = us_states[0], coords[0]
        assert result["ambiguous"] is False
        assert result["country"] == "US"
        assert result["admin"] == expected_state
        assert result["coords"] == [lat, lng]
    elif n_us > 1:
        # More than one US entry: ambiguous, no coordinates.
        assert result["ambiguous"] is True
        assert result["coords"] is None
    else:
        # Zero US entries: fall back to the existing global city behavior,
        # which is exactly what lookup_location returns for the same string.
        assert result["ambiguous"] is False
        fallback = geolocation.lookup_location(city, maps)
        assert result["coords"] == fallback["coords"]
        if include_non_us:
            assert result["coords"] == [non_us_coord[0], non_us_coord[1]]
        else:
            # No entry at all for this city: nothing resolves.
            assert result["coords"] is None
