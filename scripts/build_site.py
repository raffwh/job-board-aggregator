"""Build the Personal_Site from the scraper output.

This reads ``scripts/output/all_jobs.json`` and ``metadata.json``, writes a
slim ``site/jobs.json`` with the Filter_Chain / enrichment fields the frontend
needs (Requirements 13.7, 14.1, 14.17), and copies the static template files.

First_Seen carry-forward (Requirements 14.15, 14.16): the Personal_Workflow has
no merge history, so this builder carries ``first_seen`` forward from the
previously deployed ``jobs.json``. The workflow downloads that file (a read-only
public GET) into ``--previous`` before this runs; a missing, empty, or corrupt
file simply counts as "no previous data" and every job starts its own
``first_seen``. :func:`carry_first_seen` is pure and matches by ``id`` first,
falling back to ``url`` for previous records that predate the ``id`` field.
"""

import argparse
import json
import os
import shutil
from datetime import datetime, timezone

# merge_data provides get_dedup_key for the stable job identifier (`id`).
# Support both script-style (scripts/ on sys.path) and package-style imports.
try:
    from merge_data import get_dedup_key
except ImportError:  # pragma: no cover - fallback for package-style import
    from scripts.merge_data import get_dedup_key

try:
    import personal_config
except ImportError:  # pragma: no cover - fallback for package-style import
    from scripts import personal_config


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(ROOT_DIR, "scripts", "output")
SITE_DIR = os.path.join(ROOT_DIR, "site")
TEMPLATE_DIR = os.path.join(ROOT_DIR, "site_template")

DEFAULT_PREVIOUS_PATH = os.path.join(OUTPUT_DIR, "previous_site_jobs.json")

# Fields copied from each all_jobs record into the slim site record. The full
# `description` is intentionally excluded so the published payload stays small
# (Requirement 13.7); it lives only in all_jobs.json. `remote` stays for
# backward compatibility with older frontend code (workplace_type == "remote").
SLIM_FIELDS = (
    "title",
    "company",
    "location",
    "ats",
    "skill_level",
    "remote",
    # Filter_Chain / enrichment fields on the site (Requirements 14.1, 14.17).
    "skills",
    "summary",
    "workplace_type",
    "location_status",
    "match_type",
    "match_reason",
    "title_priority",
    "category",
    "distance_miles",
    "published_at",
    "updated_at",
    "first_seen",
)


def _now_iso():
    """Return the current UTC time as an ISO 8601 string with a trailing Z."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def slim_record(job):
    """Build one slim site record from a full Job_Record.

    Copies the SLIM_FIELDS that are present, resolves the display `url`, and
    adds the stable `id` (Dedup_Key, Requirement 14.1).
    """
    record = {k: job.get(k) for k in SLIM_FIELDS if k in job}
    record["url"] = job.get("url") or job.get("absolute_url")
    # Published date fallback mirrors the previous builder behavior.
    if not record.get("published_at"):
        record["published_at"] = job.get("created_at") or ""
    if "match_type" not in record:
        record["match_type"] = "primary"
    record["id"] = get_dedup_key(job)
    return record


def carry_first_seen(new, previous, now):
    """Carry First_Seen forward from the previously deployed site records.

    Pure function (Property 29). For each record in ``new``:

    - If a ``previous`` record has the same ``id``, or the same ``url`` when the
      previous record has no ``id``, set ``first_seen`` to that previous
      record's ``first_seen`` (Requirement 14.15).
    - Otherwise keep the record's own ``first_seen`` when present, else ``now``
      (Requirement 14.16).

    ``previous`` of ``None`` (missing or corrupt file) means no previous data,
    so every record falls through to its own ``first_seen`` or ``now``. The
    input dicts are mutated in place and also returned.
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

    for job in new:
        match = None
        jid = job.get("id")
        if jid:
            match = by_id.get(jid)
        if match is None:
            url = job.get("url")
            if url:
                match = by_url.get(url)

        if match is not None and match.get("first_seen"):
            job["first_seen"] = match.get("first_seen")
        else:
            job["first_seen"] = job.get("first_seen") or now

    return new


def load_previous(path):
    """Load the previous site ``jobs.json``.

    Returns the parsed list, or ``None`` when the file is missing, empty, not
    JSON, or not a list. A corrupt file counts as no previous data
    (Requirement 14.16).
    """
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    return data


def build_site(previous_path=DEFAULT_PREVIOUS_PATH):
    """Build the Personal_Site into SITE_DIR and return the published count."""
    os.makedirs(SITE_DIR, exist_ok=True)

    jobs_path = os.path.join(OUTPUT_DIR, "all_jobs.json")
    metadata_path = os.path.join(OUTPUT_DIR, "metadata.json")

    if not os.path.exists(jobs_path):
        raise FileNotFoundError(f"Scraper output is missing: {jobs_path}")
    if not os.path.exists(metadata_path):
        raise FileNotFoundError(f"Scraper metadata is missing: {metadata_path}")

    with open(jobs_path, encoding="utf-8") as file:
        jobs = json.load(file)

    with open(metadata_path, encoding="utf-8") as file:
        metadata = json.load(file)

    now = _now_iso()

    slim_jobs = [slim_record(job) for job in jobs]

    previous = load_previous(previous_path)
    carry_first_seen(slim_jobs, previous, now)

    with open(os.path.join(SITE_DIR, "jobs.json"), "w", encoding="utf-8") as file:
        json.dump(slim_jobs, file, ensure_ascii=False, separators=(",", ":"))

    site_metadata = {
        "total_jobs": len(slim_jobs),
        "last_updated": metadata.get("last_updated", now),
        # Radius / center so the frontend can apply the "near Boston" rule
        # without a hardcoded list (Requirements 14.1, 14.5).
        "boston_radius_miles": personal_config.BOSTON_RADIUS_MILES,
        "boston_center_lat": personal_config.BOSTON_CENTER_LAT,
        "boston_center_lng": personal_config.BOSTON_CENTER_LNG,
    }

    with open(os.path.join(SITE_DIR, "metadata.json"), "w", encoding="utf-8") as file:
        json.dump(site_metadata, file, ensure_ascii=False, indent=2)

    for filename in ("index.html", "styles.css", "app.js"):
        shutil.copyfile(
            os.path.join(TEMPLATE_DIR, filename),
            os.path.join(SITE_DIR, filename),
        )

    size_bytes = sum(
        os.path.getsize(os.path.join(root, name))
        for root, _, files in os.walk(SITE_DIR)
        for name in files
    )

    print(f"Jobs published: {len(slim_jobs):,}")
    print(f"Published site size: {size_bytes / 1024 / 1024:.2f} MB")
    return len(slim_jobs)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the Personal_Site.")
    parser.add_argument(
        "--previous",
        default=DEFAULT_PREVIOUS_PATH,
        help=(
            "Path to the previously deployed site jobs.json, used to carry "
            "first_seen forward. Missing or corrupt counts as no previous data."
        ),
    )
    args = parser.parse_args(argv)
    build_site(previous_path=args.previous)


if __name__ == "__main__":
    main()
