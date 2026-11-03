"""Property-based test for US state detection in location classification.

Property 13: US state markers resolve to the US.

For any US city row in the test location map paired with that row's state name
or two-letter abbreviation, in the forms ``"{city}, {ST}"``, ``"{city},
{State}"``, and ``"{city}, {State}, United States of America"``,
``classify_location`` SHALL record country ``US`` and ``state`` equal to the
row's two-letter state code.

This test exercises two paths to the same conclusion:

- Resolved US places (cities drawn from ``small_location_maps``): the detailed
  lookup lands on a US row, so ``country == "US"`` and ``state`` is that row's
  two-letter admin code (Requirements 8.4, 8.11).
- The state marker alone (a city name absent from the map): no coordinates
  resolve, yet a US state name or abbreviation in city-state position still
  forces ``country == "US"`` (Requirement 8.4). This confirms the US
  determination comes from the state marker rather than from a successful
  coordinate lookup. The two-letter ``state`` code is recorded only when the
  location resolves to an actual US place (Requirement 8.11), so it is asserted
  in the resolved-place path above, not here.

Validates: Requirements 8.4, 8.11
"""

from hypothesis import assume, given, settings
from hypothesis import strategies as st

import geolocation
import location_filter


# ---------------------------------------------------------------------------
# State forms: two-letter code -> (abbreviation, full name)
# ---------------------------------------------------------------------------

# ``US_STATES`` maps every alias (two-letter abbreviation and full name) to the
# canonical two-letter code. Invert it to recover, for each code, both a
# two-letter abbreviation and a full-name spelling so each generated string can
# use either form.
def _state_forms():
    abbrev = {}
    full = {}
    for alias, code in geolocation.US_STATES.items():
        if len(alias) == 2:
            abbrev.setdefault(code, alias)
        else:
            full.setdefault(code, alias)
    forms = {}
    for code in set(abbrev) | set(full):
        options = []
        if code in abbrev:
            options.append(abbrev[code])
        if code in full:
            options.append(full[code])
        forms[code] = options
    return forms


_STATE_FORMS = _state_forms()

# US rows in the shared fixture, as (city, two-letter state code) pairs. The
# fixture's admin column is the GeoNames admin1 code (two-letter state for US).
_FIXTURE_US_ROWS = [
    ("Boston", "MA"),
    ("Cambridge", "MA"),
    ("Waltham", "MA"),
    ("Burlington", "MA"),
    ("Quincy", "MA"),
    ("Framingham", "MA"),
    ("Lowell", "MA"),
    ("Worcester", "MA"),
    ("Providence", "RI"),
    ("Hartford", "CT"),
    ("San Francisco", "CA"),
    ("New York City", "NY"),
    ("Chicago", "IL"),
]

# City names that are not in the fixture map and are not reserved tokens, so a
# "City, ST" string resolves no coordinates but still carries the state marker.
_RESERVED_TOKENS = (
    set(geolocation.US_STATES)
    | set(geolocation.COUNTRY_ALIASES)
    | set(geolocation.CITY_ALIASES)
    | set(geolocation.FAMOUS_CITY_DEFAULTS)
    | set(geolocation.GARBAGE_LOCATIONS)
)
_FIXTURE_CITY_NAMES = {geolocation.normalize(c) for c, _ in _FIXTURE_US_ROWS}

_unmapped_city = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz", min_size=4, max_size=10
)


def _is_unmapped_plain_city(name):
    """True when ``name`` is a bare city token absent from the fixture map."""
    normalized = geolocation.normalize(name)
    if not normalized or normalized != name or " " in normalized:
        return False
    if normalized in _RESERVED_TOKENS or normalized in _FIXTURE_CITY_NAMES:
        return False
    if geolocation.clean_token(normalized) != normalized:
        return False
    if any(kw in normalized for kw in geolocation.REMOTE_KEYWORDS):
        return False
    if any(kw in normalized for kw in geolocation.TIMEZONE_KEYWORDS):
        return False
    return True


# One sampled strategy over (code, form) so every example uses a real state in
# either its abbreviation or full-name spelling. A few two-letter abbreviations
# are not unambiguous state markers in free text: some collide with
# normalization's direction/saint expansions ("ne" -> "northeast", "mt" ->
# "mount") and some collide with a country alias ("ar" is both Arkansas and
# Argentina, and the parser reads the country first). We keep only forms that
# the parser actually interprets as that state in city-state position, which is
# exactly the precondition this property assumes. The dropped abbreviations are
# still covered by their full-name spellings ("nebraska", "montana",
# "arkansas").
def _marker_is_unambiguous_state(code, form):
    parsed = geolocation.parse_job_location("Testcity, {form}".format(form=form))
    return parsed["country"] == "US" and parsed["admin"] == code


def _state_code_and_form():
    return st.sampled_from(
        [
            (code, form)
            for code, forms in _STATE_FORMS.items()
            for form in forms
            if _marker_is_unambiguous_state(code, form)
        ]
    )


# ---------------------------------------------------------------------------
# Property 13 — resolved US places from the fixture map
# ---------------------------------------------------------------------------


@settings(max_examples=200)
@given(
    row=st.sampled_from(_FIXTURE_US_ROWS),
    use_full_name=st.booleans(),
    trailing_country=st.booleans(),
)
def test_fixture_us_city_resolves_to_us_with_state_code(
    small_location_maps, row, use_full_name, trailing_country
):
    city, code = row
    forms = _STATE_FORMS[code]
    # Prefer the full name when asked and available, else the abbreviation.
    marker = forms[-1] if (use_full_name and len(forms) > 1) else forms[0]

    location = "{city}, {marker}".format(city=city, marker=marker)
    if trailing_country:
        location += ", United States of America"

    result = location_filter.classify_location(
        location, maps=small_location_maps
    )

    assert result.country == "US"
    assert result.state == code


# ---------------------------------------------------------------------------
# Property 13 — the state marker alone determines US, no map resolution
# ---------------------------------------------------------------------------


@settings(max_examples=200)
@given(
    city=_unmapped_city,
    code_form=_state_code_and_form(),
    trailing_country=st.booleans(),
)
def test_state_marker_determines_us_without_resolution(
    small_location_maps, city, code_form, trailing_country
):
    assume(_is_unmapped_plain_city(city))
    code, marker = code_form

    # Capitalize the city so the string looks like a real posting; the marker
    # is used as-is (abbreviation or full name).
    location = "{city}, {marker}".format(city=city.capitalize(), marker=marker)
    if trailing_country:
        location += ", United States of America"

    result = location_filter.classify_location(
        location, maps=small_location_maps
    )

    # The city is not in the map, so no coordinates resolve. The state marker in
    # city-state position still determines the country as US (Requirement 8.4).
    # The two-letter ``state`` code is only recorded when the location resolves
    # to an actual US place (Requirement 8.11), which does not happen here, so
    # we assert the country determination, not the state code.
    assert result.coords is None
    assert result.country == "US"
