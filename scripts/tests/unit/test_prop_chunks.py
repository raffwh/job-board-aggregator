"""Property-based test for the chunk save/load round trip.

Property 27: Chunk save/load round trip.

For any list of Job_Records carrying the new fields (``skills``, ``summary``,
``workplace_type``, ``location_status``, ``match_type``, ``match_reason``,
``title_priority``, ``distance_miles``) alongside identity fields and
arbitrary Unicode strings, writing them with ``save_chunks`` and reading them
back with ``load_chunks`` in a temporary directory returns the same multiset
of records with every field preserved.

``save_chunks`` sorts in place and shards by ``CHUNK_SIZE``, so the round trip
is checked order-insensitively and a monkeypatched small ``CHUNK_SIZE`` forces
the multi-chunk path (manifest with several chunk files) to be exercised too.

Validates: Requirements 13.5
"""

import copy
import itertools
import json

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import merge_data
from merge_data import load_chunks, save_chunks

# A fresh sub-directory per generated example. ``tmp_path`` is function-scoped
# so Hypothesis reuses it across the examples of one test; each example writes
# to its own numbered sub-directory so saves never collide, which makes
# suppressing the function-scoped-fixture health check safe.
_counter = itertools.count()


def _fresh_dir(tmp_path):
    return tmp_path / f"case_{next(_counter)}"


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# Arbitrary Unicode text, including the empty string and awkward characters.
_text = st.text(
    alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x2FFF),
    max_size=24,
)

# A single skill string; a skills list is drawn from these.
_skills = st.lists(_text, max_size=5)

_workplace_type = st.sampled_from(["remote", "hybrid", "onsite", "unknown"])
_location_status = st.sampled_from(
    ["boston_area", "new_england", "remote", "other", "unknown", None]
)
_match_type = st.sampled_from(["match", "maybe", "no", None])
_match_reason = st.sampled_from(
    [
        "location_unknown",
        "location_new_england",
        "remote_unspecified",
        "tier_senior_signal",
        "title_maybe",
        "title_generic_analyst",
        None,
    ]
)
_title_priority = st.sampled_from([1, 2, None])
# distance may be absent, null, an int, or a float.
_distance = st.one_of(
    st.none(),
    st.integers(min_value=0, max_value=5000),
    st.floats(min_value=0, max_value=5000, allow_nan=False, allow_infinity=False),
)


@st.composite
def _job(draw):
    """A single Job_Record carrying identity and enrichment fields."""
    record = {
        # Identity / sort fields. save_chunks sorts on company + title, so
        # include them (and allow None to exercise the None-safe sort key).
        "company": draw(st.one_of(st.none(), _text)),
        "title": draw(st.one_of(st.none(), _text)),
        "url": draw(_text),
        "ats": draw(st.sampled_from(["Greenhouse", "Lever", "Workday", "Paylocity"])),
        "scraped_at": draw(st.sampled_from(["2024-01-02T03:04:05Z", "", None])),
        "first_seen": draw(st.sampled_from(["2023-06-01T00:00:00Z", None])),
        # Enrichment / display fields the round trip must preserve.
        "skills": draw(_skills),
        "summary": draw(_text),
        "workplace_type": draw(_workplace_type),
        "location_status": draw(_location_status),
        "match_type": draw(_match_type),
        "match_reason": draw(_match_reason),
        "title_priority": draw(_title_priority),
        "distance_miles": draw(_distance),
        "distance": draw(_distance),
    }
    return record


_jobs = st.lists(_job(), max_size=12)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fingerprint(records):
    """An order-insensitive signature of a list of Job_Records.

    Each record is serialized with sorted keys so two records with the same
    fields compare equal regardless of key order, and the resulting strings are
    sorted so the comparison ignores record order (a multiset comparison).
    """
    return sorted(
        json.dumps(r, sort_keys=True, ensure_ascii=False) for r in records
    )


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(records=_jobs)
def test_round_trip_preserves_all_fields(records, tmp_path):
    """load_chunks(save_chunks(records)) is the same multiset of records."""
    directory = _fresh_dir(tmp_path)
    expected = _fingerprint(records)
    # save_chunks mutates (sorts) its argument in place; give it a copy so the
    # original fingerprint stays valid for comparison.
    save_chunks(copy.deepcopy(records), str(directory), "2024-01-01T00:00:00Z")

    loaded = load_chunks(str(directory))

    assert _fingerprint(loaded) == expected


@settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(records=st.lists(_job(), min_size=7, max_size=20))
def test_round_trip_across_multiple_chunks(records, tmp_path, monkeypatch):
    """A small CHUNK_SIZE forces several chunk files, still round-tripping."""
    monkeypatch.setattr(merge_data, "CHUNK_SIZE", 3)
    directory = _fresh_dir(tmp_path)

    expected = _fingerprint(records)
    save_chunks(copy.deepcopy(records), str(directory), "2024-01-01T00:00:00Z")

    # The manifest should name more than one chunk file for this many records.
    manifest_path = directory / "chunks" / "jobs_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest["chunks"]) >= 2
    assert manifest["totalJobs"] == len(records)

    loaded = load_chunks(str(directory))
    assert _fingerprint(loaded) == expected


@settings(
    max_examples=25,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(records=_jobs)
def test_empty_and_nonempty_round_trip_is_stable(records, tmp_path):
    """Loading twice from the same saved directory yields the same result."""
    directory = _fresh_dir(tmp_path)
    save_chunks(copy.deepcopy(records), str(directory), "2024-01-01T00:00:00Z")
    first = _fingerprint(load_chunks(str(directory)))
    second = _fingerprint(load_chunks(str(directory)))
    assert first == second


def test_missing_directory_loads_empty(tmp_path):
    """A directory with no chunks manifest loads as an empty list."""
    assert load_chunks(str(tmp_path / "nonexistent")) == []
