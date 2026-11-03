"""Unit tests for the Site_Builder slim records and radius metadata.

``build_site`` reads ``all_jobs.json`` and ``metadata.json`` from its
``OUTPUT_DIR``, writes a slim ``site/jobs.json`` with the Filter_Chain and
enrichment fields the frontend needs, and copies the static template files.

These tests run the builder against a ``tmp_path`` output fixture with
``build_site.OUTPUT_DIR``, ``SITE_DIR``, and ``TEMPLATE_DIR`` redirected into
the temp tree (stub ``index.html`` / ``app.js`` / ``styles.css`` let the copy
step succeed). They then assert:

- each slim record contains the enrichment and identity fields -- ``skills``,
  ``summary``, ``workplace_type``, ``location_status``, ``match_type``,
  ``match_reason``, ``category``, ``title_priority``, ``distance_miles``,
  ``id``, ``url``, and ``first_seen`` (Requirements 13.7, 14.1) -- and does NOT
  carry the full ``description`` into the published payload (Requirement 13.7);
- ``site/metadata.json`` carries ``boston_radius_miles`` plus the Boston_Center
  latitude and longitude (Requirement 14.1);
- the build finishes when the previous file is missing or corrupt
  (Requirements 14.16, 14.17).

Validates: Requirements 13.7, 14.1, 14.16, 14.17
"""

import json

import pytest

