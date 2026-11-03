import json
import gzip
import os
from pathlib import Path
from datetime import datetime, timezone

try:
    # When imported as a package module (scripts.merge_data) or with scripts/
    # on sys.path, prefer the shared pure helpers.
    from ats_helpers import parse_workday_slug
except ImportError:  # pragma: no cover - fallback for package-style import
    try:
        from scripts.ats_helpers import parse_workday_slug
    except ImportError:
        parse_workday_slug = None

CHUNK_SIZE = 25_000

# Age past which a record is considered stale and dropped during a merge.
STALE_AFTER_DAYS = 30

# Enrichment fields carried forward from an existing record onto a new one when
# the new record lacks them but the matched existing record has them
# (Requirement 13.4). Order is irrelevant; membership is what matters.
ENRICHMENT_FIELDS = ("skills", "summary", "jd_backend")


def _workday_tenant(job):
    """Return the Workday tenant for a Job_Record.

    The tenant is parsed from the record's ``company_slug`` (the Workday_Slug)
    with the shared :func:`ats_helpers.parse_workday_slug` so it matches the
    slug parsing used everywhere else. Falls back to the ``company`` field (the
    tenant segment stored by the fetcher) and finally to an empty string.
    """
    slug = job.get("company_slug")
    if parse_workday_slug is not None and isinstance(slug, str):
        parsed = parse_workday_slug(slug)
        if parsed is not None:
            return parsed[0]
    company = job.get("company")
    return (company or "").lower()


def get_dedup_key(job):
    """Return the stable Dedup_Key for a Job_Record.

    - Workday with a requisition ID -> ``workday:<tenant>:<req_id>``
      (Requirement 13.1). The tenant comes from the Workday_Slug via
      ``ats_helpers``.
    - Workday without a requisition ID -> the URL (previous behavior).
    - Paylocity -> ``paylocity:<id>`` or ``paylocity:<company>:<title>``.
    - Everything else -> the URL.

    The key depends only on identity fields, so changing ``scraped_at``,
    ``skills``, ``summary``, or match fields never changes it (Requirement
    13.3).
    """
    url = job.get("url", "") or ""
    ats = job.get("ats")

    if ats == "Workday":
        req_id = job.get("req_id")
        if req_id:
            tenant = _workday_tenant(job)
            return f"workday:{tenant}:{req_id}"
        return url

    if ats == "Paylocity":
        jid = job.get("id")
        if jid:
            return f"paylocity:{jid}"
        # no job_id: key on company+title so they don't collapse to one listing URL
        return f"paylocity:{job.get('company', '')}:{job.get('title', '')}"

    return url


def legacy_key(job):
    """Return the previous-format key (the URL) for a Job_Record.

    Used to recognize a posting whose new Dedup_Key has no match but whose URL
    matches an existing record (Requirement 13.2). Returns ``None`` when the
    record has no URL.
    """
    url = job.get("url")
    return url or None


