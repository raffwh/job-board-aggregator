"""Unit tests for ``run_fetch`` exception capture and the scoped-run CLI.

These cover:

- ``run_fetch`` records the exception type and message (Requirement 1.3).
- ``--platforms`` fetches only the listed platforms (Requirement 2.4).
- ``--max-companies N`` fetches at most N companies per platform (Requirement 2.5).
- ``--slugs`` fetches only the listed slugs (Requirement 2.6).
- A scoped run leaves the Dead_Slug_Cache files byte-identical (Requirement 2.7).
- Watchlist rows are written and the per-platform summary table prints to
  stdout (Requirements 1.7, 1.8).

Every test injects fake fetchers (never a real ``requests.Session``) so the
suite makes no network calls.
"""

import json

import pytest

import scraper
from diagnostics import DiagnosticsCollector, FetchOutcome, RunContext


# ============================================================
# Helpers
# ============================================================


def _ok_outcome(platform, slug, n):
    """A successful FetchOutcome reporting ``n`` fetched jobs."""
    return FetchOutcome(platform=platform, slug=slug, status="ok", fetched=n)


def _fake_fetcher(platform, jobs_per_slug=1, seen=None):
    """Build an internal-style fetcher ``_fetch(slug, ctx) -> (jobs, outcome)``.

    Every slug the fetcher is asked to fetch is appended to ``seen`` (when
    given), so a test can assert exactly which slugs were fetched.
    """

    def _fetch(slug, ctx):
        if seen is not None:
            seen.append(slug)
        jobs = [
            {"title": f"Job {i}", "company_slug": slug, "ats": platform}
            for i in range(jobs_per_slug)
        ]
        return jobs, _ok_outcome(platform, slug, len(jobs))

    return _fetch


def _scoped_ctx(**kwargs):
    """A scoped RunContext with a fresh collector."""
    return RunContext(
        diag=DiagnosticsCollector(watchlist=kwargs.pop("watchlist", set())),
        config=None,
        scoped=kwargs.pop("scoped", True),
        max_companies=kwargs.pop("max_companies", None),
        slugs=kwargs.pop("slugs", None),
    )


# ============================================================
# Requirement 1.3: run_fetch exception capture
# ============================================================


def test_run_fetch_records_exception_type_and_message():
    """A raising fetcher yields status 'exception' with type and message (1.3)."""

    def _boom(slug, ctx):
        raise ValueError("kaboom slug=%s" % slug)

    jobs, outcome = scraper.run_fetch("greenhouse", _boom, "acme", _scoped_ctx())

    assert jobs == []
    assert outcome.status == "exception"
    assert outcome.error_type == "ValueError"
    assert "kaboom" in outcome.error_message
    assert outcome.platform == "greenhouse"
    assert outcome.slug == "acme"
    # The single place exceptions are caught also times the call.
    assert outcome.elapsed_s >= 0.0


def test_run_fetch_passes_through_successful_outcome():
    """A non-raising fetcher's jobs and outcome pass straight through."""
    fetch = _fake_fetcher("lever", jobs_per_slug=2)
    jobs, outcome = scraper.run_fetch("lever", fetch, "widgets", _scoped_ctx())

    assert len(jobs) == 2
    assert outcome.status == "ok"
    assert outcome.error_type is None
    assert outcome.fetched == 2


def test_run_fetch_exception_counts_under_exception_status():
    """run_fetch's exception outcome records as status 'exception' in diagnostics (1.3)."""

    def _boom(slug, ctx):
        raise RuntimeError("down")

    ctx = _scoped_ctx()
    _, outcome = scraper.run_fetch("ashby", _boom, "foo", ctx)
    ctx.diag.record_outcome(outcome)

    report = ctx.diag.to_report()
    assert report["platforms"]["ashby"]["status_counts"]["exception"] == 1
    assert report["platforms"]["ashby"]["exception_types"]["RuntimeError"] == 1


# ============================================================
# Requirement 2.6: --slugs restricts to the listed slugs
# ============================================================


def test_slug_filter_fetches_only_listed_slugs():
    """fetch_all_jobs with scoped slugs fetches only those slugs (2.6)."""
    seen = []
    fetch = _fake_fetcher("greenhouse", seen=seen)
    companies = ["alpha", "beta", "gamma", "delta"]
    ctx = _scoped_ctx(slugs=frozenset({"beta", "delta"}))

    active, jobs = scraper.fetch_all_jobs(companies, fetch, "GREENHOUSE", ctx)

    assert sorted(seen) == ["beta", "delta"]
    assert set(active) == {"beta", "delta"}


