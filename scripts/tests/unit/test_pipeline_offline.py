"""Offline end-to-end pipeline test for ``scraper.save_results`` (task 11.2).

This drives a hand-built list of Job_Records through the whole Filter_Chain
pipeline that ``save_results`` wires together (clean -> pre-location filter ->
list-data location classification -> detail enrichment -> post-detail PhD /
industry gate -> reclassify -> location rule -> finalize / category / priority
-> JD summary -> sort / caps -> write artifacts) and asserts on the three
output artifacts:

- ``all_jobs.json``: per-job ``match_type`` / ``match_reason`` /
  ``exclusion_reason`` / ``category`` / ``title_priority``, and the
  priority-1-before-priority-2 ordering inside the primary bucket.
- the gzipped chunk slim records: they carry the new Filter_Chain fields and
  omit the full ``description``.
- ``diagnostics.json``: per-reason (match / exclusion) and per-Category counts,
  plus the detail-failure tally for the market-research job whose detail fetch
  failed.

Everything runs offline: ``scraper.get_location_maps`` is patched to the small
in-memory maps (so ``locations.json`` is never read), ``scraper.enrich_details``
is replaced with a fixture-driven stand-in (so no network), and the output and
diagnostics paths are redirected into ``tmp_path``.

Validates: Requirements 1.5, 7.14, 7.15, 7.17, 7.21, 7.22, 10.9, 12.5, 13.7,
18.5, 18.6
"""

import gzip
import json
import os

import pytest

import scraper
from diagnostics import DiagnosticsCollector, RunContext


# ============================================================
# Fixture Job_Records
# ============================================================
#
# Every job starts with an empty ``description`` so ``needs_detail`` fires and
# the job flows through the (patched) enricher, which fills a description based
# on the ``_fixture`` tag. The tag is also how the fake enricher knows to give
# the "bare remote -> US" job a US country, to synthesize the iCIMS detail, and
# to fail the market-research detail fetch.


def _job(fixture, *, title, company, ats, location, locations=None, **extra):
    job = {
        "_fixture": fixture,
        "title": title,
        "company": company,
        "company_slug": company,
        "ats": ats,
        "location": location,
        "locations": locations if locations is not None else ([location] if location else []),
        "description": "",
        "url": f"https://example.test/{company}/{fixture}",
        "published_at": "2024-01-15T00:00:00Z",
    }
    job.update(extra)
    return job


def _build_jobs():
    """The fixture Job_Records spanning every pipeline branch the task names."""
    return [
        # --- Kept, primary (within radius / remote US), priority 2 ---
        _job("boston_onsite", title="Data Analyst", company="bostonco",
             ats="Greenhouse", location="Boston, MA"),
        _job("lowell_onsite", title="Data Analyst", company="lowellco",
             ats="Greenhouse", location="Lowell, MA"),
        _job("remote_us", title="Data Analyst", company="remoteusco",
             ats="Greenhouse", location="Remote - US"),
        # Bare "Remote" whose detail resolves a US country -> promoted to primary.
        _job("bare_remote_us", title="Data Analyst", company="bareremoteusco",
             ats="Greenhouse", location="Remote"),

        # --- Kept, maybe ---
        # Worcester is New England outside the 30-mile radius -> Secondary_Region.
        _job("worcester_onsite", title="Data Analyst", company="worcesterco",
             ats="Greenhouse", location="Worcester, MA"),
        # Bare "Remote" with no US signal -> remote_unspecified.
        _job("bare_remote", title="Data Analyst", company="bareremoteco",
             ats="Greenhouse", location="Remote"),
        # A plain "Analyst" title -> generic analyst maybe.
        _job("generic_analyst", title="Analyst", company="genericco",
             ats="Greenhouse", location="Boston, MA"),
        # A Senior Data Scientist -> primary title + senior tier -> maybe.
        _job("senior_ds", title="Senior Data Scientist", company="seniorco",
             ats="Greenhouse", location="Boston, MA"),

        # --- Kept, primary, priority 1 (Data_Scientist_Title) ---
        _job("data_scientist", title="Data Scientist", company="dsco",
             ats="Greenhouse", location="Boston, MA"),

        # --- Excluded on location ---
        _job("sf_onsite", title="Data Analyst", company="sfco",
             ats="Greenhouse", location="San Francisco, CA"),
        _job("remote_canada", title="Data Analyst", company="canadaco",
             ats="Greenhouse", location="Remote, Canada"),

        # --- Excluded post-detail ---
        # PhD required in the description.
        _job("phd_required", title="Data Analyst", company="phdco",
             ats="Greenhouse", location="Boston, MA"),
        # Market research with a non-life-sciences description -> non_target_industry.
        _job("market_research_no_signal", title="Market Research Analyst",
             company="mrco", ats="Greenhouse", location="Boston, MA"),
        # Market research whose detail fetch fails -> non_target_industry
        # (detail-failure subset, counted separately).
        _job("market_research_detail_failed", title="Market Research Analyst",
             company="mrfailco", ats="Greenhouse", location="Boston, MA"),

        # --- iCIMS job that needs detail (no list location/description) ---
        _job("icims_detail", title="Data Analyst", company="icimsco",
             ats="iCIMS", location="", locations=[]),
    ]


