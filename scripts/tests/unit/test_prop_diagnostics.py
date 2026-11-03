"""Property-based test for diagnostics count conservation.

Property 1: Diagnostics counts are conserved.

For any sequence of FetchOutcomes and Filter_Chain decisions recorded into a
fresh DiagnosticsCollector, the rendered report must conserve its counts:

- per-platform status counts sum to companies attempted for that platform;
- per-Match_Type counts sum to the number of recorded decisions for that
  platform;
- per-Match_Reason counts sum to the number of recorded decisions that carried
  a Match_Reason;
- per-Category counts sum to the number of kept (primary/maybe) decisions;
- per-Exclusion_Reason counts sum to the number of excluded decisions;
- the ``non_target_industry_detail_failed`` tally is at most the
  ``non_target_industry`` exclusion count;
- ``top_companies`` has at most 20 entries, ordered by fetched count
  descending, and no omitted company has a larger fetched count than the
  smallest listed one.

Validates: Requirements 1.1, 1.5, 1.6, 1.9
"""

from collections import Counter

from hypothesis import given, settings
from hypothesis import strategies as st

from diagnostics import (
    CATEGORIES,
    FETCH_STATUSES,
    TOP_COMPANIES_LIMIT,
    DiagnosticsCollector,
    FetchOutcome,
)

PLATFORMS = ["workday", "icims", "ashby", "lever", "greenhouse", "bamboohr"]
MATCH_REASONS = [
    "primary_title",
    "tier_senior_signal",
    "title_maybe",
    "title_generic_analyst",
    "location_unknown",
    "location_new_england",
    "remote_unspecified",
]
EXCLUSION_REASONS = [
    "non_target_industry",
    "phd_required",
    "senior",
    "recruiter",
    "title_exclude",
    "blocklist",
    "outside_radius",
    "non_us",
]


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# A fetch status: mostly valid statuses, but occasionally an unknown string so
# we exercise the collector's coercion of unknown statuses to "exception".
fetch_status = st.sampled_from(list(FETCH_STATUSES) + ["weird", "timeout", "???"])


@st.composite
def fetch_outcomes(draw):
    """A FetchOutcome for a platform/slug, with a possibly-unknown status."""
    platform = draw(st.sampled_from(PLATFORMS))
    slug = draw(st.sampled_from(["a", "b", "c", "d", "e"]))
    status = draw(fetch_status)
    fetched = draw(st.integers(min_value=0, max_value=50))
    truncated = draw(st.booleans())
    return FetchOutcome(
        platform=platform,
        slug=slug,
        status=status,
        fetched=fetched,
        truncated=truncated,
        reported_total=fetched + (5 if truncated else 0),
    )


@st.composite
def decisions(draw):
    """A Job_Record decision dict as record_decision reads it.

    Kept decisions (primary/maybe) carry a Category; excluded decisions carry an
    Exclusion_Reason. Every decision may carry a Match_Reason. Some
    non_target_industry exclusions carry ``detail_failed`` so the detail-failure
    tally is exercised.
    """
    platform = draw(st.sampled_from(PLATFORMS))
    slug = draw(st.sampled_from(["a", "b", "c", "d", "e"]))
    match_type = draw(st.sampled_from(["primary", "maybe", "excluded"]))
    # A Match_Reason may be present or absent for any decision.
    match_reason = draw(st.one_of(st.none(), st.sampled_from(MATCH_REASONS)))

    job = {
        "ats": platform,
        "company_slug": slug,
        "match_type": match_type,
        "match_reason": match_reason,
    }
    if match_type == "excluded":
        job["exclusion_reason"] = draw(st.sampled_from(EXCLUSION_REASONS))
        if job["exclusion_reason"] == "non_target_industry":
            job["detail_failed"] = draw(st.booleans())
    else:
        job["category"] = draw(st.sampled_from(list(CATEGORIES)))
    return job


# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------


@settings(max_examples=300)
@given(
    outcomes=st.lists(fetch_outcomes(), max_size=60),
    decision_list=st.lists(decisions(), max_size=120),
)
def test_diagnostics_counts_are_conserved(outcomes, decision_list):
    collector = DiagnosticsCollector()

    for outcome in outcomes:
        collector.record_outcome(outcome)
    for job in decision_list:
        collector.record_decision(job)

    report = collector.to_report()
    platforms = report["platforms"]

    # --- Independent reference tallies, per platform --------------------
    attempted = Counter()
    for outcome in outcomes:
        attempted[outcome.platform] += 1

    n_decisions = Counter()
    n_with_reason = Counter()
    n_kept = Counter()
    n_excluded = Counter()
    n_non_target = Counter()
    n_non_target_detail_failed = Counter()
    for job in decision_list:
        p = job["ats"]
        n_decisions[p] += 1
        if job.get("match_reason"):
            n_with_reason[p] += 1
        if job["match_type"] == "excluded":
            n_excluded[p] += 1
            if job.get("exclusion_reason") == "non_target_industry":
                n_non_target[p] += 1
                if job.get("detail_failed"):
                    n_non_target_detail_failed[p] += 1
        else:
            n_kept[p] += 1

    # --- Assertions per platform in the report --------------------------
    for platform, data in platforms.items():
        # 1.1: status counts sum to companies attempted.
        status_counts = data["status_counts"]
        assert sum(status_counts.values()) == data["companies_attempted"]
        assert data["companies_attempted"] == attempted[platform]

        decisions_block = data["decisions"]

        # 1.5: Match_Type counts sum to all recorded decisions.
        assert sum(decisions_block["match_type"].values()) == n_decisions[platform]

        # Match_Reason counts sum to the decisions that carried a reason.
        assert sum(decisions_block["match_reason"].values()) == n_with_reason[platform]

        # 1.5: Category counts sum to the kept decisions.
        assert sum(decisions_block["category"].values()) == n_kept[platform]

        # 1.5: Exclusion_Reason counts sum to the excluded decisions.
        assert (
            sum(decisions_block["exclusion_reason"].values()) == n_excluded[platform]
        )

        # 1.9: detail-failed tally is at most the non_target_industry count.
        detail_failed = decisions_block["non_target_industry_detail_failed"]
        non_target = decisions_block["exclusion_reason"].get("non_target_industry", 0)
        assert detail_failed <= non_target
        assert detail_failed == n_non_target_detail_failed[platform]
        assert non_target == n_non_target[platform]

        # 1.6: top_companies is well formed and ordered.
        top = data["top_companies"]
        assert len(top) <= TOP_COMPANIES_LIMIT
        fetched_seq = [row["fetched"] for row in top]
        assert fetched_seq == sorted(fetched_seq, reverse=True)

    # 1.6: no omitted company has a larger fetched count than the smallest
    # listed one. Rebuild the full per-company fetched tally from the inputs.
    fetched_by_company = {}
    for outcome in outcomes:
        key = (outcome.platform, outcome.slug)
        fetched_by_company[key] = fetched_by_company.get(key, 0) + outcome.fetched

    for platform, data in platforms.items():
        top = data["top_companies"]
        if len(top) < TOP_COMPANIES_LIMIT:
            continue  # nothing omitted, nothing to check
        listed_slugs = {row["slug"] for row in top}
        smallest_listed = min(row["fetched"] for row in top)
        for (p, slug), fetched in fetched_by_company.items():
            if p != platform or slug in listed_slugs:
                continue
            assert fetched <= smallest_listed
