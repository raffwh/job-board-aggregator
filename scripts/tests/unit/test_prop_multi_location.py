"""Property-based test for multi-location aggregation.

Property 19: Multi-location verdict follows the precedence order.

For any non-empty list of per-location verdicts drawn from {kept, secondary,
remote-unspecified, undecided, rejected ``non_us``, rejected ``outside_radius``},
``apply_location_rule`` SHALL resolve the job-level verdict by taking the first
rule that applies, independent of the order the locations appear in the list:

1. any kept location  -> keep, primary-eligible (``maybe_reason is None``),
   status ``resolved``, with the minimum kept distance (9.6, 9.16);
2. else any secondary location -> keep, ``location_new_england``, with the
   minimum secondary distance (9.16);
3. else any remote-unspecified location -> keep, ``remote_unspecified`` (9.15);
4. else any undecided location -> keep, ``location_unknown`` (9.7);
5. else reject: ``non_us`` when every rejection was ``non_us``, otherwise
   ``outside_radius``.

The test builds ``LocationResult`` objects directly, one per verdict kind, so
the mapping from per-location verdict to job-level verdict is exercised without
depending on string parsing or the location map. A fixed Boston_Center, radius,
and Secondary_Region set make the great-circle distances deterministic, so the
"smallest kept/secondary distance" claim can be checked exactly.

Validates: Requirements 9.6, 9.7, 9.15, 9.16
"""

from hypothesis import given, settings
from hypothesis import strategies as st

import location_filter
from location_filter import LocationResult, apply_location_rule, haversine_miles


# ---------------------------------------------------------------------------
# Fixed rule parameters (so distances are deterministic in the test)
# ---------------------------------------------------------------------------

_CENTER = (42.3601, -71.0589)        # Boston
_RADIUS = 30.0                       # miles
_SECONDARY = {"MA", "NH", "RI", "CT", "VT", "ME"}  # New England


# Verdict-kind tags used by the generators below. Each maps to a builder that
# returns a (LocationResult, expected_kind, distance_or_None) triple.
_KEPT = "kept"
_SECONDARY_KIND = "secondary"
_REMOTE_UNSPEC = "remote_unspecified"
_UNDECIDED = "undecided"
_REJECT_NON_US = "reject_non_us"
_REJECT_OUTSIDE = "reject_outside_radius"


def _coords_at_distance(north_miles):
    """A (lat, lng) point roughly ``north_miles`` due north of the center.

    One degree of latitude is ~69 miles, so this gives a point whose
    great-circle distance from the center is close to (but, because of the
    haversine curve, not exactly) ``north_miles``. The exact value is read back
    with ``haversine_miles`` in the generators, so the test never assumes the
    approximation is exact.
    """
    return (_CENTER[0] + north_miles / 69.0, _CENTER[1])


# ---------------------------------------------------------------------------
# Per-location builders: each returns (LocationResult, kind, distance)
# ---------------------------------------------------------------------------


# A US-scope remote location: kept, no distance. No draw() needed, so it is a
# constant strategy rather than an @st.composite.
_kept_us_remote = st.just(
    (
        LocationResult(
            workplace_type="remote",
            status="resolved",
            country="US",
            remote_scope="us",
            coords=None,
            state=None,
        ),
        _KEPT,
        None,
    )
)


@st.composite
def _kept_onsite(draw):
    """An onsite location inside the radius: kept, with a distance."""
    miles = draw(st.floats(min_value=0.0, max_value=25.0))
    coords = _coords_at_distance(miles)
    distance = haversine_miles(_CENTER, coords)
    # Keep clear of the boundary so the kind is unambiguous.
    assert distance <= _RADIUS
    return (
        LocationResult(
            workplace_type="onsite",
            status="resolved",
            country="US",
            remote_scope=None,
            coords=coords,
            state="MA",
        ),
        _KEPT,
        distance,
    )


@st.composite
def _secondary(draw):
    """A New-England onsite location outside the radius: secondary, with a distance."""
    miles = draw(st.floats(min_value=40.0, max_value=120.0))
    coords = _coords_at_distance(miles)
    distance = haversine_miles(_CENTER, coords)
    assert distance > _RADIUS
    state = draw(st.sampled_from(sorted(_SECONDARY)))
    return (
        LocationResult(
            workplace_type="onsite",
            status="resolved",
            country="US",
            remote_scope=None,
            coords=coords,
            state=state,
        ),
        _SECONDARY_KIND,
        distance,
    )


