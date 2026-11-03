"""Property-based test for Workday slug parse/format/validate.

Property 4: Workday slug round trip and validation.

For any tenant matching ``[a-z0-9_-]+`` that is not of the form
``wd<digits>``, any positive instance number N, and any site ID matching
``[A-Za-z0-9_-]+``, formatting then parsing returns the same parts, and parsing
then formatting a valid slug returns the slug in lowercase. For any string that
does not match the Workday_Slug format, or whose tenant is ``wd<digits>``,
validation rejects the string (and parsing returns ``None``).

Validates: Requirements 3.11, 3.12
"""

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from ats_helpers import (
    format_workday_slug,
    parse_workday_slug,
    validate_workday_slug,
)

_WD_INSTANCE_RE = re.compile(r"^wd\d+$", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Generators for valid slug parts
# ---------------------------------------------------------------------------

# Tenant characters: the Workday_Slug format is matched case-insensitively, so a
# tenant matches ``[A-Za-z0-9_-]+``. We lowercase generated parts so "the same
# parts" is unambiguous across the lowercasing that format/parse apply.
_tenant_chars = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=20
)
_site_chars = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=20
)


@st.composite
def valid_tenants(draw):
    """A lowercase tenant matching ``[a-z0-9_-]+`` that is not ``wd<digits>``."""
    tenant = draw(_tenant_chars)
    # Exclude the ``wdN`` shape, which the validator rejects as a tenant.
    while _WD_INSTANCE_RE.match(tenant):
        tenant = draw(_tenant_chars)
    return tenant


valid_instances = st.integers(min_value=1, max_value=10_000)
valid_site_ids = _site_chars


@st.composite
def valid_slug_parts(draw):
    return draw(valid_tenants()), draw(valid_instances), draw(valid_site_ids)


# ---------------------------------------------------------------------------
# Generators for invalid slugs
# ---------------------------------------------------------------------------


@st.composite
def tenant_is_wd_instance(draw):
    """``wdN|wdM|site`` - well formed shape, but tenant is a ``wd<digits>``."""
    n = draw(st.integers(min_value=0, max_value=999))
    m = draw(valid_instances)
    site = draw(valid_site_ids)
    return f"wd{n}|wd{m}|{site}"


@st.composite
def missing_segments(draw):
    """Slugs with the wrong number of pipe-delimited segments."""
    tenant = draw(valid_tenants())
    instance = draw(valid_instances)
    site = draw(valid_site_ids)
    shape = draw(st.sampled_from(["no_pipe", "one_pipe", "trailing", "four"]))
    if shape == "no_pipe":
        return tenant
    if shape == "one_pipe":
        return f"{tenant}|wd{instance}"
    if shape == "trailing":
        return f"{tenant}|wd{instance}|{site}|"
    return f"{tenant}|wd{instance}|{site}|extra"


@st.composite
def non_numeric_instance(draw):
    """Middle segment is present but is not a ``wd<digits>`` token."""
    tenant = draw(valid_tenants())
    site = draw(valid_site_ids)
    middle = draw(
        st.sampled_from(["wd", "wdx", "wd1a", "xwd1", " wd1", "1", "workday"])
    )
    return f"{tenant}|{middle}|{site}"


@st.composite
def empty_component(draw):
    """A well-piped slug with one empty segment."""
    tenant = draw(valid_tenants())
    instance = draw(valid_instances)
    site = draw(valid_site_ids)
    which = draw(st.integers(min_value=0, max_value=2))
    parts = [tenant, f"wd{instance}", site]
    parts[which] = ""
    return "|".join(parts)


invalid_slugs = st.one_of(
    tenant_is_wd_instance(),
    missing_segments(),
    non_numeric_instance(),
    empty_component(),
)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=400)
@given(parts=valid_slug_parts())
def test_valid_slug_round_trips(parts):
    tenant, instance, site_id = parts
    slug = format_workday_slug(tenant, instance, site_id)

    # The formatted slug is a valid Workday_Slug and is lowercase.
    assert validate_workday_slug(slug)
    assert slug == slug.lower()

    # format then parse returns the same parts (3.12).
    parsed = parse_workday_slug(slug)
    assert parsed == (tenant, instance, site_id)

    # parse then format reproduces the slug in lowercase (3.12).
    p_tenant, p_instance, p_site = parsed
    assert format_workday_slug(p_tenant, p_instance, p_site) == slug.lower()


@settings(max_examples=400)
@given(parts=valid_slug_parts())
def test_parse_lowercases_mixed_case_slug(parts):
    """Parsing a valid mixed-case slug returns lowercased tenant/site_id and
    formatting those parts reproduces the original slug lowercased (3.12)."""
    tenant, instance, site_id = parts
    # Build a mixed-case slug by upper-casing the alpha characters.
    mixed = f"{tenant.upper()}|WD{instance}|{site_id.upper()}"

    assert validate_workday_slug(mixed)
    parsed = parse_workday_slug(mixed)
    assert parsed == (tenant, instance, site_id)

    p_tenant, p_instance, p_site = parsed
    assert format_workday_slug(p_tenant, p_instance, p_site) == mixed.lower()


@settings(max_examples=400)
@given(slug=invalid_slugs)
def test_invalid_slug_is_rejected(slug):
    """Strings that do not match the Workday_Slug format, or whose tenant is a
    ``wd<digits>`` segment, are rejected by validation and parse to ``None``
    (3.11)."""
    assert validate_workday_slug(slug) is False
    assert parse_workday_slug(slug) is None