# Descriptions the fake enricher applies, keyed by fixture tag. Kept short; the
# industry gate and PhD gate only read the description text.
_PLAIN_DESC = "We are hiring an analyst to work with data and reporting."
_PHD_DESC = "A PhD is required for this role. Strong statistics background."
_MR_NO_SIGNAL_DESC = (
    "Market research analyst for a consumer electronics retailer. "
    "Survey design and competitive analysis for our product teams."
)


def _fake_enrich_details(candidates, ctx):
    """Fixture-driven stand-in for ``detail_enricher.enrich_details``.

    Applies a canned description (and, where relevant, a US country, an iCIMS
    location/description, or a detail failure) to each candidate based on its
    ``_fixture`` tag. Mirrors the real enricher's side effects on the
    Job_Record and records a detail failure in ``ctx.diag`` so the diagnostics
    tally is populated, but makes no network calls.
    """
    for job in candidates:
        tag = job.get("_fixture")

        if tag == "bare_remote_us":
            job["description"] = _PLAIN_DESC
            job["native_country"] = "United States"
        elif tag == "phd_required":
            job["description"] = _PHD_DESC
        elif tag == "market_research_no_signal":
            job["description"] = _MR_NO_SIGNAL_DESC
        elif tag == "market_research_detail_failed":
            # Detail fetch failed: no description, marked and counted.
            job["detail_failed"] = True
            diag = getattr(ctx, "diag", None)
            if diag is not None:
                diag.record_detail_failure(
                    job.get("ats") or "", job.get("company_slug") or "", "boom"
                )
        elif tag == "icims_detail":
            # The detail response supplies the real location and description.
            job["description"] = _PLAIN_DESC
            job["location"] = "Boston, MA"
            job["locations"] = ["Boston, MA"]
        else:
            job["description"] = _PLAIN_DESC


# ============================================================
# Pipeline run harness
# ============================================================


