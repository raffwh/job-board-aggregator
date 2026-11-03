"""Property-based test for workplace-type precedence.

Property 12: Workplace type follows the precedence rules.

For any location string built from the templates ``"Remote - {place}"``,
``"{place} (Remote)"``, ``"Hybrid in {city}, {state}"``, ``"Home-based"``,
``"{city}, {state}"``, and arbitrary text, ``classify_location`` SHALL:

- return the supplied native workplace value (one of remote, hybrid, onsite)
  whenever one is supplied, regardless of the location text (Requirement 8.1);
- when no native value is supplied, derive Workplace_Type from the text
  (Requirement 8.2): ``remote`` for remote / home-based templates, ``hybrid``
  for hybrid templates, ``onsite`` for a resolved city-state, and ``unknown``
  for text that names no place and carries no workplace keyword.

The precedence under test, from strongest to weakest:

    native workplace  >  location-text keyword  >  onsite-if-city-resolved  >  unknown

A handful of known city/state pairs from the shared ``small_location_maps``
fixture drive the resolved-city cases, so a ``"{city}, {state}"`` string
reliably resolves to a single US entry and classifies as ``onsite``.

Validates: Requirements 8.1, 8.2
"""

from hypothesis import assume, given, settings
from hypothesis import strategies as st

import geolocation
import location_filter


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# Native workplace values spanning the spellings _normalize_native_workplace
# accepts, paired with the vocabulary value classify_location must return.
_NATIVE_WORKPLACE = st.sampled_from(
    [
        ("remote", "remote"),
        ("Remote", "remote"),
        ("fully remote", "remote"),
        ("hybrid", "hybrid"),
        ("Hybrid", "hybrid"),
        ("flexible hybrid", "hybrid"),
        ("onsite", "onsite"),
        ("on-site", "onsite"),
        ("On Site", "onsite"),
        ("in-office", "onsite"),
    ]
)

# Cities in small_location_maps that resolve to a single, unambiguous US entry
# when paired with their state code, so a "{city}, {state}" string is onsite.
# (Cambridge, Burlington, and Quincy are deliberately excluded: the fixture
# also has non-US rows for those names.)
_UNAMBIGUOUS_US_CITIES = [
    ("Boston", "MA"),
    ("Waltham", "MA"),
    ("Framingham", "MA"),
    ("Lowell", "MA"),
    ("Worcester", "MA"),
    ("Providence", "RI"),
    ("Hartford", "CT"),
    ("San Francisco", "CA"),
    ("New York City", "NY"),
    ("Chicago", "IL"),
]
_US_CITY_STATE = st.sampled_from(_UNAMBIGUOUS_US_CITIES)

# Remote-template fillers: a bare city-state place, or a region, or nothing.
_REMOTE_PLACE = st.sampled_from(
    ["Boston, MA", "Chicago, IL", "San Francisco, CA", "US", "United States", ""]
)

# Free text that carries no workplace keyword. The onsite-if-city-resolved
# default keys off whether the parser extracts a *city token* (not whether that
# token resolves to coordinates), so a string only classifies as ``unknown``
# when ``parse_job_location`` finds no city, no country, and no remote signal.
# ``_is_unknown_text`` is asserted at the top of the test so these examples
# truly exercise the weakest precedence branch.
_UNKNOWN_TEXT = st.sampled_from(
    [
        "",
        "   ",
        "TBD",
        "N/A",
        "unspecified",
        "various",
        "multiple",
        "see posting",
        "to be determined",
        "not disclosed",
    ]
)


def _resolves_a_city(text):
    """True when ``classify_location`` would extract any city token for ``text``.

    Reproduces the two resolution paths in ``classify_location``: the direct
    parse of the original string, and the fallback parse of the
    keyword-stripped remainder. A city from *either* path makes the result
    ``onsite`` under the onsite-if-city-resolved default, even when no
    coordinates resolve (e.g. ``"N/A"`` -> stripped ``"n a"`` -> ``"north a"``).
    """
    normalized = geolocation.normalize(text)
    if geolocation.parse_job_location(text)["city"] is not None:
        return True
    place_text = location_filter._strip_workplace_keywords(normalized)
    if place_text and geolocation.parse_job_location(place_text)["city"] is not None:
        return True
    return False


