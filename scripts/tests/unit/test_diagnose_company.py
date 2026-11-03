"""Unit tests for the Company_Harness (``scripts/diagnose_company.py``).

These drive ``diagnose_company.diagnose`` fully offline. The harness takes an
injectable ``fetcher`` (``(slug, ctx) -> (jobs, FetchOutcome)``) and an ``out``
stream, so no ``requests.Session`` is ever created. The 29 MB
``data/locations.json`` is never touched either: ``scraper.get_location_maps``
is patched to a tiny GeoNames-shaped lookup built from a handful of rows.

Covered:
- The printed Fetch_Outcome (status and fetched count) appears, so a user can
  see what the Fetcher returned (Requirement 2.1).
- The per-job decision table shows each job's title, location, workplace_type,
  match_type, match_reason, exclusion_reason, category, and title_priority
  (Requirement 2.2).
- With the detail option, the Detail_Enricher and JD_Summarizer run on the
  Candidate_Jobs and the extracted skills and summary are printed
  (Requirement 2.3).
- The returned per-reason ``Counter`` matches the jobs' decisions, so tests can
  assert on it without parsing stdout.

Validates: Requirements 2.1, 2.2, 2.3
"""

import io
import json
import tempfile
from collections import Counter
from pathlib import Path

import pytest

import geolocation
import scraper
import diagnose_company
from diagnostics import FetchOutcome


# ============================================================
# Small location maps (no 29 MB locations.json load)
# ============================================================

# Just the towns these tests reference. Columns match data/locations.json:
# city, admin (GeoNames admin1 / two-letter US state), country, lat, lng.
_ROWS = [
    ["Boston", "MA", "US", 42.3601, -71.0589],
    ["Worcester", "MA", "US", 42.2626, -71.8023],  # New England, outside radius
]
_SCHEMA = ["city", "admin", "country", "lat", "lng"]


@pytest.fixture
def small_maps(monkeypatch):
    """Patch ``scraper.get_location_maps`` to a tiny lookup and return it.

    Built through the real ``build_lookup`` so the shape matches runtime, just
    small enough to load instantly. Patching here is what keeps the harness from
    loading ``locations.json``.
    """
    payload = {"schema": _SCHEMA, "data": _ROWS}
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as fh:
        json.dump(payload, fh)
        temp_path = fh.name
    try:
        maps = geolocation.build_lookup(temp_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)
    monkeypatch.setattr(scraper, "get_location_maps", lambda: maps)
    return maps


# ============================================================
# Fake fetcher
# ============================================================

# Three representative jobs: a Data Scientist in Boston (primary), a Janitor
# that no title pattern matches (excluded), and a Data Scientist in Worcester
# MA -- onsite, inside New England but outside the Boston radius, so a "maybe".
_JOBS = [
    {
        "title": "Data Scientist",
        "company_slug": "acme",
        "company": "Acme",
        "ats": "greenhouse",
        "url": "https://example.test/jobs/1",
        "location": "Boston, MA",
        "workplace_type": "onsite",
        "description": "Build models in Python and SQL.",
    },
    {
        "title": "Janitor",
        "company_slug": "acme",
        "company": "Acme",
        "ats": "greenhouse",
        "url": "https://example.test/jobs/2",
        "location": "Boston, MA",
        "workplace_type": "onsite",
        "description": "",
    },
    {
        "title": "Data Scientist",
        "company_slug": "acme",
        "company": "Acme",
        "ats": "greenhouse",
        "url": "https://example.test/jobs/3",
        "location": "Worcester, MA",
        "workplace_type": "onsite",
        "description": "Analytics in Python.",
    },
]


def _fake_fetcher(jobs=None, outcome=None):
    """Build an injectable fetcher ``(slug, ctx) -> (jobs, FetchOutcome)``."""
    jobs = _JOBS if jobs is None else jobs

    def _fetch(slug, ctx):
        out = outcome or FetchOutcome(
            platform="greenhouse", slug=slug, status="ok", fetched=len(jobs)
        )
        # Return fresh copies so one test's stamping cannot leak into another.
        return [dict(job) for job in jobs], out

    return _fetch


# ============================================================
# Requirement 2.1 + 2.2: outcome and the decision table
# ============================================================


def test_prints_fetch_outcome_status_and_count(small_maps):
    """The harness prints the Fetch_Outcome status and fetched count (2.1)."""
    out = io.StringIO()
    diagnose_company.diagnose(
        "greenhouse", "acme", fetcher=_fake_fetcher(), out=out, show_all=True
    )
    text = out.getvalue()

    assert "Fetch outcome:" in text
    assert "status      : ok" in text
    assert "fetched     : 3" in text


def test_decision_table_shows_all_columns(small_maps):
    """The per-job table header carries every column Requirement 2.2 names."""
    out = io.StringIO()
    diagnose_company.diagnose(
        "greenhouse", "acme", fetcher=_fake_fetcher(), out=out, show_all=True
    )
    text = out.getvalue()

    for column in ("title", "location", "workplace", "match", "reason",
                   "exclusion", "category", "pri"):
        assert column in text, f"missing column header: {column}"


