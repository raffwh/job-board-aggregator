"""Run diagnostics for the job aggregator.

This module records, per Platform, where postings are lost between fetching and
the final Filter_Chain decision. A single :class:`DiagnosticsCollector` instance
is created per Scraper run and threaded through a small :class:`RunContext`
(``ctx.diag``, ``ctx.config``, ``ctx.scoped``), so no global mutable state is
needed and tests can build a fresh collector per case.

Components:

- :class:`FetchOutcome` -- the per-company result of one Fetcher call.
- :class:`DiagnosticsCollector` -- thread-safe aggregator that produces the
  Diagnostics_Report (``scripts/output/diagnostics.json``) and a stdout table.
- :class:`RunContext` -- the per-run bundle passed to fetchers.
- :func:`default_context` -- a context for the legacy 3-tuple wrappers.

See the design doc, Component 1, and Requirements 1.1, 1.5, 1.6, 1.7, 1.8, 1.9,
15.7.
"""

from __future__ import annotations

import json
import os
import threading
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

# Status values a Fetcher may report (Requirement 1.1).
FETCH_STATUSES = ("ok", "empty", "http_error", "exception", "blocked", "invalid_slug")

# The three Categories a kept Job_Record may carry (Requirement 1.5).
CATEGORIES = ("data_scientist", "life_sciences_research", "other_target")

# The number of top companies listed per Platform (Requirement 1.6).
TOP_COMPANIES_LIMIT = 20

# Cap on stored error messages so one huge traceback cannot bloat the report.
_ERROR_MESSAGE_CAP = 300


@dataclass
class FetchOutcome:
    """The per-company result of one Fetcher call.

    A Fetcher returns this alongside the normalized Job_Records (Requirement
    1.2). ``status`` is one of :data:`FETCH_STATUSES`. ``truncated`` with
    ``reported_total`` and ``fetched`` records an incomplete pagination
    (Requirement 1.4).
    """

    platform: str
    slug: str
    status: str  # ok | empty | http_error | exception | blocked | invalid_slug
    http_status: Optional[int] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None  # truncated to 300 chars
    pages: int = 0
    reported_total: Optional[int] = None
    fetched: int = 0
    truncated: bool = False
    host: Optional[str] = None  # resolved host (iCIMS, Workday)
    elapsed_s: float = 0.0

    def __post_init__(self) -> None:
        # Keep error messages bounded so one stack trace cannot bloat the report.
        if self.error_message is not None and len(self.error_message) > _ERROR_MESSAGE_CAP:
            self.error_message = self.error_message[:_ERROR_MESSAGE_CAP]

    def legacy_status_code(self) -> Optional[int]:
        """Return the HTTP status code the legacy 3-tuple wrapper expects.

        The old ``fetch_company_jobs_<platform>(slug)`` contract returned
        ``(slug, jobs, status_code)``. When no HTTP status is available (an
        exception, an invalid slug, or a success with no captured code), this
        returns ``None``, matching the old bare-``except`` behavior.
        """
        return self.http_status