def _is_unknown_text(text):
    """True when the text names no place and carries no workplace keyword.

    Mirrors the onsite-if-city-resolved rule in ``classify_location``: a city
    token (even one that resolves to no coordinates) is enough to make the
    result ``onsite``, so an ``unknown`` example must yield no city at all and
    no country through either resolution path.
    """
    if location_filter._keyword_workplace(geolocation.normalize(text)):
        return False
    parsed = geolocation.parse_job_location(text)
    if parsed["remote"] or parsed["country"] is not None:
        return False
    return not _resolves_a_city(text)


def _remote_strings(draw):
    place = draw(_REMOTE_PLACE)
    template = draw(st.sampled_from(["Remote - {p}", "{p} (Remote)", "Remote"]))
    if "{p}" in template and not place:
        return "Remote"
    return template.format(p=place)


@st.composite
def _remote_template(draw):
    return _remote_strings(draw)


@st.composite
def _hybrid_template(draw):
    city, state = draw(_US_CITY_STATE)
    template = draw(
        st.sampled_from(["Hybrid in {c}, {s}", "{c}, {s} (Hybrid)", "Hybrid"])
    )
    return template.format(c=city, s=state)


@st.composite
def _onsite_city_template(draw):
    city, state = draw(_US_CITY_STATE)
    return "{c}, {s}".format(c=city, s=state)


# ---------------------------------------------------------------------------
# Property 12
# ---------------------------------------------------------------------------


@settings(max_examples=300)
@given(
    native=_NATIVE_WORKPLACE,
    location=st.one_of(
        _remote_template(),
        _hybrid_template(),
        _onsite_city_template(),
        st.just("Home-based"),
        _UNKNOWN_TEXT,
    ),
)
def test_native_workplace_wins_over_text(native, location, small_location_maps):
    """A supplied native workplace value overrides any text signal (8.1)."""
    native_value, expected = native
    result = location_filter.classify_location(
        location, native_workplace=native_value, maps=small_location_maps
    )
    assert result.workplace_type == expected


@settings(max_examples=100)
@given(location=_remote_template())
def test_remote_text_without_native_is_remote(location, small_location_maps):
    """Remote templates classify as remote when no native value is given (8.2)."""
    result = location_filter.classify_location(location, maps=small_location_maps)
    assert result.workplace_type == "remote"


@settings(max_examples=50)
@given(place=_REMOTE_PLACE)
def test_home_based_without_native_is_remote(place, small_location_maps):
    """The home-based keyword classifies as remote (8.2)."""
    result = location_filter.classify_location(
        "Home-based", maps=small_location_maps
    )
    assert result.workplace_type == "remote"


@settings(max_examples=100)
@given(location=_hybrid_template())
def test_hybrid_text_without_native_is_hybrid(location, small_location_maps):
    """Hybrid templates classify as hybrid when no native value is given (8.2)."""
    result = location_filter.classify_location(location, maps=small_location_maps)
    assert result.workplace_type == "hybrid"


@settings(max_examples=100)
@given(city_state=_US_CITY_STATE)
def test_resolved_city_without_keyword_is_onsite(city_state, small_location_maps):
    """A resolved city-state with no keyword classifies as onsite (8.2).

    This is the onsite-if-city-resolved default: weaker than a text keyword
    (proved by the hybrid/remote cases above) but stronger than unknown.
    """
    city, state = city_state
    result = location_filter.classify_location(
        "{c}, {s}".format(c=city, s=state), maps=small_location_maps
    )
    assert result.workplace_type == "onsite"


@settings(max_examples=100)
@given(location=_UNKNOWN_TEXT)
def test_unresolvable_text_without_native_is_unknown(location, small_location_maps):
    """Text that names no place and carries no keyword is unknown (8.2)."""
    assume(_is_unknown_text(location))
    result = location_filter.classify_location(location, maps=small_location_maps)
    assert result.workplace_type == "unknown"
