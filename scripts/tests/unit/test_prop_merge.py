"""Property-based test for merge identity, first_seen, and enrichment.

Property 26: Merge preserves identity, first_seen, and enrichment.

For any list of existing records (fresh and stale) and new records, where some
new records match existing ones by the new Dedup_Key, some match only by the
legacy URL key, and some are brand new, ``merge_records(existing, new, now)``
SHALL:

- produce at most one record per Dedup_Key (Requirement 13.6);
- give every matched record the existing record's ``first_seen`` -- whether the
  match is by the new key or only by the legacy URL key (Requirement 13.2);
- carry the existing ``skills``, ``summary``, and ``jd_backend`` forward onto a
  matched record whose new version lacks them (Requirement 13.4);
- keep the new enrichment when the new version already has it (Requirement
  13.4);
- drop records whose ``scraped_at`` is more than 30 days old.

Validates: Requirements 13.2, 13.4, 13.6
"""

from datetime import datetime, timedelta, timezone

from hypothesis import given, settings
from hypothesis import strategies as st

from merge_data import (
    ENRICHMENT_FIELDS,
    STALE_AFTER_DAYS,
    get_dedup_key,
    legacy_key,
    merge_records,
)

# A fixed "now" so staleness is deterministic across examples.
NOW = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

# Timestamps safely inside the freshness window (not stale).
FRESH_AT = (NOW - timedelta(days=5)).isoformat().replace("+00:00", "Z")
# Timestamps safely past the 30-day window (stale and dropped).
STALE_AT = (NOW - timedelta(days=STALE_AFTER_DAYS + 10)).isoformat().replace(
    "+00:00", "Z"
)

# Enrichment values an existing record may carry forward. Each is truthy so the
# merge logic treats it as present.
_enrichment_values = {
    "skills": st.lists(
        st.sampled_from(["python", "sql", "r", "pytorch", "spark"]),
        min_size=1,
        max_size=3,
    ),
    "summary": st.text(min_size=1, max_size=40),
    "jd_backend": st.sampled_from(["keyword", "llm", "regex"]),
}


@st.composite
def enrichment(draw, *, require_all=False):
    """A dict of a subset of ENRICHMENT_FIELDS mapped to truthy values.

    With ``require_all`` every field is present; otherwise an arbitrary subset
    (possibly empty) is present.
    """
    present = {}
    for field in ENRICHMENT_FIELDS:
        include = True if require_all else draw(st.booleans())
        if include:
            present[field] = draw(_enrichment_values[field])
    return present


# Distinct identity pools so independent records never collide by accident.
_req_ids = st.integers(min_value=1, max_value=50).map(lambda n: f"REQ{n}")
_url_ids = st.integers(min_value=1, max_value=50).map(
    lambda n: f"https://jobs.example.com/{n}"
)


@st.composite
def new_key_match_pair(draw):
    """An existing + new record that share the SAME new Dedup_Key.

    Both are plain (URL-keyed) postings with the same URL, so the new key hits
    directly. The existing record carries all enrichment and a known
    first_seen; the new record's enrichment is an arbitrary subset so we can
    assert both carry-forward and keep-new behavior.
    """
    url = draw(_url_ids)
    first_seen = (NOW - timedelta(days=draw(st.integers(20, 60)))).isoformat().replace(
        "+00:00", "Z"
    )
    existing = {
        "url": url,
        "first_seen": first_seen,
        "scraped_at": FRESH_AT,
        **draw(enrichment(require_all=True)),
    }
    new = {"url": url, "scraped_at": FRESH_AT, **draw(enrichment())}
    return existing, new, "new_key"


@st.composite
def legacy_key_match_pair(draw):
    """An existing + new record that match ONLY by the legacy URL key.

    The existing record is URL-keyed (an old-format Workday posting with no
    req_id, so its Dedup_Key is the URL). The new record is the same posting
    re-scraped with a req_id, so its new Dedup_Key is ``workday:<tenant>:<req>``
    -- different from the existing key -- but the URL still matches
    (Requirement 13.2).
    """
    url = draw(_url_ids)
    tenant = draw(st.sampled_from(["acme", "globex", "initech"]))
    req_id = draw(_req_ids)
    first_seen = (NOW - timedelta(days=draw(st.integers(20, 60)))).isoformat().replace(
        "+00:00", "Z"
    )
    existing = {
        "ats": "Workday",
        "company": tenant,
        "url": url,
        "first_seen": first_seen,
        "scraped_at": FRESH_AT,
        **draw(enrichment(require_all=True)),
    }
    new = {
        "ats": "Workday",
        "company": tenant,
        "company_slug": f"{tenant}|wd1|site",
        "req_id": req_id,
        "url": url,
        "scraped_at": FRESH_AT,
        **draw(enrichment()),
    }
    return existing, new, "legacy_key"


@st.composite
def brand_new_record(draw):
    """A new record with no existing counterpart."""
    url = draw(_url_ids)
    new = {
        "url": f"{url}/brand-new",
        "scraped_at": FRESH_AT,
        **draw(enrichment()),
    }
    return new


