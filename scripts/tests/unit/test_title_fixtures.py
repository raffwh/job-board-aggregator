"""Wanted / junk title fixture tests for the Title_Matcher.

Every title in ``scripts/tests/fixtures/titles_wanted.txt`` must be kept
(``primary`` or ``maybe`` -- never ``excluded`` at the title level) and
every title in ``scripts/tests/fixtures/titles_junk.txt`` must be
excluded at the title level (``senior_excluded``, ``title_excluded`` or
``title_no_match``).

Classification is driven through the real Title_Matcher
(``TitleMatcher.classify`` and ``TitleMatcher.priority``) built from the
project configuration via ``build_title_matcher``. The senior -> maybe
mapping is checked through ``finalize_decision`` with a kept location
verdict, so "Senior Data Scientist" / "Senior Data Analyst" resolve to
Match_Type ``maybe`` with Match_Reason ``tier_senior_signal``.

The fixture files are pipe-separated; comment (``#``) and blank lines are
ignored. Columns left blank only assert the title is kept (wanted) or
excluded (junk); populated columns pin the senior and priority cases.

Validates: Requirements 7.19, 7.20, 18.8, 18.9, 18.10
"""

from pathlib import Path

import pytest

import filters
import scraper


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures"
_WANTED_PATH = _FIXTURE_DIR / "titles_wanted.txt"
_JUNK_PATH = _FIXTURE_DIR / "titles_junk.txt"


def _rows(path):
    """Yield the pipe-separated, comment-stripped rows of a fixture file."""
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        rows.append([col.strip() for col in line.split("|")])
    return rows


def _load_wanted():
    cases = []
    for cols in _rows(_WANTED_PATH):
        # title | expected_match | expected_reason | expected_priority
        cols = (cols + ["", "", "", ""])[:4]
        title, match, reason, priority = cols
        cases.append(
            {
                "title": title,
                "match": match or None,
                "reason": reason or None,
                "priority": int(priority) if priority else None,
            }
        )
    return cases


def _load_junk():
    cases = []
    for cols in _rows(_JUNK_PATH):
        # title | expected_exclusion_reason
        cols = (cols + ["", ""])[:2]
        title, reason = cols
        cases.append({"title": title, "reason": reason or None})
    return cases


_WANTED_CASES = _load_wanted()
_JUNK_CASES = _load_junk()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Title-level classifications that mean "excluded" (Requirement 7.20).
# ``TitleMatcher.classify`` returns ``no_match`` where the filter chain
# records the Exclusion_Reason ``title_no_match``.
_CLASS_TO_EXCLUSION = {
    "senior_excluded": "senior_excluded",
    "title_excluded": "title_excluded",
    "no_match": "title_no_match",
}

# Kept title-level classifications (Requirement 7.19).
_KEPT_CLASSES = {"primary", "maybe", "generic_analyst"}

# A location verdict that keeps the job, so ``finalize_decision`` is
# driven purely by the title / tier decision rows (no location maybe).
_KEPT_LOCATION = {
    "kept": True,
    "secondary_region": False,
    "remote_scope": "us",
    "location_status": "domestic",
}


@pytest.fixture(scope="module")
def matcher():
    return filters.build_title_matcher()


def _decision(matcher, title):
    """Run a wanted title through the kept-location decision table.

    ``classify_pre_location`` guarantees ``title_class`` is one of
    ``primary`` / ``maybe`` / ``generic_analyst`` for a kept title, and
    ``tier`` is the value ``job_tier_classification`` returns, so a
    senior primary title takes the ``tier_senior_signal`` row.
    """
    title_class = matcher.classify(title)
    tier = scraper.job_tier_classification(title)
    return filters.finalize_decision(
        {"title": title},
        title_class=title_class,
        tier=tier,
        loc_result=_KEPT_LOCATION,
        matcher=matcher,
    )


# ---------------------------------------------------------------------------
# Wanted titles (Requirements 7.19, 18.8, 18.9, 18.10)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case", _WANTED_CASES, ids=[c["title"] for c in _WANTED_CASES]
)
def test_wanted_title_is_kept(case, matcher):
    """Every wanted title classifies as a kept title, never excluded."""
    title = case["title"]
    title_class = matcher.classify(title)

    assert title_class in _KEPT_CLASSES, (
        f"{title!r} classified as {title_class!r}; expected a kept title "
        f"(one of {sorted(_KEPT_CLASSES)})"
    )
    assert title_class not in _CLASS_TO_EXCLUSION, (
        f"{title!r} was excluded at the title level ({title_class!r})"
    )

    decision = _decision(matcher, title)
    assert decision.match_type in ("primary", "maybe")
    assert decision.exclusion_reason is None