# A remote location with no country: remote-unspecified, no distance. No draw()
# needed, so it is a constant strategy rather than an @st.composite.
_remote_unspecified = st.just(
    (
        LocationResult(
            workplace_type="remote",
            status="resolved",
            country=None,
            remote_scope="unspecified",
            coords=None,
            state=None,
        ),
        _REMOTE_UNSPEC,
        None,
    )
)


@st.composite
def _undecided(draw):
    """An ambiguous/unknown non-remote location with no coords: undecided."""
    workplace = draw(st.sampled_from(["onsite", "hybrid", "unknown"]))
    status = draw(st.sampled_from(["ambiguous", "unknown"]))
    # country None, or "US" with no coords (United States alone carries no
    # usable place for the radius rule) -> undecided either way.
    country = draw(st.sampled_from([None, "US"]))
    return (
        LocationResult(
            workplace_type=workplace,
            status=status,
            country=country,
            remote_scope=None,
            coords=None,
            state=None,
        ),
        _UNDECIDED,
        None,
    )


@st.composite
def _reject_non_us(draw):
    """A non-US location: rejected as non_us."""
    country = draw(st.sampled_from(["GB", "CA", "DE", "FR", "EMEA", "APAC", "IN"]))
    remote = draw(st.booleans())
    if remote:
        return (
            LocationResult(
                workplace_type="remote",
                status="resolved",
                country=country,
                remote_scope=None,
                coords=None,
                state=None,
            ),
            _REJECT_NON_US,
            None,
        )
    # Non-remote with a non-US country and no coords.
    return (
        LocationResult(
            workplace_type=draw(st.sampled_from(["onsite", "hybrid"])),
            status="resolved",
            country=country,
            remote_scope=None,
            coords=None,
            state=None,
        ),
        _REJECT_NON_US,
        None,
    )


@st.composite
def _reject_outside(draw):
    """A non-New-England US onsite location outside the radius: outside_radius."""
    miles = draw(st.floats(min_value=40.0, max_value=200.0))
    coords = _coords_at_distance(miles)
    distance = haversine_miles(_CENTER, coords)
    assert distance > _RADIUS
    # A US state not in the Secondary_Region set.
    state = draw(st.sampled_from(["CA", "NY", "IL", "TX", "WA", "FL"]))
    return (
        LocationResult(
            workplace_type="onsite",
            status="resolved",
            country="US",
            remote_scope=None,
            coords=coords,
            state=state,
        ),
        _REJECT_OUTSIDE,
        distance,
    )


_ANY_LOCATION = st.one_of(
    _kept_us_remote,
    _kept_onsite(),
    _secondary(),
    _remote_unspecified,
    _undecided(),
    _reject_non_us(),
    _reject_outside(),
)


# ---------------------------------------------------------------------------
# Helpers over a drawn list of (result, kind, distance) triples
# ---------------------------------------------------------------------------


def _results(triples):
    return [t[0] for t in triples]


def _kinds(triples):
    return [t[1] for t in triples]


def _min_distance(triples, kind):
    """Smallest distance among entries of ``kind`` that have one, rounded."""
    distances = [t[2] for t in triples if t[1] == kind and t[2] is not None]
    if not distances:
        return None
    return round(min(distances), 1)


def _sanity_check_kinds(triples):
    """Assert each built result maps to the kind its builder claimed.

    This guards the generators themselves: it runs the exact per-location
    classifier the rule uses and confirms the builder's expected kind, so a
    drift in the source classification can never silently weaken the property.
    """
    kind_tag = {
        "kept": _KEPT,
        "secondary": _SECONDARY_KIND,
        "remote_unspecified": _REMOTE_UNSPEC,
        "undecided": _UNDECIDED,
        "reject_non_us": _REJECT_NON_US,
        "reject_outside_radius": _REJECT_OUTSIDE,
    }
    for result, expected_kind, _ in triples:
        actual, _dist = location_filter._per_location_verdict(
            result, _CENTER, _RADIUS, _SECONDARY
        )
        assert kind_tag[actual] == expected_kind


def _apply(results, order):
    """Apply the rule to ``results`` reordered by the index list ``order``."""
    reordered = [results[i] for i in order]
    return apply_location_rule(
        reordered,
        center=_CENTER,
        radius_miles=_RADIUS,
        secondary_region_states=_SECONDARY,
    )


# ---------------------------------------------------------------------------
# Property 19
# ---------------------------------------------------------------------------