def _parse_scraped_at(value):
    """Parse a ``scraped_at`` value into a timezone-aware UTC datetime.

    Returns ``None`` when the value is missing or unparseable; callers treat an
    unparseable date as "not stale" so a bad timestamp never silently drops a
    job.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_stale(job, now):
    """Return ``True`` when a record's ``scraped_at`` is more than 30 days old.

    A missing or unparseable ``scraped_at`` is treated as not stale.
    """
    scraped = _parse_scraped_at(job.get("scraped_at"))
    if scraped is None:
        return False
    return (now - scraped).days > STALE_AFTER_DAYS


def _carry_enrichment(new_job, existing_job):
    """Copy ENRICHMENT_FIELDS from ``existing_job`` onto ``new_job`` in place.

    A field is carried only when the new record lacks a usable value (missing,
    None, or empty) and the existing record has one (Requirement 13.4).
    """
    for field in ENRICHMENT_FIELDS:
        new_value = new_job.get(field)
        if new_value:
            continue
        existing_value = existing_job.get(field)
        if existing_value:
            new_job[field] = existing_value


def merge_records(existing, new, now):
    """Merge a new scrape with existing records (pure function).

    Steps (design section 8):

    1. Index fresh existing records (dropping records older than 30 days) by
       their Dedup_Key and by their legacy URL key.
    2. For each new record, look up the new key, then the legacy key
       (Requirement 13.2). On a match, preserve ``first_seen`` and carry
       ENRICHMENT_FIELDS forward when the new record lacks them (Requirement
       13.4).
    3. Store every surviving record under its new Dedup_Key only, so legacy
       entries collapse into a single posting (at most one record per
       Dedup_Key, Requirement 13.6).

    ``now`` is a timezone-aware UTC datetime used for staleness. The input
    lists are not mutated except that new-record dicts receive ``first_seen``
    and any carried enrichment fields.
    """
    merged = {}
    by_legacy = {}

    for job in existing:
        key = get_dedup_key(job)
        if not key:
            continue
        if _is_stale(job, now):
            continue
        merged[key] = job
        lk = legacy_key(job)
        if lk:
            by_legacy[lk] = job

    for job in new:
        key = get_dedup_key(job)
        if not key:
            continue

        match = merged.get(key)
        if match is None:
            # New key missed; try the legacy (URL) key (Requirement 13.2).
            lk = legacy_key(job)
            if lk:
                match = by_legacy.get(lk)

        if match is not None:
            job["first_seen"] = match.get("first_seen") or match.get("scraped_at")
            _carry_enrichment(job, match)
            # Remove the legacy-keyed entry so it collapses into the new key
            # rather than surviving as a duplicate (Requirement 13.6).
            old_key = get_dedup_key(match)
            if old_key and old_key != key:
                merged.pop(old_key, None)
        else:
            job["first_seen"] = job.get("first_seen") or job.get("scraped_at")

        merged[key] = job

    return list(merged.values())


def load_chunks(directory):
    """Load all jobs from chunked gzip files via manifest."""
    chunks_dir = Path(directory) / "chunks"
    manifest_path = chunks_dir / "jobs_manifest.json"
    if not manifest_path.exists():
        return []
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    jobs = []
    for chunk_file in manifest["chunks"]:
        chunk_path = chunks_dir / chunk_file
        if chunk_path.exists():
            with gzip.open(chunk_path, "rt", encoding="utf-8") as f:
                jobs.extend(json.load(f))
    return jobs


def save_chunks(jobs, directory, timestamp):
    """Write chunked gzip files + manifest.

    Chunks store whole Job_Record dicts, so every field (skills, summary,
    workplace_type, location_status, match_type, match_reason, title_priority,
    distance, and the rest) rides along and survives a save/load round trip
    (Requirements 13.5, 13.7).
    """
    chunks_dir = Path(directory) / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    # Clean old chunks
    for f in os.listdir(chunks_dir):
        if f.startswith("jobs_chunk_") and f.endswith(".json.gz"):
            os.remove(chunks_dir / f)

    # Sort consistently. None-safe: a missing or None company/title sorts as "".
    jobs.sort(key=lambda x: ((x.get('company') or '').lower(),
                             (x.get('title') or '').lower()))
    chunks = [jobs[i:i + CHUNK_SIZE] for i in range(0, len(jobs), CHUNK_SIZE)]

    chunk_filenames = []
    for idx, chunk in enumerate(chunks):
        chunk_file = f"jobs_chunk_{idx}.json.gz"
        with gzip.open(chunks_dir / chunk_file, "wt", encoding="utf-8") as f:
            json.dump(chunk, f, ensure_ascii=False, indent=0)
        chunk_filenames.append(chunk_file)
        print(f"  Chunk {idx}: {len(chunk):,} jobs")

    manifest = {
        "chunks": chunk_filenames,
        "totalJobs": len(jobs),
        "last_updated": timestamp,
    }
    with open(chunks_dir / "jobs_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def merge_job_data():
    """Merge new scrape with existing data, removing stale jobs."""
    new_jobs = load_chunks("scripts/output")
    print(f"New scrape: {len(new_jobs):,} jobs")

    existing_jobs = load_chunks("data")
    print(f"Existing data: {len(existing_jobs):,} jobs")

    now = datetime.now(timezone.utc)
    final_jobs = merge_records(existing_jobs, new_jobs, now)
    print(f"Merged result: {len(final_jobs):,} jobs")

    timestamp = now.isoformat().replace("+00:00", "Z")
    save_chunks(final_jobs, "data", timestamp)

    # Update metadata
    with open("scripts/output/metadata.json", encoding="utf-8") as f:
        metadata = json.load(f)
    metadata["total_jobs"] = len(final_jobs)
    with open("data/metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    print("Merge complete")
    return len(final_jobs)


if __name__ == "__main__":
    merge_job_data()