class DiagnosticsCollector:
    """Thread-safe aggregator for one Scraper run.

    Fetchers (across a ThreadPool) call :meth:`record_outcome`; the Filter_Chain
    calls :meth:`record_decision` for each Job_Record. :meth:`to_report` renders
    the Diagnostics_Report dict, :meth:`write` persists it, and
    :meth:`print_summary` prints a per-Platform table to stdout (Requirement
    1.8).
    """

    def __init__(self, watchlist=None):
        # Slugs/companies whose per-job decisions are written out (Requirement 1.7).
        self.watchlist = {str(w).lower() for w in (watchlist or set())}
        self._lock = threading.Lock()

        # Per-platform fetch aggregates.
        self._attempted: Counter = Counter()
        self._skipped_dead: Counter = Counter()
        self._invalid_slug: Counter = Counter()
        self._status_counts: Dict[str, Counter] = defaultdict(Counter)
        self._http_status_counts: Dict[str, Counter] = defaultdict(Counter)
        self._exception_types: Dict[str, Counter] = defaultdict(Counter)
        self._truncated_companies: Counter = Counter()
        self._jobs_fetched: Counter = Counter()
        self._elapsed_s: Dict[str, float] = defaultdict(float)
        self._not_fetched_time_budget: Counter = Counter()

        # Per-platform Filter_Chain decision aggregates.
        self._match_type: Dict[str, Counter] = defaultdict(Counter)
        self._match_reason: Dict[str, Counter] = defaultdict(Counter)
        self._exclusion_reason: Dict[str, Counter] = defaultdict(Counter)
        self._category: Dict[str, Counter] = defaultdict(Counter)
        self._non_target_industry_detail_failed: Counter = Counter()

        # Per-platform, per-company fetched/decision tallies for top_companies.
        # platform -> slug -> {"fetched", "primary", "maybe", "excluded"}
        self._company_counts: Dict[str, Dict[str, Counter]] = defaultdict(
            lambda: defaultdict(Counter)
        )

        # Detail enrichment aggregates.
        self._detail_requested: Counter = Counter()
        self._detail_failed: Counter = Counter()
        self._detail_skipped: Counter = Counter()

        # Cap truncation counts per bucket (primary/maybe).
        self._cap_truncated: Counter = Counter()

        # Per-job Watchlist rows (Requirement 1.7).
        self._watchlist_rows: Dict[str, List[dict]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Fetch recording
    # ------------------------------------------------------------------
    def record_outcome(self, outcome: FetchOutcome) -> None:
        """Record one company's Fetch_Outcome (Requirements 1.1, 1.3, 1.4)."""
        platform = outcome.platform
        with self._lock:
            self._attempted[platform] += 1
            status = outcome.status if outcome.status in FETCH_STATUSES else "exception"

            if status == "invalid_slug":
                self._invalid_slug[platform] += 1
            self._status_counts[platform][status] += 1

            if outcome.http_status is not None:
                self._http_status_counts[platform][str(outcome.http_status)] += 1
            if status == "exception" and outcome.error_type:
                self._exception_types[platform][outcome.error_type] += 1
            if outcome.truncated:
                self._truncated_companies[platform] += 1

            self._jobs_fetched[platform] += outcome.fetched
            self._elapsed_s[platform] += outcome.elapsed_s
            # Seed the per-company fetched count so a company with jobs always
            # appears in top_companies even before decisions are recorded.
            self._company_counts[platform][outcome.slug]["fetched"] += outcome.fetched

    def record_skipped_dead(self, platform: str, count: int) -> None:
        """Record companies skipped because of the Dead_Slug_Cache."""
        with self._lock:
            self._skipped_dead[platform] += count

    def record_platform_time(self, platform: str, seconds: float, not_fetched: int) -> None:
        """Record a platform's elapsed time and tenants dropped by the time budget.

        ``not_fetched`` is the number of companies cancelled when
        ``PLATFORM_TIME_BUDGET_MINUTES`` was exceeded (Requirement 15.7/15.8).
        """
        with self._lock:
            self._elapsed_s[platform] += seconds
            self._not_fetched_time_budget[platform] += not_fetched

    # ------------------------------------------------------------------
    # Decision recording
    # ------------------------------------------------------------------
    def record_decision(self, job: dict) -> None:
        """Record one Job_Record's Filter_Chain decision (Requirements 1.5, 1.7).

        Reads ``match_type``, ``match_reason``, ``exclusion_reason``, and
        ``category`` off the job dict. The platform comes from ``ats`` and the
        company from ``company_slug`` (falling back to ``company``).
        """
        platform = job.get("ats") or "unknown"
        slug = job.get("company_slug") or job.get("company") or ""
        match_type = job.get("match_type")
        match_reason = job.get("match_reason")
        exclusion_reason = job.get("exclusion_reason")
        category = job.get("category")

        with self._lock:
            if match_type:
                self._match_type[platform][match_type] += 1
            if match_reason:
                self._match_reason[platform][match_reason] += 1

            if match_type == "excluded":
                if exclusion_reason:
                    self._exclusion_reason[platform][exclusion_reason] += 1
                    # A non_target_industry exclusion caused by a failed detail
                    # fetch (empty description) is tallied separately so the
                    # detail-failure share is visible (Requirement 1.9).
                    if exclusion_reason == "non_target_industry" and job.get(
                        "detail_failed"
                    ):
                        self._non_target_industry_detail_failed[platform] += 1
                self._company_counts[platform][slug]["excluded"] += 1
            else:
                # Kept Job_Records (primary / maybe) carry exactly one Category.
                if category:
                    self._category[platform][category] += 1
                if match_type in ("primary", "maybe"):
                    self._company_counts[platform][slug][match_type] += 1

            self._record_watchlist_row(slug, job)

    def _record_watchlist_row(self, slug: str, job: dict) -> None:
        """Append a per-job row for a Watchlist company. Caller holds the lock."""
        company = str(job.get("company") or "").lower()
        slug_l = str(slug).lower()
        if slug_l in self.watchlist or company in self.watchlist:
            key = slug or job.get("company") or ""
            self._watchlist_rows[key].append(
                {
                    "title": job.get("title"),
                    "location": job.get("location"),
                    "workplace_type": job.get("workplace_type"),
                    "match_type": job.get("match_type"),
                    "match_reason": job.get("match_reason"),
                    "exclusion_reason": job.get("exclusion_reason"),
                }
            )

    # ------------------------------------------------------------------
    # Detail enrichment recording
    # ------------------------------------------------------------------
    def record_detail_requested(self, platform: str, count: int = 1) -> None:
        """Record detail requests issued for a platform."""
        with self._lock:
            self._detail_requested[platform] += count

    def record_detail_failure(self, platform: str, slug: str, error: str) -> None:
        """Record a failed detail fetch. The job is still kept (Requirement 10.6)."""
        with self._lock:
            self._detail_failed[platform] += 1

    def record_detail_skipped(self, platform: str, count: int) -> None:
        """Record detail fetches skipped by the per-run cap (Requirement 10.7)."""
        with self._lock:
            self._detail_skipped[platform] += count

    def record_cap_truncation(self, bucket: str, count: int) -> None:
        """Record Job_Records dropped by a per-bucket cap (Requirement 7.16)."""
        with self._lock:
            self._cap_truncated[bucket] += count

    # ------------------------------------------------------------------
    # Report rendering
    # ------------------------------------------------------------------
    def _platforms(self) -> List[str]:
        names = set(self._attempted)
        names.update(self._match_type)
        names.update(self._exclusion_reason)
        names.update(self._category)
        names.update(self._company_counts)
        return sorted(names)

    def _top_companies(self, platform: str) -> List[dict]:
        companies = self._company_counts.get(platform, {})
        rows = [
            {
                "slug": slug,
                "fetched": counts.get("fetched", 0),
                "primary": counts.get("primary", 0),
                "maybe": counts.get("maybe", 0),
                "excluded": counts.get("excluded", 0),
            }
            for slug, counts in companies.items()
        ]
        # Largest fetched first; stable tie-break on slug for determinism.
        rows.sort(key=lambda r: (-r["fetched"], r["slug"]))
        return rows[:TOP_COMPANIES_LIMIT]

    def to_report(self, scoped: bool = False, config: Optional[dict] = None) -> dict:
        """Render the Diagnostics_Report dict (Requirements 1.1, 1.5, 1.6, 1.7)."""
        with self._lock:
            platforms = {}
            for platform in self._platforms():
                platforms[platform] = {
                    "companies_attempted": self._attempted.get(platform, 0),
                    "skipped_dead": self._skipped_dead.get(platform, 0),
                    "invalid_slug": self._invalid_slug.get(platform, 0),
                    "status_counts": dict(self._status_counts.get(platform, {})),
                    "http_status_counts": dict(
                        self._http_status_counts.get(platform, {})
                    ),
                    "exception_types": dict(self._exception_types.get(platform, {})),
                    "truncated_companies": self._truncated_companies.get(platform, 0),
                    "jobs_fetched": self._jobs_fetched.get(platform, 0),
                    "elapsed_s": round(self._elapsed_s.get(platform, 0.0), 1),
                    "not_fetched_time_budget": self._not_fetched_time_budget.get(
                        platform, 0
                    ),
                    "decisions": {
                        "match_type": dict(self._match_type.get(platform, {})),
                        "match_reason": dict(self._match_reason.get(platform, {})),
                        "exclusion_reason": dict(
                            self._exclusion_reason.get(platform, {})
                        ),
                        "category": dict(self._category.get(platform, {})),
                        "non_target_industry_detail_failed": self._non_target_industry_detail_failed.get(
                            platform, 0
                        ),
                    },
                    "detail": {
                        "requested": self._detail_requested.get(platform, 0),
                        "failed": self._detail_failed.get(platform, 0),
                        "skipped_cap": self._detail_skipped.get(platform, 0),
                    },
                    "top_companies": self._top_companies(platform),
                }

            report = {
                "generated_at": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "scoped": scoped,
                "jd_backend": os.environ.get("JD_BACKEND", "keyword"),
                "config": config or {},
                "platforms": platforms,
                "caps": {
                    "primary_truncated": self._cap_truncated.get("primary", 0),
                    "maybe_truncated": self._cap_truncated.get("maybe", 0),
                },
                "watchlist": {
                    key: list(rows) for key, rows in self._watchlist_rows.items()
                },
            }
            return report

    def write(self, path: str, scoped: bool = False, config: Optional[dict] = None) -> None:
        """Write the Diagnostics_Report to ``path`` as pretty JSON."""
        report = self.to_report(scoped=scoped, config=config)
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, sort_keys=True)

    def print_summary(self, scoped: bool = False, config: Optional[dict] = None) -> None:
        """Print a fixed-width per-Platform summary table to stdout (Requirement 1.8)."""
        report = self.to_report(scoped=scoped, config=config)
        platforms = report["platforms"]

        header = (
            f"{'Platform':<12} {'attempt':>7} {'ok':>6} {'empty':>6} "
            f"{'httperr':>7} {'exc':>5} {'blockd':>6} {'invalid':>7} "
            f"{'fetched':>8} {'primary':>7} {'maybe':>6} {'exclud':>8}"
        )
        print("\nDiagnostics summary" + (" (scoped run)" if scoped else ""))
        print(header)
        print("-" * len(header))

        for name in sorted(platforms):
            p = platforms[name]
            sc = p["status_counts"]
            mt = p["decisions"]["match_type"]
            print(
                f"{name:<12} {p['companies_attempted']:>7} "
                f"{sc.get('ok', 0):>6} {sc.get('empty', 0):>6} "
                f"{sc.get('http_error', 0):>7} {sc.get('exception', 0):>5} "
                f"{sc.get('blocked', 0):>6} {p['invalid_slug']:>7} "
                f"{p['jobs_fetched']:>8} {mt.get('primary', 0):>7} "
                f"{mt.get('maybe', 0):>6} {mt.get('excluded', 0):>8}"
            )

        caps = report["caps"]
        if caps["primary_truncated"] or caps["maybe_truncated"]:
            print(
                f"\nCaps: primary_truncated={caps['primary_truncated']} "
                f"maybe_truncated={caps['maybe_truncated']}"
            )