@pytest.mark.parametrize(
    "case",
    [c for c in _WANTED_CASES if c["match"]],
    ids=[c["title"] for c in _WANTED_CASES if c["match"]],
)
def test_wanted_title_pinned_match_and_reason(case, matcher):
    """Pinned rows get the exact Match_Type and Match_Reason.

    Includes "Senior Data Scientist" and "Senior Data Analyst" ->
    ``maybe`` / ``tier_senior_signal`` (Requirements 18.8-18.10).
    """
    decision = _decision(matcher, case["title"])

    assert decision.match_type == case["match"], (
        f"{case['title']!r}: match_type {decision.match_type!r} "
        f"!= expected {case['match']!r}"
    )
    if case["reason"] is not None:
        assert decision.match_reason == case["reason"], (
            f"{case['title']!r}: match_reason {decision.match_reason!r} "
            f"!= expected {case['reason']!r}"
        )


@pytest.mark.parametrize(
    "case",
    [c for c in _WANTED_CASES if c["priority"] is not None],
    ids=[c["title"] for c in _WANTED_CASES if c["priority"] is not None],
)
def test_wanted_title_priority(case, matcher):
    """Pinned rows get the expected Title_Priority (Requirements 18.8, 18.9)."""
    priority = matcher.priority(case["title"])
    assert priority == case["priority"], (
        f"{case['title']!r}: priority {priority} != expected {case['priority']}"
    )
    # Priority also rides on the kept Decision for a kept location.
    assert _decision(matcher, case["title"]).title_priority == case["priority"]


def test_senior_data_titles_map_to_maybe(matcher):
    """"Senior Data Scientist" / "Senior Data Analyst" -> maybe/tier_senior_signal.

    This is the explicit senior-signal row of Requirement 18.10, driven
    through ``finalize_decision`` with a kept location verdict.
    """
    for title, priority in (
        ("Senior Data Scientist", 1),
        ("Senior Data Analyst", 2),
    ):
        decision = _decision(matcher, title)
        assert decision.match_type == "maybe", title
        assert decision.match_reason == "tier_senior_signal", title
        assert decision.title_priority == priority, title


# ---------------------------------------------------------------------------
# Explicit Title_Priority lists (Requirements 18.8, 18.9)
# ---------------------------------------------------------------------------

_PRIORITY_1_TITLES = [
    "Associate Data Scientist",
    "RWE Data Scientist",
    "Real World Evidence Scientist",
    "Decision Scientist",
    "Applied Scientist, Machine Learning",
]

_PRIORITY_2_TITLES = [
    "Data Analyst",
    "Biostatistician II",
    "HEOR Analyst",
    "Analytics Engineer",
]


@pytest.mark.parametrize("title", _PRIORITY_1_TITLES)
def test_data_scientist_titles_are_priority_1(title, matcher):
    """Data_Scientist_Titles have Title_Priority 1 (Requirement 18.8)."""
    assert matcher.priority(title) == 1


@pytest.mark.parametrize("title", _PRIORITY_2_TITLES)
def test_other_kept_titles_are_priority_2(title, matcher):
    """Other kept titles have Title_Priority 2 (Requirement 18.9)."""
    assert matcher.priority(title) == 2


# ---------------------------------------------------------------------------
# Junk titles (Requirement 7.20)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case", _JUNK_CASES, ids=[c["title"] for c in _JUNK_CASES]
)
def test_junk_title_is_excluded(case, matcher):
    """Every junk title is excluded at the title level.

    ``TitleMatcher.classify`` returns ``senior_excluded``,
    ``title_excluded`` or ``no_match`` (recorded as ``title_no_match``).
    A populated fixture column pins the exact Exclusion_Reason.
    """
    title = case["title"]
    title_class = matcher.classify(title)

    assert title_class in _CLASS_TO_EXCLUSION, (
        f"{title!r} classified as {title_class!r}; expected an excluded "
        f"title (one of {sorted(_CLASS_TO_EXCLUSION)})"
    )

    if case["reason"] is not None:
        actual = _CLASS_TO_EXCLUSION[title_class]
        assert actual == case["reason"], (
            f"{title!r}: exclusion_reason {actual!r} != expected "
            f"{case['reason']!r}"
        )