def test_decision_table_shows_each_jobs_decision(small_maps):
    """Each job's title and its resolved decision appear in the table (2.2)."""
    out = io.StringIO()
    diagnose_company.diagnose(
        "greenhouse", "acme", fetcher=_fake_fetcher(), out=out, show_all=True
    )
    text = out.getvalue()

    # The Boston Data Scientist is a clean primary with Title_Priority 1.
    assert "Data Scientist" in text
    assert "primary" in text
    assert "title_primary" in text
    assert "data_scientist" in text

    # The Janitor matches no title pattern and is excluded.
    assert "Janitor" in text
    assert "title_no_match" in text

    # The Worcester Data Scientist is a New England "maybe".
    assert "location_new_england" in text


def test_returned_counter_matches_decisions(small_maps):
    """The returned per-reason Counter matches the three jobs' decisions."""
    out = io.StringIO()
    counts = diagnose_company.diagnose(
        "greenhouse", "acme", fetcher=_fake_fetcher(), out=out, show_all=True
    )

    assert isinstance(counts, Counter)
    assert counts == Counter(
        {
            "title_primary": 1,
            "title_no_match": 1,
            "location_new_england": 1,
        }
    )
    assert sum(counts.values()) == 3


def test_kept_only_default_hides_excluded_rows(small_maps):
    """Without ``--all`` the table lists kept jobs only, but counts stay total."""
    out = io.StringIO()
    counts = diagnose_company.diagnose(
        "greenhouse", "acme", fetcher=_fake_fetcher(), out=out, show_all=False
    )
    text = out.getvalue()

    # Kept jobs are shown; the excluded Janitor is not in the table.
    assert "Data Scientist" in text
    assert "Janitor" not in text
    # The counts still cover every fetched job (Requirement 2.1 summary).
    assert counts == Counter(
        {"title_primary": 1, "title_no_match": 1, "location_new_england": 1}
    )


# ============================================================
# Requirement 2.3: the detail option prints skills and summary
# ============================================================


def test_detail_option_runs_enricher_and_summarizer(small_maps, monkeypatch):
    """With detail=True the kept jobs get skills/summary and they are printed.

    The Detail_Enricher and JD_Summarizer are monkeypatched so the test stays
    offline: ``enrich_details`` fills in a description, and ``summarize_jd``
    returns a fixed skills/summary payload. The harness must print both for the
    kept Candidate_Jobs (Requirement 2.3).
    """
    # A single Boston Data Scientist with no list-data description, so
    # ``needs_detail`` fires and the enricher runs.
    job = dict(_JOBS[0])
    job["description"] = ""

    enriched_calls = []

    def fake_enrich(jobs, ctx):
        enriched_calls.append(len(jobs))
        for j in jobs:
            j["description"] = "Build propensity models in Python and SQL."
        return jobs

    summarize_calls = []

    def fake_summarize(title, description, backend=None):
        summarize_calls.append((title, description))
        return {
            "skills": ["Python", "SQL"],
            "summary": "Builds propensity models.",
            "backend": "keyword",
        }

    monkeypatch.setattr(diagnose_company, "enrich_details", fake_enrich)
    monkeypatch.setattr(diagnose_company, "summarize_jd", fake_summarize)

    out = io.StringIO()
    diagnose_company.diagnose(
        "greenhouse",
        "acme",
        detail=True,
        fetcher=_fake_fetcher(jobs=[job]),
        out=out,
        show_all=True,
    )
    text = out.getvalue()

    # The enricher ran on the one Candidate_Job, and the summarizer too.
    assert enriched_calls == [1]
    assert len(summarize_calls) == 1

    # The detail section prints the extracted skills and summary.
    assert "Detail (skills and summary):" in text
    assert "Python" in text
    assert "SQL" in text
    assert "Builds propensity models." in text


def test_detail_option_skips_summary_for_excluded_jobs(small_maps, monkeypatch):
    """Skills/summary are only attached to kept jobs, not excluded ones (2.3)."""
    jobs = [dict(_JOBS[0]), dict(_JOBS[1])]  # Data Scientist (kept) + Janitor
    for j in jobs:
        j["description"] = ""  # force needs_detail for the candidate

    def fake_enrich(enriching, ctx):
        for j in enriching:
            j["description"] = "Python and SQL modelling."
        return enriching

    summarize_titles = []

    def fake_summarize(title, description, backend=None):
        summarize_titles.append(title)
        return {"skills": ["Python"], "summary": "A data role.", "backend": "keyword"}

    monkeypatch.setattr(diagnose_company, "enrich_details", fake_enrich)
    monkeypatch.setattr(diagnose_company, "summarize_jd", fake_summarize)

    out = io.StringIO()
    diagnose_company.diagnose(
        "greenhouse",
        "acme",
        detail=True,
        fetcher=_fake_fetcher(jobs=jobs),
        out=out,
        show_all=True,
    )

    # Only the kept Data Scientist is summarized; the Janitor is not.
    assert summarize_titles == ["Data Scientist"]