@pytest.fixture
def pipeline(tmp_path, monkeypatch, small_location_maps):
    """Run ``save_results`` offline and return the parsed artifacts.

    Returns a dict with ``all_jobs`` (list), ``by_fixture`` (tag -> record for
    kept jobs), ``chunk`` (the slim records from the gzipped chunk), and
    ``diagnostics`` (the diagnostics.json report).
    """
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    diag_file = tmp_path / "diagnostics.json"

    # Redirect every path save_results / _finalize_diagnostics writes to.
    monkeypatch.setattr(scraper, "OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(scraper, "DIAGNOSTICS_FILE", str(diag_file))
    # No locations.json read: use the small in-memory maps.
    monkeypatch.setattr(scraper, "get_location_maps", lambda: small_location_maps)
    # No network: fixture-driven detail enrichment.
    monkeypatch.setattr(scraper, "enrich_details", _fake_enrich_details)

    jobs = _build_jobs()
    all_companies = sorted({j["company"] for j in jobs})
    active_companies = {c: 1 for c in all_companies}

    ctx = RunContext(
        diag=DiagnosticsCollector(watchlist=set()),
        config=scraper.personal_config,
        scoped=True,
    )

    scraper.save_results(all_companies, active_companies, jobs, ctx)
    # save_results does not write diagnostics; _finalize_diagnostics does.
    scraper._finalize_diagnostics(ctx.diag, scoped=True)

    all_jobs = json.loads(
        (output_dir / "all_jobs.json").read_text(encoding="utf-8")
    )
    by_fixture = {j.get("_fixture"): j for j in all_jobs}

    manifest = json.loads(
        (output_dir / "chunks" / "jobs_manifest.json").read_text(encoding="utf-8")
    )
    chunk = []
    for name in manifest["chunks"]:
        with gzip.open(output_dir / "chunks" / name, "rt", encoding="utf-8") as fh:
            chunk.extend(json.load(fh))

    diagnostics = json.loads(diag_file.read_text(encoding="utf-8"))

    return {
        "all_jobs": all_jobs,
        "by_fixture": by_fixture,
        "chunk": chunk,
        "diagnostics": diagnostics,
    }


# ============================================================
# all_jobs.json: per-job decisions
# ============================================================


def _assert_match(job, *, match_type, match_reason=None, exclusion_reason=None,
                  category=None, title_priority=None):
    assert job["match_type"] == match_type, job.get("_fixture")
    if match_reason is not None:
        assert job["match_reason"] == match_reason, job.get("_fixture")
    if exclusion_reason is not None:
        assert job["exclusion_reason"] == exclusion_reason, job.get("_fixture")
    if category is not None:
        assert job["category"] == category, job.get("_fixture")
    if title_priority is not None:
        assert job["title_priority"] == title_priority, job.get("_fixture")


def test_kept_jobs_have_expected_type_reason_category_priority(pipeline):
    """Kept jobs carry the right Match_Type / Match_Reason / Category / priority.

    Covers Requirements 7.14/7.15/7.21/7.22 (reason routing), 18.5/18.6
    (Title_Priority) and 1.5 (every decision is captured on the record).
    """
    kept = pipeline["by_fixture"]

    # Clean primary, within radius / remote US, priority 2, other_target.
    for tag in ("boston_onsite", "lowell_onsite", "remote_us", "bare_remote_us"):
        _assert_match(kept[tag], match_type="primary", match_reason="title_primary",
                      category="other_target", title_priority=2)

    # Worcester -> New England Secondary_Region maybe (7.21).
    _assert_match(kept["worcester_onsite"], match_type="maybe",
                  match_reason="location_new_england", category="other_target")

    # Bare remote with no US signal -> remote_unspecified maybe (7.22, 10.9).
    _assert_match(kept["bare_remote"], match_type="maybe",
                  match_reason="remote_unspecified", category="other_target")

    # Generic analyst -> title_generic_analyst maybe.
    _assert_match(kept["generic_analyst"], match_type="maybe",
                  match_reason="title_generic_analyst", category="other_target")

    # Senior Data Scientist -> senior tier signal maybe, priority 1 (18.5/18.6).
    _assert_match(kept["senior_ds"], match_type="maybe",
                  match_reason="tier_senior_signal", category="data_scientist",
                  title_priority=1)

    # Data Scientist -> clean primary, priority 1, data_scientist category.
    _assert_match(kept["data_scientist"], match_type="primary",
                  match_reason="title_primary", category="data_scientist",
                  title_priority=1)

    # iCIMS job needing detail: detail resolved Boston onsite -> primary.
    _assert_match(kept["icims_detail"], match_type="primary",
                  match_reason="title_primary", category="other_target",
                  title_priority=2)


def test_excluded_jobs_are_dropped_with_the_right_reason(pipeline):
    """Excluded jobs never appear in all_jobs.json; their reasons are recorded.

    SF -> outside_radius, remote Canada -> non_us (7.14); PhD -> phd_required;
    market-research-no-signal and the detail-failure job -> non_target_industry.
    """
    kept = pipeline["by_fixture"]
    excluded_tags = {
        "sf_onsite", "remote_canada", "phd_required",
        "market_research_no_signal", "market_research_detail_failed",
    }
    # None of the excluded fixtures survive into all_jobs.json.
    for tag in excluded_tags:
        assert tag not in kept, tag

    # The exclusion reasons are visible in the diagnostics per-reason counts.
    gh = pipeline["diagnostics"]["platforms"]["Greenhouse"]["decisions"]
    excl = gh["exclusion_reason"]
    assert excl.get("outside_radius") == 1
    assert excl.get("non_us") == 1
    assert excl.get("phd_required") == 1
    # Two market-research jobs fail the industry gate.
    assert excl.get("non_target_industry") == 2


# ============================================================
# all_jobs.json: ordering
# ============================================================


def test_priority_one_jobs_sort_before_priority_two_in_primary_bucket(pipeline):
    """Within the primary bucket, priority-1 jobs come before priority-2 (7.17)."""
    primary = [j for j in pipeline["all_jobs"] if j["match_type"] == "primary"]
    priorities = [j["title_priority"] for j in primary]

    # There is at least one priority-1 (data_scientist) and several priority-2.
    assert 1 in priorities
    assert 2 in priorities
    # Non-decreasing: every priority-1 appears before any priority-2.
    assert priorities == sorted(priorities)
    # Concretely, the Data Scientist leads the primary bucket.
    assert primary[0]["_fixture"] == "data_scientist"


# ============================================================
# Chunk slim records
# ============================================================


def test_chunk_records_carry_new_fields_and_omit_description(pipeline):
    """Slim chunk records carry the Filter_Chain fields but drop the full JD (13.7)."""
    chunk = pipeline["chunk"]
    assert chunk, "expected at least one slim record"

    # The chunk holds exactly the kept jobs.
    assert len(chunk) == len(pipeline["all_jobs"])

    for record in chunk:
        # New Filter_Chain / enrichment fields are present.
        for field in ("match_type", "match_reason", "category", "title_priority"):
            assert field in record, field
        # The full description is intentionally excluded from the chunk.
        assert "description" not in record

    # The full description still lives in all_jobs.json.
    assert all("description" in j for j in pipeline["all_jobs"])


# ============================================================
# diagnostics.json: per-reason / per-category counts
# ============================================================


def test_diagnostics_has_per_reason_and_per_category_counts(pipeline):
    """diagnostics.json records match-reason, category, and the detail tally (1.5, 1.9)."""
    gh = pipeline["diagnostics"]["platforms"]["Greenhouse"]
    decisions = gh["decisions"]

    # Per-match-reason counts for kept Greenhouse jobs. title_primary covers
    # boston, lowell, remote_us, bare_remote_us, and the Data Scientist.
    reasons = decisions["match_reason"]
    assert reasons.get("title_primary") == 5
    assert reasons.get("location_new_england") == 1
    assert reasons.get("remote_unspecified") == 1
    assert reasons.get("title_generic_analyst") == 1
    assert reasons.get("tier_senior_signal") == 1

    # Per-Category counts: data_scientist (data_scientist + senior_ds) and
    # other_target (the other seven kept Greenhouse jobs).
    category = decisions["category"]
    assert category.get("data_scientist") == 2
    assert category.get("other_target") == 7

    # The market-research detail-failure exclusion is counted in the
    # detail-failure tally (Requirement 1.9).
    assert decisions["non_target_industry_detail_failed"] == 1

    # The iCIMS job's Category is recorded on its own platform entry.
    icims = pipeline["diagnostics"]["platforms"]["iCIMS"]["decisions"]
    assert icims["match_type"].get("primary") == 1
    assert icims["category"].get("other_target") == 1