def test_slug_filter_ignores_slugs_from_other_platforms():
    """A slug not in this platform's Company_List is simply not fetched (2.6)."""
    seen = []
    fetch = _fake_fetcher("lever", seen=seen)
    companies = ["alpha", "beta"]
    # "iqvia|wd1|iqvia" belongs to Workday, not this list.
    ctx = _scoped_ctx(slugs=frozenset({"alpha", "iqvia|wd1|iqvia"}))

    scraper.fetch_all_jobs(companies, fetch, "LEVER", ctx)

    assert seen == ["alpha"]


# ============================================================
# Requirement 2.5: --max-companies caps companies per platform
# ============================================================


def test_max_companies_caps_companies_per_platform():
    """At most N companies per platform are fetched (2.5)."""
    seen = []
    fetch = _fake_fetcher("workday", seen=seen)
    companies = [f"c{i}" for i in range(10)]
    ctx = _scoped_ctx(max_companies=3)

    scraper.fetch_all_jobs(companies, fetch, "WORKDAY", ctx)

    assert len(seen) == 3
    assert set(seen).issubset(set(companies))


def test_max_companies_zero_fetches_nothing():
    """A cap of 0 fetches no companies (2.5)."""
    seen = []
    fetch = _fake_fetcher("workday", seen=seen)
    companies = ["a", "b", "c"]
    ctx = _scoped_ctx(max_companies=0)

    scraper.fetch_all_jobs(companies, fetch, "WORKDAY", ctx)

    assert seen == []


def test_slug_then_max_companies_compose():
    """The cap applies after the slug filter (2.5, 2.6)."""
    seen = []
    fetch = _fake_fetcher("greenhouse", seen=seen)
    companies = ["a", "b", "c", "d", "e"]
    ctx = _scoped_ctx(slugs=frozenset({"b", "c", "d"}), max_companies=2)

    scraper.fetch_all_jobs(companies, fetch, "GREENHOUSE", ctx)

    assert len(seen) == 2
    assert set(seen).issubset({"b", "c", "d"})


# ============================================================
# Requirement 2.4: --platforms fetches only the listed platforms
# ============================================================


def test_platform_filter_fetches_only_listed_platforms(monkeypatch):
    """main() with a platforms_filter runs only those platforms' fetchers (2.4)."""
    # Enable several platforms so the filter has something to exclude.
    monkeypatch.setattr(
        scraper,
        "ENABLED_PLATFORMS",
        {"greenhouse", "lever", "workday"},
        raising=False,
    )
    # Each Company_List loads a single slug, regardless of path.
    monkeypatch.setattr(scraper, "load_companies", lambda path: {"only"})
    monkeypatch.setattr(scraper, "load_paylocity", lambda path: set())

    fetched_platforms = []

    def fake_fetch_all_jobs(companies, fetcher, name, ctx):
        fetched_platforms.append(name.lower())
        return {}, []

    monkeypatch.setattr(scraper, "fetch_all_jobs", fake_fetch_all_jobs)
    monkeypatch.setattr(scraper, "save_results", lambda *a, **k: None)
    monkeypatch.setattr(scraper, "_finalize_diagnostics", lambda *a, **k: None)

    scraper.main(platforms_filter=["greenhouse", "lever"])

    assert set(fetched_platforms) == {"greenhouse", "lever"}
    assert "workday" not in fetched_platforms


# ============================================================
# Requirement 2.7: a scoped run leaves the Dead_Slug_Cache untouched
# ============================================================


def test_scoped_run_leaves_dead_slug_cache_byte_identical(tmp_path, monkeypatch):
    """A scoped run never rewrites a cache file, even with a new dead slug (2.7)."""
    dead_dir = tmp_path / "dead_slugs"
    dead_dir.mkdir()
    monkeypatch.setattr(scraper, "DEAD_SLUG_DIR", str(dead_dir))

    # Seed a current-format cache file for greenhouse.
    cache_file = dead_dir / "greenhouse.json"
    seeded = {
        "version": scraper.dead_slug_version("greenhouse"),
        "slugs": ["olddead"],
    }
    cache_file.write_text(json.dumps(seeded, indent=2), encoding="utf-8")
    before = cache_file.read_bytes()

    # A fetcher that reports a permanent 404 for a live slug -- this *would*
    # be cached on a non-scoped run.
    def _fetch_404(slug, ctx):
        return [], FetchOutcome(
            platform="greenhouse",
            slug=slug,
            status="http_error",
            http_status=404,
        )

    ctx = _scoped_ctx(scoped=True)
    scraper.fetch_all_jobs(["newdead"], _fetch_404, "GREENHOUSE", ctx)

    assert cache_file.read_bytes() == before


