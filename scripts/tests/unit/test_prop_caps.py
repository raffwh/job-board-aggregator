"""Property-based test for the per-bucket caps.

Property 11: Caps keep the first records in sort order and count the rest.

For any list of primary and maybe Job_Records with mixed Title_Priority and
caps P and Q, ``apply_caps`` keeps at most P primary and Q maybe records. The
kept records are the first P (or Q) of ``sort_by_priority_and_date`` within
each bucket, so no dropped record has a lower Title_Priority than a kept
record of the same bucket. The reported truncated count equals
``max(0, n_primary - P)`` and ``max(0, n_maybe - Q)`` respectively. Dropped
records are re-marked ``excluded`` with Exclusion_Reason ``cap_truncated``,
and records whose Match_Type is neither primary nor maybe pass through
untouched and are not capped.

Validates: Requirements 7.16
"""

from collections import Counter

from hypothesis import given, settings
from hypothesis import strategies as st

from filters import apply_caps, sort_by_priority_and_date


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# A published_at value covering the shapes parse_posted understands plus the
# undated cases (unparseable text and None), so sort order mixes dated and
# undated records within a bucket.
_published = st.one_of(
    st.builds(
        lambda y, mo, d: f"{y:04d}-{mo:02d}-{d:02d}",
        st.integers(min_value=1971, max_value=2035),
        st.integers(min_value=1, max_value=12),
        st.integers(min_value=1, max_value=28),
    ),
    st.builds(
        lambda y, mo, d, h, mi, s: f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}Z",
        st.integers(min_value=1971, max_value=2035),
        st.integers(min_value=1, max_value=12),
        st.integers(min_value=1, max_value=28),
        st.integers(min_value=0, max_value=23),
        st.integers(min_value=0, max_value=59),
        st.integers(min_value=0, max_value=59),
    ),
    st.sampled_from(["", "not a date", "soon"]),
    st.none(),
)

_priorities = st.sampled_from([1, 2, None])
_company = st.sampled_from(["alpha", "Beta", "gamma", "Acme", ""])
_title = st.sampled_from(
    ["Data Scientist", "Data Analyst", "Research Analyst", "ML Engineer", ""]
)

# Match_Type: primary and maybe are capped; the "other" values must pass
# through untouched.
_match_type = st.sampled_from(["primary", "maybe", "excluded", "other"])


@st.composite
def _job(draw, seq):
    """A Job_Record tagged with a unique ``uid`` so we can track identity."""
    return {
        "uid": seq,
        "match_type": draw(_match_type),
        "title_priority": draw(_priorities),
        "published_at": draw(_published),
        "company": draw(_company),
        "title": draw(_title),
    }


@st.composite
def jobs(draw):
    n = draw(st.integers(min_value=0, max_value=30))
    return [draw(_job(seq=i)) for i in range(n)]


_caps = st.integers(min_value=0, max_value=30)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _effective_priority(job):
    p = job.get("title_priority")
    return p if isinstance(p, int) else 2


def _uids(records):
    return [r["uid"] for r in records]


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=400)
@given(records=jobs(), primary_cap=_caps, maybe_cap=_caps)
def test_kept_count_within_caps(records, primary_cap, maybe_cap):
    n_primary = sum(1 for r in records if r["match_type"] == "primary")
    n_maybe = sum(1 for r in records if r["match_type"] == "maybe")

    kept, _ = apply_caps(records, primary_cap, maybe_cap)

    kept_primary = [r for r in kept if r["match_type"] == "primary"]
    kept_maybe = [r for r in kept if r["match_type"] == "maybe"]

    assert len(kept_primary) == min(n_primary, primary_cap)
    assert len(kept_maybe) == min(n_maybe, maybe_cap)
    # kept is exactly the kept primary records followed by the kept maybe ones.
    assert len(kept) == len(kept_primary) + len(kept_maybe)


@settings(max_examples=400)
@given(records=jobs(), primary_cap=_caps, maybe_cap=_caps)
def test_truncated_counts_are_exact(records, primary_cap, maybe_cap):
    n_primary = sum(1 for r in records if r["match_type"] == "primary")
    n_maybe = sum(1 for r in records if r["match_type"] == "maybe")

    _, truncated = apply_caps(records, primary_cap, maybe_cap)

    assert truncated == {
        "primary": max(0, n_primary - primary_cap),
        "maybe": max(0, n_maybe - maybe_cap),
    }


