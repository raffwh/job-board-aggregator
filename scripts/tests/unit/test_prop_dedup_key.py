"""Property-based test for Dedup_Key stability.

Property 25: Dedup keys are stable.

For any Job_Record, ``get_dedup_key`` returns the same key on repeated calls
and is unchanged when fields that are not part of a posting's identity change
(``scraped_at``, ``first_seen``, ``skills``, ``summary``, ``jd_backend``,
``match_type``, ``match_reason``, or ``distance_miles``). For any Workday
Job_Record with a requisition ID, the key equals
``workday:<tenant>:<req_id>``, where the tenant is parsed from the
Workday_Slug.

Validates: Requirements 13.1, 13.3
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from ats_helpers import format_workday_slug
from merge_data import get_dedup_key


# ---------------------------------------------------------------------------
# Generators for identity parts
# ---------------------------------------------------------------------------

# Slug parts, matching the generators used by the Workday slug property test so
# the slug is a valid Workday_Slug that ``parse_workday_slug`` accepts.
_slug_chars = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=15
)


@st.composite
def _tenants(draw):
    """A lowercase tenant that is not of the ``wd<digits>`` form."""
    tenant = draw(_slug_chars)
    while tenant.startswith("wd") and tenant[2:].isdigit() and len(tenant) > 2:
        tenant = draw(_slug_chars)
    return tenant


# Requisition IDs the way ``extract_req_id`` would hand them over: non-empty
# tokens such as "R1575320", "JR-12345", or a bare numeric id.
_req_ids = st.one_of(
    st.from_regex(r"R\d{3,7}", fullmatch=True),
    st.from_regex(r"JR-\d{3,6}", fullmatch=True),
    st.from_regex(r"REQ-\d{3,6}", fullmatch=True),
    st.from_regex(r"\d{3,8}", fullmatch=True),
)

_urls = st.from_regex(r"https://[a-z0-9]{3,10}\.example\.com/job/[a-z0-9/_-]{1,20}",
                      fullmatch=True)

_titles = st.text(min_size=1, max_size=30)
_companies = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz ", min_size=1, max_size=20
)


# ---------------------------------------------------------------------------
# Generators for whole Job_Records
# ---------------------------------------------------------------------------


@st.composite
def _workday_with_req(draw):
    """A Workday record carrying a requisition ID and a valid Workday_Slug."""
    tenant = draw(_tenants())
    instance = draw(st.integers(min_value=1, max_value=9999))
    site_id = draw(_slug_chars)
    slug = format_workday_slug(tenant, instance, site_id)
    req_id = draw(_req_ids)
    return {
        "ats": "Workday",
        "company_slug": slug,
        "company": tenant,
        "req_id": req_id,
        "url": draw(_urls),
        "title": draw(_titles),
    }, f"workday:{tenant}:{req_id}"


@st.composite
def _workday_without_req(draw):
    """A Workday record with no requisition ID; keyed on its URL."""
    tenant = draw(_tenants())
    instance = draw(st.integers(min_value=1, max_value=9999))
    site_id = draw(_slug_chars)
    url = draw(_urls)
    return {
        "ats": "Workday",
        "company_slug": format_workday_slug(tenant, instance, site_id),
        "company": tenant,
        "req_id": draw(st.sampled_from(["", None])),
        "url": url,
        "title": draw(_titles),
    }


@st.composite
def _paylocity_with_id(draw):
    """A Paylocity record carrying an id; keyed on ``paylocity:<id>``."""
    jid = draw(st.from_regex(r"[0-9]{3,8}", fullmatch=True))
    return {
        "ats": "Paylocity",
        "id": jid,
        "url": draw(_urls),
        "company": draw(_companies),
        "title": draw(_titles),
    }


@st.composite
def _paylocity_without_id(draw):
    """A Paylocity record with no id; keyed on company+title."""
    return {
        "ats": "Paylocity",
        "id": draw(st.sampled_from(["", None])),
        "url": draw(_urls),
        "company": draw(_companies),
        "title": draw(_titles),
    }


@st.composite
def _other_ats(draw):
    """A non-Workday, non-Paylocity record; keyed on its URL."""
    ats = draw(st.sampled_from(["Greenhouse", "Lever", "Ashby", "BambooHR",
                                "iCIMS", None]))
    return {
        "ats": ats,
        "url": draw(_urls),
        "company": draw(_companies),
        "title": draw(_titles),
    }


@st.composite
def _any_record(draw):
    """Any kind of Job_Record the merger may encounter."""
    kind = draw(st.sampled_from(
        ["workday_req", "workday_no_req", "paylocity_id",
         "paylocity_no_id", "other"]
    ))
    if kind == "workday_req":
        return draw(_workday_with_req())[0]
    if kind == "workday_no_req":
        return draw(_workday_without_req())
    if kind == "paylocity_id":
        return draw(_paylocity_with_id())
    if kind == "paylocity_no_id":
        return draw(_paylocity_without_id())
    return draw(_other_ats())


# Non-identity fields that must never affect the key (Requirement 13.3). Each
# maps to a strategy of plausible replacement values.
_MUTABLE_FIELDS = {
    "scraped_at": st.one_of(
        st.none(),
        st.from_regex(r"20\d\d-[01]\d-[0-3]\dT[0-2]\d:[0-5]\d:[0-5]\dZ",
                      fullmatch=True),
    ),
    "first_seen": st.one_of(
        st.none(),
        st.from_regex(r"20\d\d-[01]\d-[0-3]\d", fullmatch=True),
    ),
    "skills": st.lists(st.text(max_size=12), max_size=6),
    "summary": st.text(max_size=80),
    "jd_backend": st.sampled_from(["", "keyword", "llm", "none"]),
    "match_type": st.sampled_from(["", "primary", "secondary", "remote"]),
    "match_reason": st.text(max_size=40),
    "distance_miles": st.one_of(
        st.none(), st.floats(min_value=0, max_value=5000, allow_nan=False)
    ),
}


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=400)
@given(job=_any_record())
def test_dedup_key_is_deterministic(job):
    """Computing the key twice on the same record returns the same key."""
    assert get_dedup_key(job) == get_dedup_key(dict(job))


@settings(max_examples=400)
@given(job=_any_record(), data=st.data())
def test_dedup_key_unchanged_by_mutable_fields(job, data):
    """Changing any non-identity field never changes the key (13.3)."""
    baseline = get_dedup_key(job)

    # Mutate every non-identity field to a fresh value and re-key.
    mutated = dict(job)
    for field, strategy in _MUTABLE_FIELDS.items():
        mutated[field] = data.draw(strategy, label=field)

    assert get_dedup_key(mutated) == baseline

    # Also mutate each field in isolation so a single change is covered too.
    for field, strategy in _MUTABLE_FIELDS.items():
        one = dict(job)
        one[field] = data.draw(strategy, label=f"solo-{field}")
        assert get_dedup_key(one) == baseline


@settings(max_examples=400)
@given(payload=_workday_with_req())
def test_workday_req_id_key_shape(payload):
    """A Workday record with a req_id keys to ``workday:<tenant>:<req_id>``
    (13.1), and remains stable under non-identity changes (13.3)."""
    job, expected = payload
    assert get_dedup_key(job) == expected

    mutated = dict(job)
    mutated["scraped_at"] = "2099-01-01T00:00:00Z"
    mutated["skills"] = ["python", "sql"]
    mutated["summary"] = "a different summary"
    mutated["match_type"] = "secondary"
    mutated["match_reason"] = "changed"
    mutated["distance_miles"] = 123.4
    assert get_dedup_key(mutated) == expected
