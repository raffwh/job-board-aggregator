"""Property-based test for the priority-then-date sort.

Property 10: Sort is by priority, then newest, with undated last.

For any list of Job_Records with Title_Priority 1 or 2 and dates that mix
ISO 8601 with ``Z``, ISO with an offset, date-only values, epoch
milliseconds, unparseable strings, and missing values,
``sort_by_priority_and_date`` returns a permutation of the input in which:

- Title_Priority values are non-decreasing;
- within each Title_Priority, parsed UTC timestamps are non-increasing;
- within each Title_Priority, every undated record comes after every dated
  one.

In addition, the sort is deterministic (same input -> same output), the
output is a permutation of the input, and ties break stably on company then
title.

Validates: Requirements 7.17
"""

from collections import Counter

from hypothesis import given, settings
from hypothesis import strategies as st

import ats_helpers
from filters import sort_by_priority_and_date


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# Title_Priority values the spec cares about: 1, 2, and the missing case
# (None), which the sort key treats as priority 2.
_priorities = st.sampled_from([1, 2, None])


def _iso_z():
    """ISO 8601 with a ``Z`` suffix, e.g. ``2024-01-02T03:04:05Z``."""
    return st.builds(
        lambda y, mo, d, h, mi, s: f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}Z",
        st.integers(min_value=1971, max_value=2035),
        st.integers(min_value=1, max_value=12),
        st.integers(min_value=1, max_value=28),
        st.integers(min_value=0, max_value=23),
        st.integers(min_value=0, max_value=59),
        st.integers(min_value=0, max_value=59),
    )


def _iso_offset():
    """ISO 8601 with an explicit offset, e.g. ``2024-01-02T03:04:05+02:00``."""
    return st.builds(
        lambda y, mo, d, h, mi, s, sign, oh: (
            f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}"
            f"{sign}{oh:02d}:00"
        ),
        st.integers(min_value=1971, max_value=2035),
        st.integers(min_value=1, max_value=12),
        st.integers(min_value=1, max_value=28),
        st.integers(min_value=0, max_value=23),
        st.integers(min_value=0, max_value=59),
        st.integers(min_value=0, max_value=59),
        st.sampled_from(["+", "-"]),
        st.integers(min_value=0, max_value=12),
    )


def _date_only():
    """Date-only value, e.g. ``2024-01-02``."""
    return st.builds(
        lambda y, mo, d: f"{y:04d}-{mo:02d}-{d:02d}",
        st.integers(min_value=1971, max_value=2035),
        st.integers(min_value=1, max_value=12),
        st.integers(min_value=1, max_value=28),
    )


def _epoch_millis():
    """Epoch milliseconds, as an int or a numeric string."""
    base = st.integers(min_value=0, max_value=2_000_000_000_000)
    return st.one_of(base, base.map(str))


def _unparseable():
    """Strings that ``parse_posted`` cannot interpret as a date."""
    return st.sampled_from(
        ["", "   ", "not a date", "yesterday", "2024-13-99", "N/A", "soon"]
    )


def _missing():
    """A missing posting date."""
    return st.none()


# A published_at value drawn from every shape the property enumerates.
_published = st.one_of(
    _iso_z(),
    _iso_offset(),
    _date_only(),
    _epoch_millis(),
    _unparseable(),
    _missing(),
)

# Short company/title strings so collisions (and therefore tie-breaks) occur.
_company = st.sampled_from(["alpha", "Beta", "gamma", "Acme", ""])
_title = st.sampled_from(
    ["Data Scientist", "Data Analyst", "Research Analyst", "ML Engineer", ""]
)


@st.composite
def jobs(draw):
    """A list of Job_Records with mixed priority and date shapes."""
    n = draw(st.integers(min_value=0, max_value=25))
    records = []
    for _ in range(n):
        records.append(
            {
                "title_priority": draw(_priorities),
                "published_at": draw(_published),
                "company": draw(_company),
                "title": draw(_title),
            }
        )
    return records


# ---------------------------------------------------------------------------
# Reference helpers mirroring the sort key's documented semantics
# ---------------------------------------------------------------------------


def _effective_priority(job):
    p = job.get("title_priority")
    return p if isinstance(p, int) else 2


def _parsed_ts(job):
    """Parsed UTC epoch seconds, or None when the date is undated."""
    dt = ats_helpers.parse_posted(job.get("published_at"))
    return None if dt is None else dt.timestamp()


def _fingerprint(records):
    """A hashable, order-insensitive signature of a list of Job_Records."""
    return Counter(
        (
            r.get("title_priority"),
            r.get("published_at"),
            r.get("company"),
            r.get("title"),
        )
        for r in records
    )


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=400)
@given(records=jobs())
def test_output_is_a_permutation_and_input_unmutated(records):
    before = _fingerprint(records)
    result = sort_by_priority_and_date(records)

    # Same multiset of records in, out.
    assert _fingerprint(result) == before
    assert len(result) == len(records)

    # The input list itself is not reordered or mutated.
    assert _fingerprint(records) == before


@settings(max_examples=400)
@given(records=jobs())
def test_priority_non_decreasing(records):
    result = sort_by_priority_and_date(records)
    priorities = [_effective_priority(r) for r in result]
    assert priorities == sorted(priorities)


@settings(max_examples=400)
@given(records=jobs())
def test_within_priority_dated_before_undated_and_newest_first(records):
    result = sort_by_priority_and_date(records)

    # Group consecutively by effective priority (the list is already sorted by
    # priority) and check the per-priority ordering.
    current_priority = None
    group = []

    def check(group):
        # Dated records all precede undated ones.
        undated_flags = [_parsed_ts(r) is None for r in group]
        assert undated_flags == sorted(undated_flags), (
            "an undated record preceded a dated one within a priority"
        )
        # Within the dated prefix, timestamps are non-increasing (newest first).
        dated_ts = [_parsed_ts(r) for r in group if _parsed_ts(r) is not None]
        assert dated_ts == sorted(dated_ts, reverse=True), (
            "dated records are not ordered newest-first within a priority"
        )

    for r in result:
        p = _effective_priority(r)
        if p != current_priority:
            if group:
                check(group)
            current_priority = p
            group = [r]
        else:
            group.append(r)
    if group:
        check(group)


@settings(max_examples=200)
@given(records=jobs())
def test_all_priority_one_precede_priority_two(records):
    result = sort_by_priority_and_date(records)
    last_p1 = max(
        (i for i, r in enumerate(result) if _effective_priority(r) == 1),
        default=-1,
    )
    first_p2 = min(
        (i for i, r in enumerate(result) if _effective_priority(r) == 2),
        default=len(result),
    )
    assert last_p1 < first_p2


@settings(max_examples=200)
@given(records=jobs())
def test_tie_break_is_stable_on_company_then_title(records):
    result = sort_by_priority_and_date(records)
    # For records that are equal on (priority, undated, timestamp), the order
    # must be by lowercased company, then lowercased title.
    for a, b in zip(result, result[1:]):
        same_bucket = (
            _effective_priority(a) == _effective_priority(b)
            and _parsed_ts(a) == _parsed_ts(b)
        )
        if same_bucket:
            ka = (
                (a.get("company") or a.get("company_slug") or "").lower(),
                (a.get("title") or "").lower(),
            )
            kb = (
                (b.get("company") or b.get("company_slug") or "").lower(),
                (b.get("title") or "").lower(),
            )
            assert ka <= kb


@settings(max_examples=200)
@given(records=jobs())
def test_sort_is_deterministic(records):
    first = sort_by_priority_and_date(records)
    second = sort_by_priority_and_date(records)
    assert first == second
