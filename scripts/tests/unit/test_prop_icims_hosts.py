"""Property-based test for iCIMS host candidate resolution.

Property 5: iCIMS host candidates are correct.

For any slug, ``icims_host_candidates`` returns exactly two distinct hosts that
both end in ``.icims.com``. When the slug starts with a known subdomain prefix
the first candidate is ``<slug>.icims.com`` and the fallback is
``careers-<slug>.icims.com`` (Requirement 4.1). Otherwise the first candidate is
``careers-<slug>.icims.com`` and the fallback is ``<slug>.icims.com``
(Requirement 4.2). No first candidate starts with ``careers-careers-`` unless
the (normalized) slug itself starts with ``careers-careers-``.

Validates: Requirements 4.1, 4.2
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from ats_helpers import ICIMS_PREFIXES, icims_host_candidates

# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# Slug body characters typical of an iCIMS subdomain: lowercase letters,
# digits and hyphens. Kept non-empty so hosts are never just ".icims.com".
_slug_chars = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=1, max_size=30
)


@st.composite
def bare_slugs(draw):
    """A slug that does NOT start with any known prefix (after normalizing)."""
    s = draw(_slug_chars)
    # Normalize the way the function does, then reject anything that would be
    # treated as prefixed so this strategy only yields true "bare" slugs.
    while s.strip().lower().startswith(ICIMS_PREFIXES):
        s = draw(_slug_chars)
    return s


@st.composite
def prefixed_slugs(draw):
    """A slug that starts with a known prefix followed by a non-empty body."""
    prefix = draw(st.sampled_from(ICIMS_PREFIXES))
    body = draw(_slug_chars)
    return f"{prefix}{body}"


# Arbitrary slugs: a mix of prefixed, bare, and raw text (including mixed case
# and surrounding whitespace) to exercise the general contract.
arbitrary_slugs = st.one_of(
    prefixed_slugs(),
    bare_slugs(),
    _slug_chars,
    st.text(min_size=1, max_size=30).filter(lambda s: s.strip() != ""),
)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=400)
@given(slug=arbitrary_slugs)
def test_two_distinct_icims_hosts(slug):
    """Exactly two distinct hosts, both ending in ``.icims.com`` (4.1, 4.2)."""
    candidates = icims_host_candidates(slug)

    assert len(candidates) == 2
    assert candidates[0] != candidates[1]
    for host in candidates:
        assert host.endswith(".icims.com")


@settings(max_examples=400)
@given(slug=prefixed_slugs())
def test_prefixed_slug_order(slug):
    """A prefixed slug yields ``[<slug>.icims.com, careers-<slug>.icims.com]``
    (Requirement 4.1)."""
    s = slug.strip().lower()
    assert icims_host_candidates(slug) == [
        f"{s}.icims.com",
        f"careers-{s}.icims.com",
    ]


@settings(max_examples=400)
@given(slug=bare_slugs())
def test_bare_slug_order(slug):
    """A bare slug yields ``[careers-<slug>.icims.com, <slug>.icims.com]``
    (Requirement 4.2)."""
    s = slug.strip().lower()
    assert icims_host_candidates(slug) == [
        f"careers-{s}.icims.com",
        f"{s}.icims.com",
    ]


@settings(max_examples=400)
@given(slug=arbitrary_slugs)
def test_first_candidate_never_double_prefixes(slug):
    """The first candidate never starts with ``careers-careers-`` unless the
    normalized slug itself starts that way (4.1, 4.2)."""
    s = slug.strip().lower()
    first = icims_host_candidates(slug)[0]
    if not s.startswith("careers-careers-"):
        assert not first.startswith("careers-careers-")