import build_site
import personal_config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def site_env(tmp_path, monkeypatch):
    """Redirect build_site's directories into tmp_path and seed input files.

    Returns the tuple ``(output_dir, site_dir, jobs)`` where ``jobs`` is the
    full Job_Record list written to ``all_jobs.json``.
    """
    output_dir = tmp_path / "output"
    site_dir = tmp_path / "site"
    template_dir = tmp_path / "template"
    output_dir.mkdir()
    template_dir.mkdir()

    # Stub template files so the copy step at the end of build_site() works.
    for name in ("index.html", "app.js", "styles.css"):
        (template_dir / name).write_text(f"stub {name}", encoding="utf-8")

    monkeypatch.setattr(build_site, "OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(build_site, "SITE_DIR", str(site_dir))
    monkeypatch.setattr(build_site, "TEMPLATE_DIR", str(template_dir))

    jobs = [
        {
            "title": "Senior Data Scientist",
            "company": "Acme Bio",
            "location": "Cambridge, MA",
            "url": "https://boards.greenhouse.io/acme/jobs/123",
            "ats": "Greenhouse",
            "skills": ["Python", "SQL"],
            "summary": "Build models for clinical trials.",
            "workplace_type": "hybrid",
            "location_status": "boston_area",
            "match_type": "primary",
            "match_reason": "primary",
            "category": "data_scientist",
            "title_priority": 1,
            "distance_miles": 3.2,
            "published_at": "2024-01-02T00:00:00Z",
            "first_seen": "2023-12-01T00:00:00Z",
            # The full description must NOT survive into the slim record.
            "description": "A very long job description " * 50,
        },
        {
            "title": "HEOR Analyst",
            "company": "Beta Health",
            "location": "Remote - US",
            "url": "https://jobs.lever.co/beta/456",
            "ats": "Lever",
            "skills": [],
            "summary": "",
            "workplace_type": "remote",
            "location_status": "remote",
            "match_type": "maybe",
            "match_reason": "remote_unspecified",
            "category": "other_target",
            "title_priority": 2,
            "distance_miles": None,
            "published_at": "2024-02-03T00:00:00Z",
            "description": "Another description.",
        },
    ]

    _write_json(output_dir / "all_jobs.json", jobs)
    _write_json(
        output_dir / "metadata.json",
        {"last_updated": "2024-03-01T00:00:00Z", "total_jobs": len(jobs)},
    )

    return output_dir, site_dir, jobs


def _load_site_jobs(site_dir):
    return json.loads((site_dir / "jobs.json").read_text(encoding="utf-8"))


def _load_site_metadata(site_dir):
    return json.loads((site_dir / "metadata.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Slim-record field tests (Requirements 13.7, 14.1)
# ---------------------------------------------------------------------------

# The Filter_Chain / enrichment / identity fields every slim record must carry.
REQUIRED_SLIM_FIELDS = (
    "skills",
    "summary",
    "workplace_type",
    "location_status",
    "match_type",
    "match_reason",
    "category",
    "title_priority",
    "distance_miles",
    "id",
    "url",
    "first_seen",
)


def test_slim_records_contain_required_fields(site_env):
    """Each slim record carries the enrichment and identity fields (13.7, 14.1)."""
    _output_dir, site_dir, _jobs = site_env

    # No previous file passed -> every record starts its own first_seen.
    build_site.build_site(previous_path=None)

    records = _load_site_jobs(site_dir)
    assert len(records) == 2

    for record in records:
        for field in REQUIRED_SLIM_FIELDS:
            assert field in record, f"slim record missing {field!r}: {record}"


def test_slim_records_omit_description(site_env):
    """The full description never rides into the published payload (13.7)."""
    _output_dir, site_dir, _jobs = site_env

    build_site.build_site(previous_path=None)

    for record in _load_site_jobs(site_dir):
        assert "description" not in record


def test_slim_record_values_match_source(site_env):
    """Field values and the id (Dedup_Key) carry through from all_jobs (14.1)."""
    _output_dir, site_dir, jobs = site_env

    build_site.build_site(previous_path=None)

    records = _load_site_jobs(site_dir)
    by_url = {r["url"]: r for r in records}

    ds = by_url["https://boards.greenhouse.io/acme/jobs/123"]
    assert ds["skills"] == ["Python", "SQL"]
    assert ds["summary"] == "Build models for clinical trials."
    assert ds["workplace_type"] == "hybrid"
    assert ds["location_status"] == "boston_area"
    assert ds["match_type"] == "primary"
    assert ds["category"] == "data_scientist"
    assert ds["title_priority"] == 1
    assert ds["distance_miles"] == 3.2
    # For a non-Workday / non-Paylocity record the Dedup_Key is the URL.
    assert ds["id"] == "https://boards.greenhouse.io/acme/jobs/123"
    # first_seen is preserved from the source record when no previous match.
    assert ds["first_seen"] == "2023-12-01T00:00:00Z"


def test_first_seen_defaults_to_build_time(site_env):
    """A record with no first_seen gets one at build time (14.16)."""
    _output_dir, site_dir, _jobs = site_env

    build_site.build_site(previous_path=None)

    records = _load_site_jobs(site_dir)
    heor = next(r for r in records if r["url"] == "https://jobs.lever.co/beta/456")
    assert heor["first_seen"]  # non-empty timestamp assigned


# ---------------------------------------------------------------------------
# Metadata tests (Requirement 14.1)
# ---------------------------------------------------------------------------


def test_metadata_contains_boston_radius_and_center(site_env):
    """site/metadata.json carries the radius and Boston_Center coords (14.1)."""
    _output_dir, site_dir, _jobs = site_env

    build_site.build_site(previous_path=None)

    metadata = _load_site_metadata(site_dir)
    assert metadata["boston_radius_miles"] == personal_config.BOSTON_RADIUS_MILES
    assert metadata["boston_center_lat"] == personal_config.BOSTON_CENTER_LAT
    assert metadata["boston_center_lng"] == personal_config.BOSTON_CENTER_LNG
    assert metadata["total_jobs"] == 2


# ---------------------------------------------------------------------------
# Previous-file resilience (Requirements 14.16, 14.17)
# ---------------------------------------------------------------------------


def test_build_finishes_with_missing_previous_file(site_env, tmp_path):
    """A missing previous file counts as no previous data (14.16)."""
    _output_dir, site_dir, _jobs = site_env

    missing = tmp_path / "does_not_exist.json"
    count = build_site.build_site(previous_path=str(missing))

    assert count == 2
    assert (site_dir / "jobs.json").exists()


def test_build_finishes_with_corrupt_previous_file(site_env, tmp_path):
    """A corrupt previous file counts as no previous data (14.16, 14.17)."""
    _output_dir, site_dir, _jobs = site_env

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{ this is not valid json", encoding="utf-8")

    count = build_site.build_site(previous_path=str(corrupt))

    assert count == 2
    records = _load_site_jobs(site_dir)
    # Every record still has a first_seen despite the unusable previous file.
    for record in records:
        assert record.get("first_seen")


def test_build_carries_first_seen_from_previous(site_env, tmp_path):
    """A matching previous record's first_seen is carried forward (14.15)."""
    _output_dir, site_dir, _jobs = site_env

    previous = tmp_path / "previous.json"
    _write_json(
        previous,
        [
            {
                "id": "https://jobs.lever.co/beta/456",
                "url": "https://jobs.lever.co/beta/456",
                "first_seen": "2022-01-01T00:00:00Z",
            }
        ],
    )

    build_site.build_site(previous_path=str(previous))

    records = _load_site_jobs(site_dir)
    heor = next(r for r in records if r["url"] == "https://jobs.lever.co/beta/456")
    assert heor["first_seen"] == "2022-01-01T00:00:00Z"
