"""Company_Harness: run one company's Fetcher and Filter_Chain in isolation.

This CLI (design Component 9) fetches a single company from one Platform and
runs the same Filter_Chain the Scraper runs, but scoped so the Dead_Slug_Cache
is never written (Requirement 2.7). It is the quick way to debug a missing or
mis-classified company without a full laptop scrape.

Usage::

    python scripts/diagnose_company.py workday "iqvia|wd1|iqvia" [--detail] [--limit 50] [--all]

It prints:

- the :class:`diagnostics.FetchOutcome` (status, http_status, fetched,
  truncated, host) for the fetch (Requirement 2.1);
- a per-job decision table -- title, location, workplace_type, match_type,
  match_reason, exclusion_reason, category, title_priority (Requirement 2.2);
- with ``--detail``, the extracted skills and summary for each Candidate_Job,
  via the Detail_Enricher and JD_Summarizer (Requirement 2.3);
- per-reason counts at the end.

``--limit N`` caps the rows shown; ``--all`` shows excluded jobs too (by
default only kept primary/maybe jobs are listed, plus the counts).

The per-job steps mirror ``scraper.save_results`` (the monolithic pipeline):
``clean_job_data`` -> ``classify_pre_location`` -> list-data location
classification -> (``enrich_details`` when ``--detail``) -> reclassify ->
``classify_post_detail`` -> ``apply_location_rule`` -> ``assign_category`` +
``finalize_decision`` -> ``summarize_jd``. The same helpers are imported and
called, so the harness and the Scraper stay in step (Requirements 2.1-2.3).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

import personal_config

import filters
import location_filter
from diagnostics import DiagnosticsCollector, RunContext
from detail_enricher import enrich_details, needs_detail
from jd_summarizer import summarize_jd

import scraper


# Map the lowercase CLI platform name to its internal ``_fetch_<platform>``.
# The names match the positional ``platform`` argument; the Scraper's internal
# fetchers take ``(slug, ctx)`` and return ``(jobs, FetchOutcome)``.
PLATFORM_FETCHERS = {
    "greenhouse": scraper._fetch_greenhouse,
    "lever": scraper._fetch_lever,
    "ashby": scraper._fetch_ashby,
    "workday": scraper._fetch_workday,
    "bamboohr": scraper._fetch_bamboohr,
    "icims": scraper._fetch_icims,
}


def build_scoped_context():
    """Build a scoped :class:`RunContext` that never writes the Dead_Slug_Cache.

    ``scoped=True`` is the invariant that keeps the harness from touching shared
    cache files (Requirement 2.7). The Watchlist is irrelevant here but passed
    through for parity with the Scraper.
    """
    watchlist = getattr(personal_config, "WATCHLIST", set()) or set()
    return RunContext(
        diag=DiagnosticsCollector(watchlist=watchlist),
        config=personal_config,
        scoped=True,
    )


def _classify_job(job, cfg, matcher, recruiter, maps):
    """Classify one already-enriched Job_Record, stamping its decision fields.

    Mirrors the per-job steps of ``scraper.save_results`` for a single job:
    the pre-location filter, the post-detail PhD/industry gate, the location
    rule, and ``finalize_decision`` with ``assign_category``. The job dict is
    stamped in place (``match_type``, ``match_reason``, ``exclusion_reason``,
    ``category``, ``title_priority`` and the location fields) and returned.
    """
    # Step 2: pre-location filter (invalid -> blocklist -> recruiter ->
    # senior -> title exclude -> title match). A Decision here is terminal.
    pre = filters.classify_pre_location(job, cfg, matcher, recruiter)
    if pre is not None:
        scraper._stamp_decision_fields(job, pre)
        return job

    title_class = matcher.classify(job.get("title") or "")

    # Step 6: PhD + industry/Category gate (runs after any detail enrichment).
    post = filters.classify_post_detail(job, title_class, cfg, matcher)
    if post is not None:
        scraper._stamp_decision_fields(job, post)
        return job

    # Steps 3/5: location classification + rule on the (possibly enriched) data.
    results = location_filter.classify_job_locations(job, maps)
    verdict = location_filter.apply_location_rule(
        results, secondary_region_states=cfg.SECONDARY_REGION_STATES
    )
    scraper._stamp_location_fields(job, verdict)

    # Step 7: category + final decision via the location adapter bridge.
    tier = job.get("skill_level") or scraper.job_tier_classification(
        job.get("title") or ""
    )
    category = filters.assign_category(
        job.get("title") or "", title_class, job.get("description") or "", matcher, cfg
    )
    decision = filters.finalize_decision(
        job,
        title_class,
        tier,
        scraper._loc_adapter(verdict),
        matcher=matcher,
        category=category,
    )
    scraper._stamp_decision_fields(job, decision)
    return job


def diagnose(platform, slug, detail=False, limit=None, show_all=False,
             ctx=None, fetcher=None, out=None):
    """Fetch one company and run the Filter_Chain, printing the results.

    ``fetcher`` is injectable (``(slug, ctx) -> (jobs, FetchOutcome)``) so tests
    can run the harness fully offline; by default it is looked up from
    :data:`PLATFORM_FETCHERS` by ``platform``. ``out`` is the output stream
    (defaults to ``sys.stdout``). Returns the per-reason ``Counter`` so tests
    can assert on it without parsing stdout.
    """
    out = out or sys.stdout
    if ctx is None:
        ctx = build_scoped_context()
    cfg = ctx.config or personal_config

    if fetcher is None:
        fetcher = PLATFORM_FETCHERS.get(platform)
        if fetcher is None:
            raise ValueError(
                f"Unknown platform {platform!r}. "
                f"Choose one of: {', '.join(sorted(PLATFORM_FETCHERS))}."
            )

    print(f"Company harness: platform={platform} slug={slug!r} "
          f"(scoped, Dead_Slug_Cache untouched)", file=out)

    # --- Fetch (Requirement 2.1). run_fetch catches exceptions. ---
    jobs, outcome = scraper.run_fetch(platform, fetcher, slug, ctx)

    print("\nFetch outcome:", file=out)
    print(f"  status      : {outcome.status}", file=out)
    print(f"  http_status : {outcome.http_status}", file=out)
    print(f"  fetched     : {outcome.fetched}", file=out)
    print(f"  truncated   : {outcome.truncated}", file=out)
    print(f"  host        : {outcome.host}", file=out)
    if outcome.reported_total is not None:
        print(f"  reported_total : {outcome.reported_total}", file=out)
    if outcome.error_type:
        print(f"  error       : {outcome.error_type}: {outcome.error_message}",
              file=out)

    jobs = scraper.clean_job_data(jobs)

    # Build the Filter_Chain helpers once (pure, reusable).
    matcher = filters.build_title_matcher(cfg)
    recruiter = filters.build_recruiter_detector(cfg)
    maps = scraper.get_location_maps()

    # --- Optional detail enrichment (Requirement 2.3). ---
    # Enrich the Candidate_Jobs (those that survive the pre-location filter and
    # that needs_detail flags) before the final classification, matching the
    # Scraper's order.
    if detail and jobs:
        candidates = [
            job for job in jobs
            if filters.classify_pre_location(job, cfg, matcher, recruiter) is None
            and needs_detail(job)
        ]
        if candidates:
            print(f"\nEnriching details for {len(candidates)} candidate job(s)...",
                  file=out)
            enrich_details(candidates, ctx)

    # --- Classify every job. ---
    reason_counts: Counter = Counter()
    for job in jobs:
        _classify_job(job, cfg, matcher, recruiter, maps)
        key = (job.get("exclusion_reason")
               or job.get("match_reason")
               or job.get("match_type")
               or "unknown")
        reason_counts[key] += 1

        # With --detail, attach skills/summary for kept Candidate_Jobs so the
        # table can show them (Requirement 2.3).
        if detail and job.get("match_type") in ("primary", "maybe"):
            summary = summarize_jd(job.get("title") or "", job.get("description") or "")
            job["skills"] = summary.get("skills") or []
            job["summary"] = summary.get("summary") or ""

    # --- Per-job decision table (Requirement 2.2). ---
    shown = [
        job for job in jobs
        if show_all or job.get("match_type") in ("primary", "maybe")
    ]
    if limit is not None:
        shown = shown[:limit]

    print(f"\nDecisions ({len(shown)} shown"
          f"{'' if show_all else ', kept only'}"
          f"{f', limited to {limit}' if limit is not None else ''}):",
          file=out)
    _print_table(shown, out)

    if detail:
        _print_detail(shown, out)

    # --- Per-reason counts (Requirements 2.1-2.3). ---
    print("\nCounts per reason:", file=out)
    for reason, count in sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {reason:<24} {count}", file=out)
    print(f"  {'TOTAL':<24} {sum(reason_counts.values())}", file=out)

    return reason_counts


def _trim(value, width):
    """Return ``value`` as a string trimmed to ``width`` characters."""
    text = "" if value is None else str(value)
    text = text.replace("\n", " ").strip()
    if len(text) > width:
        return text[: width - 1] + "\u2026"
    return text


def _print_table(jobs, out):
    """Print the per-job decision table (Requirement 2.2)."""
    header = (
        f"{'title':<36} {'location':<24} {'workplace':<9} "
        f"{'match':<8} {'reason':<22} {'exclusion':<20} "
        f"{'category':<20} {'pri':>3}"
    )
    print(header, file=out)
    print("-" * len(header), file=out)
    if not jobs:
        print("  (no jobs)", file=out)
        return
    for job in jobs:
        print(
            f"{_trim(job.get('title'), 36):<36} "
            f"{_trim(job.get('location'), 24):<24} "
            f"{_trim(job.get('workplace_type'), 9):<9} "
            f"{_trim(job.get('match_type'), 8):<8} "
            f"{_trim(job.get('match_reason'), 22):<22} "
            f"{_trim(job.get('exclusion_reason'), 20):<20} "
            f"{_trim(job.get('category'), 20):<20} "
            f"{_trim(job.get('title_priority'), 3):>3}",
            file=out,
        )


def _print_detail(jobs, out):
    """Print extracted skills and summary per kept job (Requirement 2.3)."""
    kept = [j for j in jobs if j.get("match_type") in ("primary", "maybe")]
    if not kept:
        return
    print("\nDetail (skills and summary):", file=out)
    for job in kept:
        print(f"\n  {_trim(job.get('title'), 70)}", file=out)
        skills = job.get("skills") or []
        print(f"    skills : {', '.join(skills) if skills else '(none)'}", file=out)
        summary = job.get("summary") or ""
        print(f"    summary: {summary if summary else '(none)'}", file=out)


def _parse_args(argv=None):
    """Parse the Company_Harness CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="diagnose_company.py",
        description=(
            "Run one company's Fetcher and the Filter_Chain in isolation "
            "(scoped: the Dead_Slug_Cache is never written)."
        ),
    )
    parser.add_argument(
        "platform",
        choices=sorted(PLATFORM_FETCHERS),
        help="ATS platform to fetch from.",
    )
    parser.add_argument(
        "slug",
        help="Company slug (e.g. a Workday slug 'iqvia|wd1|iqvia').",
    )
    parser.add_argument(
        "--detail",
        action="store_true",
        help="Run the Detail_Enricher and JD_Summarizer and print skills/summary.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Show at most N jobs in the decision table.",
    )
    parser.add_argument(
        "--all",
        dest="show_all",
        action="store_true",
        help="Show all jobs, including excluded ones (default: kept jobs only).",
    )
    return parser.parse_args(argv)


def main(argv=None):
    """CLI entry point."""
    args = _parse_args(argv)
    diagnose(
        args.platform,
        args.slug,
        detail=args.detail,
        limit=args.limit,
        show_all=args.show_all,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