@settings(max_examples=400)
@given(records=jobs(), primary_cap=_caps, maybe_cap=_caps)
def test_kept_records_are_the_sort_order_prefix(records, primary_cap, maybe_cap):
    # Capture the two buckets before the call mutates match_type on drops.
    primary_before = [r for r in records if r["match_type"] == "primary"]
    maybe_before = [r for r in records if r["match_type"] == "maybe"]

    expected_primary = _uids(sort_by_priority_and_date(primary_before))[:primary_cap]
    expected_maybe = _uids(sort_by_priority_and_date(maybe_before))[:maybe_cap]

    kept, _ = apply_caps(records, primary_cap, maybe_cap)

    kept_primary = [r for r in kept if r["match_type"] == "primary"]
    kept_maybe = [r for r in kept if r["match_type"] == "maybe"]

    assert _uids(kept_primary) == expected_primary
    assert _uids(kept_maybe) == expected_maybe


@settings(max_examples=400)
@given(records=jobs(), primary_cap=_caps, maybe_cap=_caps)
def test_no_dropped_record_outranks_a_kept_one_in_its_bucket(records, primary_cap, maybe_cap):
    # Track identity and original bucket before the in-place re-marking.
    orig_bucket = {r["uid"]: r["match_type"] for r in records}
    by_uid = {r["uid"]: r for r in records}

    kept, _ = apply_caps(records, primary_cap, maybe_cap)
    kept_uids = set(_uids(kept))

    for bucket in ("primary", "maybe"):
        bucket_uids = [u for u, b in orig_bucket.items() if b == bucket]
        kept_in_bucket = [by_uid[u] for u in bucket_uids if u in kept_uids]
        dropped_in_bucket = [by_uid[u] for u in bucket_uids if u not in kept_uids]
        if not kept_in_bucket or not dropped_in_bucket:
            continue
        worst_kept = max(_effective_priority(j) for j in kept_in_bucket)
        best_dropped = min(_effective_priority(j) for j in dropped_in_bucket)
        # No dropped record has a strictly better (lower) priority than the
        # worst kept record of the same bucket.
        assert best_dropped >= worst_kept


@settings(max_examples=400)
@given(records=jobs(), primary_cap=_caps, maybe_cap=_caps)
def test_dropped_records_are_marked_cap_truncated(records, primary_cap, maybe_cap):
    orig_bucket = {r["uid"]: r["match_type"] for r in records}
    by_uid = {r["uid"]: r for r in records}

    kept, _ = apply_caps(records, primary_cap, maybe_cap)
    kept_uids = set(_uids(kept))

    for uid, bucket in orig_bucket.items():
        if bucket in ("primary", "maybe") and uid not in kept_uids:
            job = by_uid[uid]
            assert job["match_type"] == "excluded"
            assert job["exclusion_reason"] == "cap_truncated"


@settings(max_examples=400)
@given(records=jobs(), primary_cap=_caps, maybe_cap=_caps)
def test_other_match_types_pass_through_untouched(records, primary_cap, maybe_cap):
    # Records that are neither primary nor maybe are never capped and keep
    # their original match_type.
    others_before = {
        r["uid"]: r["match_type"]
        for r in records
        if r["match_type"] not in ("primary", "maybe")
    }

    kept, _ = apply_caps(records, primary_cap, maybe_cap)
    kept_by_uid = {r["uid"]: r for r in records}

    for uid, mtype in others_before.items():
        # Not capped (unchanged match_type) and not added to the kept list.
        assert kept_by_uid[uid]["match_type"] == mtype
        assert uid not in set(_uids(kept))


@settings(max_examples=200)
@given(records=jobs(), primary_cap=_caps, maybe_cap=_caps)
def test_no_records_are_lost(records, primary_cap, maybe_cap):
    # apply_caps partitions by match_type and never invents or loses records;
    # every input uid is still present afterwards.
    before = Counter(_uids(records))
    apply_caps(records, primary_cap, maybe_cap)
    after = Counter(r["uid"] for r in records)
    assert after == before