@settings(max_examples=400)
@given(
    triples=st.lists(_ANY_LOCATION, min_size=1, max_size=8),
    order=st.randoms(use_true_random=True),
)
def test_precedence_order(triples, order):
    """The job-level verdict follows the kept -> secondary -> remote-unspecified
    -> undecided -> reject precedence, with the smallest kept/secondary distance,
    independent of location order (9.6, 9.7, 9.15, 9.16)."""
    _sanity_check_kinds(triples)
    results = _results(triples)
    kinds = _kinds(triples)

    verdict = apply_location_rule(
        results,
        center=_CENTER,
        radius_miles=_RADIUS,
        secondary_region_states=_SECONDARY,
    )

    if _KEPT in kinds:
        # 1. Any kept location -> primary-eligible, smallest kept distance (9.6, 9.16).
        assert verdict.keep is True
        assert verdict.maybe_reason is None
        assert verdict.exclusion_reason is None
        assert verdict.status == "resolved"
        assert verdict.distance_miles == _min_distance(triples, _KEPT)
    elif _SECONDARY_KIND in kinds:
        # 2. Any secondary -> New England, smallest secondary distance (9.16).
        assert verdict.keep is True
        assert verdict.maybe_reason == "location_new_england"
        assert verdict.exclusion_reason is None
        assert verdict.distance_miles == _min_distance(triples, _SECONDARY_KIND)
    elif _REMOTE_UNSPEC in kinds:
        # 3. Any remote-unspecified -> remote_unspecified (9.15).
        assert verdict.keep is True
        assert verdict.maybe_reason == "remote_unspecified"
        assert verdict.exclusion_reason is None
        assert verdict.remote_scope == "unspecified"
    elif _UNDECIDED in kinds:
        # 4. Any undecided -> location_unknown (9.7).
        assert verdict.keep is True
        assert verdict.maybe_reason == "location_unknown"
        assert verdict.exclusion_reason is None
        assert verdict.status in ("ambiguous", "unknown")
    else:
        # 5. Every location rejected. non_us only when all were non_us.
        assert verdict.keep is False
        assert verdict.maybe_reason is None
        if all(k == _REJECT_NON_US for k in kinds):
            assert verdict.exclusion_reason == "non_us"
        else:
            assert verdict.exclusion_reason == "outside_radius"


@settings(max_examples=300)
@given(
    triples=st.lists(_ANY_LOCATION, min_size=1, max_size=8),
    perm=st.randoms(use_true_random=True),
)
def test_verdict_is_order_independent(triples, perm):
    """Permuting the location list does not change the job-level verdict.

    The verdict is a pure function of the multiset of per-location verdicts, so
    any ordering must produce the same keep flag, reasons, status, scope, and
    chosen distance (9.6)."""
    _sanity_check_kinds(triples)
    results = _results(triples)
    n = len(results)

    identity = list(range(n))
    shuffled = list(range(n))
    perm.shuffle(shuffled)

    base = _apply(results, identity)
    other = _apply(results, shuffled)

    assert base.keep == other.keep
    assert base.maybe_reason == other.maybe_reason
    assert base.exclusion_reason == other.exclusion_reason
    assert base.status == other.status
    assert base.remote_scope == other.remote_scope
    assert base.distance_miles == other.distance_miles


@settings(max_examples=300)
@given(
    others=st.lists(_ANY_LOCATION, min_size=0, max_size=7),
    kept=_kept_onsite(),
    pos=st.randoms(use_true_random=True),
)
def test_a_kept_location_anywhere_keeps_the_job(others, kept, pos):
    """A single kept location anywhere in the list keeps the job (9.6).

    Regardless of how many non-kept locations surround it or where it sits in
    the list, one kept location forces a primary-eligible verdict and a kept
    distance no larger than that one location's distance."""
    triples = list(others)
    insert_at = pos.randint(0, len(triples))
    triples.insert(insert_at, kept)
    _sanity_check_kinds(triples)

    verdict = apply_location_rule(
        _results(triples),
        center=_CENTER,
        radius_miles=_RADIUS,
        secondary_region_states=_SECONDARY,
    )

    assert verdict.keep is True
    assert verdict.maybe_reason is None
    assert verdict.exclusion_reason is None
    assert verdict.status == "resolved"
    # The chosen distance is the minimum over all kept locations, so it is at
    # most the distance of the kept location we inserted.
    assert verdict.distance_miles == _min_distance(triples, _KEPT)
    assert verdict.distance_miles <= round(kept[2], 1)
