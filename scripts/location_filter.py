# location_filter.py
"""
Location classifier and (later) Greater-Boston radius filter.

This module turns a Job_Record's raw location data into a structured
``LocationResult`` describing the workplace type, the resolved country or
region, coordinates, and (for US places) the two-letter state code.

Task 6.3 implements the Location_Classifier: ``LocationResult``,
``classify_location``, and ``classify_job_locations``.

Task 6.7 adds ``haversine_miles`` and ``apply_location_rule`` (the
Location_Filter) to this same module, so the public surface is laid out with
that follow-up in mind.

Target: Python 3.9 (no PEP 604 ``X | Y`` runtime annotations).
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

try:
    # Flat layout: scripts/ on sys.path (matches scraper.py / geolocation.py).
    import geolocation
except ImportError:  # pragma: no cover - package-style import fallback
    from scripts import geolocation  # type: ignore


# ---- Keyword sets (design Component 5, step 2) ----

REMOTE_KEYWORDS = (
    "remote",
    "anywhere",
    "work from home",
    "wfh",
    "home-based",
    "home based",
    "virtual",
    "telecommute",
    "distributed",
)

HYBRID_KEYWORDS = (
    "flexible hybrid",
    "hybrid",
)

ONSITE_KEYWORDS = (
    "on-site",
    "onsite",
    "on site",
    "in-office",
    "in office",
)

# Non-US regions and region-level aliases. The value is the region code (or an
# ISO-2 country code) recorded on the result. These are matched as whole
# tokens on the normalized string (Requirement 8.3).
REGION_CODES = {
    "emea": "EMEA",
    "apac": "APAC",
    "latam": "LATAM",
    "europe": "EU",
    "eu": "EU",
    "asia": "APAC",
    "uk": "GB",
    "india": "IN",
}

# Phrases that name a cross-border area with no single country. They carry no
# usable country, so a remote job tagged this way is "unspecified".
NEUTRAL_REGIONS = (
    "north america",
    "americas",
)


# ---- Result type ----


@dataclass(frozen=True)
class LocationResult:
    """One classified location for a Job_Record."""

    workplace_type: str                      # remote | hybrid | onsite | unknown
    status: str                              # resolved | ambiguous | unknown
    country: Optional[str]                   # ISO-2 or region code (EMEA, APAC, ...)
    remote_scope: Optional[str]              # us | unspecified | None
    coords: Optional[Tuple[float, float]]    # (lat, lng) or None
    state: Optional[str]                     # two-letter US state code or None


# ---- Helpers ----


def _normalize_native_workplace(native_workplace):
    """Map a platform-native workplace value to our vocabulary, or None.

    Accepts common spellings such as ``"on-site"``, ``"On Site"``, ``True``
    (treated as remote when it came from an ``isRemote`` flag the caller
    already resolved to the string ``"remote"``). Unknown values return None so
    the keyword/city fallback decides (Requirement 8.1, 8.2).
    """
    if native_workplace is None:
        return None
    value = str(native_workplace).strip().lower()
    if not value:
        return None
    if value in ("remote", "fully remote", "fullyremote"):
        return "remote"
    if value in ("hybrid", "flexible hybrid", "flexiblehybrid"):
        return "hybrid"
    if value in ("onsite", "on-site", "on site", "in-office", "in office", "in office/onsite"):
        return "onsite"
    return None


def _native_country_is_us(native_country):
    """True when the platform-native country field names the US."""
    if native_country is None:
        return False
    value = str(native_country).strip().lower()
    if not value:
        return False
    if geolocation.COUNTRY_ALIASES.get(value) == "US":
        return True
    # Already an ISO-2 code, e.g. "US".
    return value.upper() == "US"


def _keyword_workplace(normalized):
    """Workplace type implied by keywords in the normalized string, or None."""
    for kw in REMOTE_KEYWORDS:
        if kw in normalized:
            return "remote"
    for kw in HYBRID_KEYWORDS:
        if kw in normalized:
            return "hybrid"
    for kw in ONSITE_KEYWORDS:
        if kw in normalized:
            return "onsite"
    return None


def _strip_workplace_keywords(normalized):
    """Remove workplace keywords, separators, and parentheses for lookup.

    The remainder is passed to ``lookup_location_detailed`` so that a string
    like ``"remote - boston, ma"`` still resolves the place (design step 3).
    """
    text = normalized
    # Drop parenthesized groups, e.g. "boston (remote)".
    text = re.sub(r"\([^)]*\)", " ", text)
    for kw in REMOTE_KEYWORDS + HYBRID_KEYWORDS + ONSITE_KEYWORDS:
        text = re.sub(r"\b" + re.escape(kw) + r"\b", " ", text)
    # Collapse leftover separators and whitespace.
    text = re.sub(r"[\-\u2014/|]", " ", text)
    text = " ".join(text.split())
    return text.strip(" ,-\u2014")


def _find_region(normalized):
    """Return (code, is_neutral) for a region/country-region match, else (None, False)."""
    for phrase in NEUTRAL_REGIONS:
        if re.search(r"\b" + re.escape(phrase) + r"\b", normalized):
            return None, True
    for token, code in REGION_CODES.items():
        if re.search(r"\b" + re.escape(token) + r"\b", normalized):
            return code, False
    return None, False


def _text_names_us(normalized):
    """True when the normalized text names the US as a country alias."""
    for alias, code in geolocation.COUNTRY_ALIASES.items():
        if code != "US":
            continue
        if re.search(r"\b" + re.escape(alias) + r"\b", normalized):
            return True
    return False


# ---- Classifier (design Component 5) ----


def classify_location(location, native_workplace=None, native_country=None, maps=None):
    """Classify one location string into a ``LocationResult``.

    Steps (design Component 5):
    1. Normalize with ``geolocation.normalize``.
    2. Determine the keyword workplace type (remote / hybrid / onsite).
    3. Strip keywords and run the remainder through ``lookup_location_detailed``.
    4. Record country/region (regions, ``COUNTRY_ALIASES``, US state markers).
    5. Workplace precedence: native, then keywords, then onsite when a city
       resolved, otherwise unknown.
    6. Status and ``remote_scope`` per Requirements 8.6, 8.7, 8.8.

    Coordinates are only kept when they fall in valid lat/lng ranges
    (Requirement 8.10).
    """
    normalized = geolocation.normalize(location)

    native_wp = _normalize_native_workplace(native_workplace)
    keyword_wp = _keyword_workplace(normalized)

    region_code, region_neutral = _find_region(normalized)

    # Detailed lookup for the place part. geolocation already normalizes,
    # strips work-arrangement prefixes ("hybrid in ...", "onsite in ...") and
    # parentheticals, and recovers the country for remote strings, so the
    # original string is handed over directly (design step 3). Only when that
    # yields nothing do we retry on a keyword-stripped remainder, which rescues
    # formats geolocation does not special-case (e.g. "Remote - Boston, MA").
    # When no maps are supplied we skip the lookup and rely on text signals.
    detailed = {"city": None, "admin": None, "country": None,
                "coords": None, "ambiguous": False}
    if maps is not None:
        detailed = geolocation.lookup_location_detailed(location, maps)
        if detailed.get("coords") is None and detailed.get("city") is None:
            place_text = _strip_workplace_keywords(normalized)
            if place_text:
                retry = geolocation.lookup_location_detailed(place_text, maps)
                if retry.get("coords") is not None or retry.get("city") is not None:
                    detailed = retry

    # --- Country / region resolution (step 4) ---
    country = detailed.get("country")
    if country is None and region_code is not None:
        country = region_code

    # A US state name or abbreviation in city-state position (via the detailed
    # lookup's admin code on a US row) means the country is US.
    state = None
    admin = detailed.get("admin")
    if country == "US" and admin:
        state = str(admin).upper()
    elif country is None and not region_neutral and _text_names_us(normalized):
        country = "US"

    coords = _valid_coords(detailed.get("coords"))
    ambiguous = bool(detailed.get("ambiguous"))

    # --- Workplace precedence (step 5) ---
    city_resolved = detailed.get("city") is not None or coords is not None
    if native_wp is not None:
        workplace_type = native_wp
    elif keyword_wp is not None:
        workplace_type = keyword_wp
    elif city_resolved:
        workplace_type = "onsite"
    else:
        workplace_type = "unknown"

    # --- Status and remote_scope (step 6) ---
    is_non_us_region = country is not None and country != "US"

    if workplace_type == "remote":
        if is_non_us_region:
            # Remote with a recorded non-US country/region: resolved, no scope.
            return LocationResult(
                workplace_type="remote",
                status="resolved",
                country=country,
                remote_scope=None,
                coords=coords,
                state=state,
            )
        # A neutral cross-border region ("North America", "Americas") names no
        # single country, so it must not be read as the US even though the
        # "america" alias appears inside it (design step 6).
        names_us = (
            country == "US"
            or state is not None
            or (not region_neutral and _text_names_us(normalized))
            or _native_country_is_us(native_country)
        )
        remote_scope = "us" if names_us else "unspecified"
        return LocationResult(
            workplace_type="remote",
            status="resolved",
            country="US" if names_us else None,
            remote_scope=remote_scope,
            coords=coords,
            state=state,
            )

    # Non-remote (hybrid / onsite / unknown).
    if ambiguous:
        return LocationResult(
            workplace_type=workplace_type,
            status="ambiguous",
            country=country,
            remote_scope=None,
            coords=coords,
            state=state,
        )

    if coords is not None:
        return LocationResult(
            workplace_type=workplace_type,
            status="resolved",
            country=country,
            remote_scope=None,
            coords=coords,
            state=state,
        )

    if country is not None:
        # Country but no city. Non-US country is a resolved placement signal;
        # "United States" alone carries no usable place for the radius rule.
        status = "resolved" if country != "US" else "unknown"
        return LocationResult(
            workplace_type=workplace_type,
            status=status,
            country=country,
            remote_scope=None,
            coords=None,
            state=state,
        )

    # Neither coordinates nor a country (Requirement 8.8).
    return LocationResult(
        workplace_type=workplace_type,
        status="unknown",
        country=None,
        remote_scope=None,
        coords=None,
        state=None,
    )


def _valid_coords(coords):
    """Return a (lat, lng) tuple when within valid ranges, else None (8.10)."""
    if coords is None:
        return None
    try:
        lat, lng = coords[0], coords[1]
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError, IndexError):
        return None
    if -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0:
        return (lat, lng)
    return None


def classify_job_locations(job, maps):
    """Classify every location of a Job_Record (Requirement 8.9 reclassify-ready).

    Produces one ``LocationResult`` per entry of ``job["locations"]`` when that
    list is present and non-empty, otherwise one result for ``job["location"]``.
    Native workplace and country fields on the Job_Record are applied to each
    classification so platform-native data takes precedence (Requirement 8.1).
    """
    native_workplace = job.get("workplace_type") or job.get("native_workplace")
    native_country = job.get("country") or job.get("native_country")

    locations = job.get("locations")
    if not locations:
        locations = [job.get("location")]

    results = []
    for loc in locations:
        results.append(
            classify_location(
                loc,
                native_workplace=native_workplace,
                native_country=native_country,
                maps=maps,
            )
        )
    return results


# ---- Radius filter (design Component 5, Location_Filter) ----

import math

try:
    import personal_config
except ImportError:  # pragma: no cover - package-style import fallback
    from scripts import personal_config  # type: ignore


# Mean Earth radius in miles (design: R = 3958.8 mi).
_EARTH_RADIUS_MILES = 3958.8


def haversine_miles(a, b):
    """Great-circle distance in miles between two (lat, lng) points.

    Uses the haversine formula with mean Earth radius R = 3958.8 miles. The
    result is non-negative, 0 for identical points, and symmetric in its two
    arguments (Requirements 9.1, 9.9).
    """
    lat1, lng1 = float(a[0]), float(a[1])
    lat2, lng2 = float(b[0]), float(b[1])

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)

    h = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    # Clamp against floating-point drift so asin stays in [0, 1].
    h = min(1.0, max(0.0, h))
    return 2.0 * _EARTH_RADIUS_MILES * math.asin(math.sqrt(h))


@dataclass(frozen=True)
class LocationVerdict:
    """Job-level outcome of the Location_Filter for a Job_Record.

    ``finalize_decision`` reads ``keep``, ``exclusion_reason``, ``maybe_reason``
    (the Secondary_Region / remote-unspecified / unknown signal),
    ``remote_scope`` and ``status`` (the Location_Status) from this verdict.
    """

    keep: bool
    maybe_reason: Optional[str]      # None | location_new_england | remote_unspecified | location_unknown
    exclusion_reason: Optional[str]  # non_us | outside_radius | None
    status: str                      # resolved | ambiguous | unknown
    remote_scope: Optional[str]      # us | unspecified | None
    distance_miles: Optional[float]
    workplace_type: str
    coords: Optional[Tuple[float, float]]
    state: Optional[str]


# Per-location verdict kinds, used internally to build the job-level verdict.
_KEPT = "kept"
_SECONDARY = "secondary"
_REMOTE_UNSPECIFIED = "remote_unspecified"
_UNDECIDED = "undecided"
_REJECT_NON_US = "reject_non_us"
_REJECT_OUTSIDE = "reject_outside_radius"


def _per_location_verdict(result, center, radius_miles, secondary_states):
    """Classify one ``LocationResult`` into a (kind, distance) pair.

    Follows the per-location verdict table in design Component 5. ``distance``
    is the great-circle distance in miles when the location has usable
    coordinates, otherwise None.
    """
    workplace = result.workplace_type
    coords = result.coords

    if workplace == "remote":
        if result.remote_scope == "us":
            return _KEPT, None
        # A recorded non-US country or region rejects the remote job (9.3).
        if result.country is not None and result.country != "US":
            return _REJECT_NON_US, None
        # No country at all -> unspecified scope (9.15).
        return _REMOTE_UNSPECIFIED, None

    # hybrid / onsite / unknown.
    if coords is not None:
        distance = haversine_miles(center, coords)
        if distance <= radius_miles:
            return _KEPT, distance
        if result.state is not None and result.state in secondary_states:
            return _SECONDARY, distance
        return _REJECT_OUTSIDE, distance

    # No coordinates. A non-US country is a non-US placement signal.
    if result.country is not None and result.country != "US":
        return _REJECT_NON_US, None

    # Ambiguous / unknown with no usable coordinates -> undecided (9.7).
    return _UNDECIDED, None


def apply_location_rule(results, center=None, radius_miles=None,
                        secondary_region_states=None):
    """Apply the US-remote-or-Greater-Boston rule to a job's locations.

    ``results`` is the list of ``LocationResult`` for one Job_Record (from
    ``classify_job_locations``). ``center`` is the (lat, lng) Boston_Center,
    ``radius_miles`` the Boston_Radius, and ``secondary_region_states`` the
    Secondary_Region state set. Any of these default to the corresponding
    ``personal_config`` value when not passed (Requirement 9.8).

    Returns a :class:`LocationVerdict`. The job-level verdict takes the first
    rule that applies (design Component 5):

    1. Any kept location -> keep, primary-eligible, smallest kept distance.
    2. Else any secondary location -> keep, ``location_new_england``.
    3. Else any remote-unspecified location -> keep, ``remote_unspecified``.
    4. Else any undecided location -> keep, ``location_unknown``.
    5. Otherwise reject: ``non_us`` when every rejection was ``non_us``, else
       ``outside_radius`` (Requirements 9.3, 9.5).
    """
    if center is None:
        center = (personal_config.BOSTON_CENTER_LAT, personal_config.BOSTON_CENTER_LNG)
    if radius_miles is None:
        radius_miles = personal_config.BOSTON_RADIUS_MILES
    if secondary_region_states is None:
        secondary_region_states = personal_config.SECONDARY_REGION_STATES

    kept = []        # (result, distance)
    secondary = []   # (result, distance)
    remote_unspec = []
    undecided = []
    rejections = []  # exclusion reason strings

    for result in results or []:
        kind, distance = _per_location_verdict(
            result, center, radius_miles, secondary_region_states
        )
        if kind == _KEPT:
            kept.append((result, distance))
        elif kind == _SECONDARY:
            secondary.append((result, distance))
        elif kind == _REMOTE_UNSPECIFIED:
            remote_unspec.append(result)
        elif kind == _UNDECIDED:
            undecided.append(result)
        elif kind == _REJECT_NON_US:
            rejections.append("non_us")
        else:  # _REJECT_OUTSIDE
            rejections.append("outside_radius")

    # 1. Any kept location -> primary-eligible, smallest kept distance.
    if kept:
        return _build_kept_verdict(kept, maybe_reason=None, status="resolved")

    # 2. Any secondary (New England outside the radius) location.
    if secondary:
        return _build_kept_verdict(
            secondary, maybe_reason="location_new_england", status="resolved"
        )

    # 3. Any remote-unspecified location.
    if remote_unspec:
        result = remote_unspec[0]
        return LocationVerdict(
            keep=True,
            maybe_reason="remote_unspecified",
            exclusion_reason=None,
            status="resolved",
            remote_scope="unspecified",
            distance_miles=None,
            workplace_type=result.workplace_type,
            coords=result.coords,
            state=result.state,
        )

    # 4. Any undecided (ambiguous/unknown) location.
    if undecided:
        # Deterministic choice so the verdict does not depend on location
        # order (Requirement 9.6): prefer an "ambiguous" result over an
        # "unknown" one. Both map to the same location_unknown maybe
        # outcome; this only pins the surfaced diagnostic status.
        result = next((r for r in undecided if r.status == "ambiguous"), undecided[0])
        return LocationVerdict(
            keep=True,
            maybe_reason="location_unknown",
            exclusion_reason=None,
            status=result.status if result.status in ("ambiguous", "unknown") else "unknown",
            remote_scope=None,
            distance_miles=None,
            workplace_type=result.workplace_type,
            coords=result.coords,
            state=result.state,
        )

    # 5. Reject. non_us only when every rejection was non_us.
    if rejections and all(reason == "non_us" for reason in rejections):
        exclusion_reason = "non_us"
    else:
        exclusion_reason = "outside_radius"

    # Surface the first rejected location's details for diagnostics.
    first = results[0] if results else None
    return LocationVerdict(
        keep=False,
        maybe_reason=None,
        exclusion_reason=exclusion_reason,
        status=first.status if first is not None else "unknown",
        remote_scope=first.remote_scope if first is not None else None,
        distance_miles=None,
        workplace_type=first.workplace_type if first is not None else "unknown",
        coords=first.coords if first is not None else None,
        state=first.state if first is not None else None,
    )


def _build_kept_verdict(entries, maybe_reason, status):
    """Build a kept ``LocationVerdict`` from (result, distance) entries.

    Records the smallest distance (rounded to one decimal) and the coordinates
    of the nearest location (Requirements 9.4, 9.6, 9.16).
    """
    # Prefer the entry with the smallest known distance; fall back to the first.
    with_distance = [e for e in entries if e[1] is not None]
    if with_distance:
        result, distance = min(with_distance, key=lambda e: e[1])
        distance_miles = round(distance, 1)
    else:
        result, _ = entries[0]
        distance_miles = None
    return LocationVerdict(
        keep=True,
        maybe_reason=maybe_reason,
        exclusion_reason=None,
        status=status,
        remote_scope=result.remote_scope,
        distance_miles=distance_miles,
        workplace_type=result.workplace_type,
        coords=result.coords,
        state=result.state,
    )
