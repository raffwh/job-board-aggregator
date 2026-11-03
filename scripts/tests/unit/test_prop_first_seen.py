"""Property-based test for Site first-seen carry-forward.

Property 29: Site first-seen carry-forward.

For any list of previous site records (with and without ``id``, with arbitrary
``first_seen`` values), any list of new records, and any build time ``now``,
``carry_first_seen(new, previous, now)`` returns one record per new record, and
for each record:

- ``first_seen`` equals the matching previous record's ``first_seen`` when a
  previous record has the same ``id``, or the same ``url`` when that previous
  record has no ``id`` (Requirement 14.15);
- otherwise ``first_seen`` equals the new record's own ``first_seen`` when
  present, else ``now`` (Requirement 14.16);
- when ``previous`` is ``None`` every record follows the second rule.

The result has one record per new record and each record keeps its ``id``.

Validates: Requirements 14.15, 14.16
"""

# Feature: job-aggregator-improvements, Property 29: Site first-seen carry-forward

from hypothesis import given, settings
from hypothesis import strategies as st

from build_site import carry_first_seen


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# A small shared pool of identifiers and URLs so new and previous records
# collide often enough to exercise both the matched and unmatched branches.
_ids = st.sampled_from(
    [
        "workday:acme:R100",
        "workday:acme:R200",
        "paylocity:12345",
        "greenhouse:https://boards.example.com/acme/jobs/9",
        "lever:https://jobs.example.com/acme/abc-def",
    ]
)

_urls = st.sampled_from(
    [
        "https://boards.example.com/acme/jobs/9",
        "https://jobs.example.com/acme/abc-def",
        "https://acme.example.com/careers/42",
        "https://acme.example.com/careers/43",
    ]
)

# ISO-ish timestamps plus the falsy values the function treats as "absent".
_timestamps = st.from_regex(
    r"20[12]\d-[01]\d-[0-3]\dT[0-2]\d:[0-5]\d:[0-5]\dZ", fullmatch=True
)
_first_seen_values = st.one_of(_timestamps, st.just(""), st.none())

# A build time distinct in shape from the generated first_seen values so the
# "fall back to now" branch is easy to detect.
_now = st.from_regex(
    r"20[34]\d-[01]\d-[0-3]\dT[0-2]\d:[0-5]\d:[0-5]\dZ", fullmatch=True
)


@st.composite
def _previous_record(draw):
    """A previous site record, sometimes without an ``id`` (legacy)."""
    rec = {"url": draw(_urls), "first_seen": draw(_first_seen_values)}
    # Legacy records (predating the id field) have no id; model that with a
    # mix of truthy ids, falsy ids, and a missing id key entirely.
    kind = draw(st.sampled_from(["id", "falsy_id", "no_id"]))
    if kind == "id":
        rec["id"] = draw(_ids)
    elif kind == "falsy_id":
        rec["id"] = draw(st.sampled_from(["", None]))
    return rec


@st.composite
def _new_record(draw):
    """A freshly built record awaiting its carried-forward first_seen."""
    rec = {"url": draw(_urls), "title": draw(st.text(max_size=20))}
    kind = draw(st.sampled_from(["id", "falsy_id", "no_id"]))
    if kind == "id":
        rec["id"] = draw(_ids)
    elif kind == "falsy_id":
        rec["id"] = draw(st.sampled_from(["", None]))
    # The new record may or may not carry its own first_seen already.
    if draw(st.booleans()):
        rec["first_seen"] = draw(_first_seen_values)
    return rec


_previous_lists = st.one_of(
    st.none(),
    st.lists(_previous_record(), max_size=6),
)
_new_lists = st.lists(_new_record(), max_size=6)


# ---------------------------------------------------------------------------
# Reference oracle
# ---------------------------------------------------------------------------


def _expected_first_seen(job, previous, now):
    """Independent re-derivation of the carry-forward rule for one job.

    Matches by id first (truthy id), then by url against previous records that
    have no truthy id. The first such previous record wins. A match counts only
    when it carries a truthy first_seen; otherwise the job keeps its own
    first_seen when present, else ``now``.
    """
    by_id = {}
    by_url = {}
    for rec in previous or []:
        if not isinstance(rec, dict):
            continue
        rid = rec.get("id")
        if rid:
            by_id.setdefault(rid, rec)
        else:
            url = rec.get("url")
            if url:
                by_url.setdefault(url, rec)

    match = None
    jid = job.get("id")
    if jid:
        match = by_id.get(jid)
    if match is None:
        url = job.get("url")
        if url:
            match = by_url.get(url)

    if match is not None and match.get("first_seen"):
        return match.get("first_seen")
    return job.get("first_seen") or now


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=400)
@given(new=_new_lists, previous=_previous_lists, now=_now)
def test_carry_first_seen_matches_reference(new, previous, now):
    """Every record's first_seen follows the carry-forward rule (14.15, 14.16).

    The result has one record per new record and each record keeps its id.
    """
    # Preserve the inputs the function mutates in place for an independent check.
    expected = [_expected_first_seen(dict(job), previous, now) for job in new]
    original_ids = [job.get("id") for job in new]

    result = carry_first_seen(new, previous, now)

    # One record per new record.
    assert len(result) == len(expected)

    for record, want, original_id in zip(result, expected, original_ids):
        # The carried (or defaulted) first_seen matches the reference.
        assert record["first_seen"] == want
        # Ids are preserved, not rewritten by the carry-forward step.
        assert record.get("id") == original_id
        # First_seen is always populated (never left falsy).
        assert record["first_seen"]


@settings(max_examples=200)
@given(new=_new_lists, now=_now)
def test_none_previous_uses_own_first_seen_or_now(new, now):
    """previous=None (missing/corrupt file) falls through to own or now (14.16)."""
    # Capture each record's own first_seen before the in-place mutation.
    own = [job.get("first_seen") for job in new]

    result = carry_first_seen(new, None, now)

    assert len(result) == len(new)
    for record, own_value in zip(result, own):
        # With no previous data, the rule is exactly own-first-seen-or-now.
        assert record["first_seen"] == (own_value or now)
        assert record["first_seen"]


@settings(max_examples=300)
@given(
    first_seen=_timestamps,
    url=_urls,
    jid=_ids,
    now=_now,
)
def test_matched_by_id_carries_previous_first_seen(first_seen, url, jid, now):
    """A new record sharing an id with a previous record inherits its
    first_seen, regardless of its own value (14.15)."""
    previous = [{"id": jid, "url": "https://other.example.com/x",
                 "first_seen": first_seen}]
    new = [{"id": jid, "url": url, "first_seen": "2024-01-01T00:00:00Z"}]

    carry_first_seen(new, previous, now)

    assert new[0]["first_seen"] == first_seen


@settings(max_examples=300)
@given(first_seen=_timestamps, url=_urls, now=_now)
def test_matched_by_url_when_previous_lacks_id(first_seen, url, now):
    """A new record matches a previous record on url when that previous record
    has no id (14.15)."""
    previous = [{"url": url, "first_seen": first_seen}]  # no id => legacy record
    new = [{"url": url}]  # also no id, carries no first_seen of its own

    carry_first_seen(new, previous, now)

    assert new[0]["first_seen"] == first_seen