@dataclass
class RunContext:
    """The per-run bundle threaded through the fetchers and Filter_Chain.

    ``diag`` is the shared :class:`DiagnosticsCollector`, ``config`` is the
    ``personal_config`` module (or a stand-in in tests), and ``scoped`` is true
    for Company_Harness or ``--platforms/--slugs/--max-companies`` runs, which
    must leave the Dead_Slug_Cache untouched (Requirement 2.7).

    ``max_companies`` and ``slugs`` carry the scoped-run filters that
    :func:`scraper.fetch_all_jobs` applies per platform (Requirements 2.5, 2.6).
    ``max_companies`` of ``None`` means no per-platform cap; ``slugs`` of
    ``None`` (or empty) means no slug restriction.
    """

    diag: DiagnosticsCollector
    config: object = None
    scoped: bool = False
    max_companies: Optional[int] = None
    slugs: Optional[frozenset] = None


def default_context() -> RunContext:
    """Build a RunContext for the legacy 3-tuple wrappers.

    The legacy ``fetch_company_jobs_<platform>(slug)`` wrappers have no context
    of their own, so they use a fresh, throwaway collector and the
    ``personal_config`` module when it is importable.
    """
    config = None
    try:  # personal_config lives alongside this module in scripts/.
        import personal_config as config  # type: ignore
    except Exception:
        config = None
    watchlist = set()
    if config is not None:
        watchlist = getattr(config, "WATCHLIST", set()) or set()
    return RunContext(diag=DiagnosticsCollector(watchlist=watchlist), config=config, scoped=True)