@st.composite
def stale_existing_record(draw):
    """An existing record older than 30 days that the merge must drop."""
    url = draw(_url_ids)
    return {
        "url": f"{url}/stale",
        "first_seen": STALE_AT,
        "scraped_at": STALE_AT,
        **draw(enrichment(require_all=True)),
    }


@st.composite
def scenario(draw):
    """Assemble existing/new lists with unique identities across groups.

    Each match pair, brand-new record, and stale record is namespaced by index
    so their URLs and keys never collide, which lets the assertions reason about
    each group independently.
    """
    pairs = draw(st.lists(st.one_of(new_key_match_pair(), legacy_key_match_pair()),
                          min_size=0, max_size=4))
    brand_new = draw(st.lists(brand_new_record(), min_size=0, max_size=3))
    stale = draw(st.lists(stale_existing_record(), min_size=0, max_size=3))

    existing = []
    new = []
    expected_first_seen = {}  # new Dedup_Key -> expected first_seen
    carried = {}  # new Dedup_Key -> (existing_enrichment, new_had_value flags)

    for i, (ex, nw, kind) in enumerate(pairs):
        # Namespace every identity field by the pair index so each pair is
        # globally unique. For legacy-key pairs the new Dedup_Key is
        # ``workday:<tenant>:<req_id>``, so the tenant (company / company_slug)
        # and req_id -- not just the URL -- must be namespaced or two pairs
        # could collapse onto the same key and make the test ambiguous.
        tag = f"pair{i}"
        ex = dict(ex)
        nw = dict(nw)
        ex["url"] = f"{ex['url']}/{tag}"
        nw["url"] = f"{nw['url']}/{tag}"
        if kind == "legacy_key":
            tenant = f"{nw['company']}{i}"
            ex["company"] = tenant
            nw["company"] = tenant
            nw["company_slug"] = f"{tenant}|wd1|site"
            nw["req_id"] = f"{nw['req_id']}-{i}"
        existing.append(ex)
        new.append(nw)

        new_key = get_dedup_key(nw)
        expected_first_seen[new_key] = ex["first_seen"]
        carried[new_key] = (ex, nw)

    for i, nw in enumerate(brand_new):
        nw = dict(nw)
        nw["url"] = f"{nw['url']}/bn{i}"
        new.append(nw)

    for i, ex in enumerate(stale):
        ex = dict(ex)
        ex["url"] = f"{ex['url']}/st{i}"
        existing.append(ex)

    return existing, new, expected_first_seen, carried


@settings(max_examples=300)
@given(data=scenario())
def test_merge_preserves_identity_and_enrichment(data):
    existing, new, expected_first_seen, carried = data

    # Snapshot inputs that must not be mutated incorrectly. merge_records is
    # allowed to set first_seen / enrichment on the NEW dicts only.
    stale_keys_before = {
        get_dedup_key(e) for e in existing
        if (NOW - datetime.fromisoformat(
            e["scraped_at"].replace("Z", "+00:00"))).days > STALE_AFTER_DAYS
    }

    result = merge_records(existing, new, NOW)

    # --- At most one record per Dedup_Key (Requirement 13.6) ---
    keys = [get_dedup_key(r) for r in result]
    assert len(keys) == len(set(keys)), f"duplicate Dedup_Keys in result: {keys}"

    by_key = {get_dedup_key(r): r for r in result}

    # --- Stale records are dropped ---
    for stale_key in stale_keys_before:
        # A stale key only survives if a fresh new record happens to reuse it;
        # our namespacing prevents that, so it must be gone.
        assert stale_key not in by_key, (
            f"stale record {stale_key} survived the merge"
        )

    # --- Matched records: first_seen preserved + enrichment carried ---
    for new_key, expected in expected_first_seen.items():
        assert new_key in by_key, f"matched record {new_key} missing from result"
        record = by_key[new_key]

        # first_seen comes from the existing record, by new key OR legacy URL
        # key (Requirement 13.2).
        assert record.get("first_seen") == expected, (
            f"first_seen not preserved for {new_key}: "
            f"{record.get('first_seen')!r} != {expected!r}"
        )

        existing_rec, new_rec = carried[new_key]
        for field in ENRICHMENT_FIELDS:
            if new_rec.get(field):
                # New record had its own enrichment: it must be kept.
                assert record.get(field) == new_rec[field], (
                    f"new enrichment {field} not kept for {new_key}"
                )
            else:
                # New record lacked it: existing value is carried forward
                # (Requirement 13.4).
                assert record.get(field) == existing_rec[field], (
                    f"existing enrichment {field} not carried for {new_key}"
                )

    # Sanity: every matched new record also matches by at least the legacy key
    # or new key path, so the merge really exercised Requirement 13.2.
    for new_key in expected_first_seen:
        record = by_key[new_key]
        assert legacy_key(record) is not None or new_key.startswith("workday:")