def test_non_scoped_run_does_write_dead_slug_cache(tmp_path, monkeypatch):
    """Contrast case: a non-scoped run adds a permanent 404 slug to the cache."""
    dead_dir = tmp_path / "dead_slugs"
    dead_dir.mkdir()
    monkeypatch.setattr(scraper, "DEAD_SLUG_DIR", str(dead_dir))

    def _fetch_404(slug, ctx):
        return [], FetchOutcome(
            platform="greenhouse",
            slug=slug,
            status="http_error",
            http_status=404,
        )

    ctx = _scoped_ctx(scoped=False)
    scraper.fetch_all_jobs(["newdead"], _fetch_404, "GREENHOUSE", ctx)

    cache_file = dead_dir / "greenhouse.json"
    assert cache_file.exists()
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert "newdead" in data["slugs"]


# ============================================================
# Requirement 1.7: Watchlist rows
# ============================================================


def test_watchlist_rows_written_for_watched_company():
    """A watched slug's per-job decisions are written to the report (1.7)."""
    diag = DiagnosticsCollector(watchlist={"watchco"})
    diag.record_decision(
        {
            "ats": "greenhouse",
            "company_slug": "watchco",
            "company": "Watch Co",
            "title": "Data Scientist",
            "location": "Boston, MA",
            "workplace_type": "onsite",
            "match_type": "primary",
            "match_reason": "title_primary",
        }
    )
    # A non-watched company produces no watchlist row.
    diag.record_decision(
        {
            "ats": "greenhouse",
            "company_slug": "otherco",
            "title": "Data Scientist",
            "match_type": "primary",
            "match_reason": "title_primary",
        }
    )

    report = diag.to_report()
    rows = report["watchlist"]
    assert "watchco" in rows
    assert "otherco" not in rows
    assert len(rows["watchco"]) == 1
    row = rows["watchco"][0]
    assert row["title"] == "Data Scientist"
    assert row["location"] == "Boston, MA"
    assert row["match_type"] == "primary"
    assert row["match_reason"] == "title_primary"


def test_watchlist_matches_company_name_case_insensitively():
    """Watchlisting works on the company name too, case-insensitively (1.7)."""
    diag = DiagnosticsCollector(watchlist={"Watch Co"})
    diag.record_decision(
        {
            "ats": "lever",
            "company": "Watch Co",
            "title": "Analyst",
            "match_type": "maybe",
            "match_reason": "title_generic_analyst",
        }
    )
    report = diag.to_report()
    assert any(rows for rows in report["watchlist"].values())


# ============================================================
# Requirement 1.8: the per-platform summary table prints to stdout
# ============================================================


def test_print_summary_renders_per_platform_table(capsys):
    """print_summary writes a header and one row per platform to stdout (1.8)."""
    diag = DiagnosticsCollector()
    diag.record_outcome(_ok_outcome("greenhouse", "acme", 5))
    diag.record_decision(
        {
            "ats": "greenhouse",
            "company_slug": "acme",
            "title": "Data Scientist",
            "match_type": "primary",
            "match_reason": "title_primary",
            "category": "data_scientist",
        }
    )

    diag.print_summary()
    out = capsys.readouterr().out

    assert "Diagnostics summary" in out
    # Column header and the platform row are both present.
    assert "Platform" in out
    assert "fetched" in out
    assert "greenhouse" in out


def test_print_summary_marks_scoped_run(capsys):
    """A scoped summary labels itself (1.8)."""
    diag = DiagnosticsCollector()
    diag.record_outcome(_ok_outcome("lever", "x", 1))

    diag.print_summary(scoped=True)
    out = capsys.readouterr().out

    assert "scoped run" in out
    assert "lever" in out


def test_finalize_diagnostics_writes_and_prints(tmp_path, monkeypatch, capsys):
    """_finalize_diagnostics writes diagnostics.json and prints the table (1.1, 1.8)."""
    diag_file = tmp_path / "diagnostics.json"
    monkeypatch.setattr(scraper, "DIAGNOSTICS_FILE", str(diag_file))

    diag = DiagnosticsCollector()
    diag.record_outcome(_ok_outcome("workday", "iqvia|wd1|iqvia", 42))

    scraper._finalize_diagnostics(diag, scoped=True)

    assert diag_file.exists()
    report = json.loads(diag_file.read_text(encoding="utf-8"))
    assert report["scoped"] is True
    assert report["platforms"]["workday"]["jobs_fetched"] == 42

    out = capsys.readouterr().out
    assert "Diagnostics summary" in out
    assert str(diag_file) in out
