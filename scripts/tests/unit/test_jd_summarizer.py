"""Unit tests for the keyword JD backend and backend selection.

Three families of checks, all offline:

- Fixture JDs (``scripts/tests/fixtures/jds/*.json``): the Keyword_Backend
  returns every ``expected_skills`` entry and none of the ``must_not_match``
  entries for each fixture. This covers the HEOR/claims JD (ICD-10, CPT,
  MarketScan ...), the biostatistics JD (SAS, GEE, Kaplan-Meier, Cox ...),
  and the ordinary-words JD where "epic"/"clarity" must NOT become
  Epic/Clarity (Requirement 11.10).
- Skills_Dictionary coverage: every skill group named in Requirement 11.9 is
  present in ``SKILLS`` by canonical name (11.9). A pattern lint asserts no
  compiled pattern uses a negative lookahead/lookbehind, which keeps
  extraction monotonic under appended text (Requirement 11.8 support).
- Backend selection: ``JD_BACKEND`` unset/``keyword`` -> keyword (12.1);
  ``llm`` -> keyword fallback with a warning (12.2); an unknown value ->
  keyword with a warning (12.4). A no-network test patches ``socket.socket``
  to raise and clears ``JD_BACKEND`` to confirm the keyword path needs no
  network (12.6).

Validates: Requirements 11.9, 11.10, 12.1, 12.2, 12.4, 12.6
"""

from __future__ import annotations

import json
import logging
import socket
from pathlib import Path

import pytest

import jd_summarizer
from jd_skills import SKILLS, Skill
from jd_summarizer import KeywordBackend, get_backend, summarize_jd


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "jds"


def _load_fixtures():
    """Load every JD fixture as ``(name, data)`` sorted by file name."""
    cases = []
    for path in sorted(_FIXTURE_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        cases.append((path.stem, data))
    return cases


_FIXTURES = _load_fixtures()


@pytest.fixture(autouse=True)
def _reset_warning_guards():
    """Reset the once-per-run warning events so each test sees warnings.

    ``get_backend`` and ``summarize_jd`` guard their warnings behind
    module-level events so a real run logs at most one of each. Tests clear
    them before and after so warning assertions are independent of order.
    """
    jd_summarizer._warned_unknown_backend.clear()
    jd_summarizer._warned_fallback.clear()
    yield
    jd_summarizer._warned_unknown_backend.clear()
    jd_summarizer._warned_fallback.clear()


# ---------------------------------------------------------------------------
# Fixture JDs: expected skills present, must-not-match absent (Requirement 11.10)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name, data", _FIXTURES, ids=[c[0] for c in _FIXTURES])
def test_fixture_expected_skills_present(name, data):
    """Every ``expected_skills`` entry is returned for the fixture JD."""
    result = summarize_jd(data["title"], data["description"])
    skills = set(result["skills"])

    expected = data.get("expected_skills", [])
    missing = [s for s in expected if s not in skills]
    assert not missing, (
        f"{name}: missing expected skills {missing}; got {sorted(skills)}"
    )


@pytest.mark.parametrize("name, data", _FIXTURES, ids=[c[0] for c in _FIXTURES])
def test_fixture_must_not_match_absent(name, data):
    """No ``must_not_match`` entry is returned for the fixture JD.

    This is where "epic"/"clarity" as ordinary words must not surface
    Epic/Clarity, the HEOR/claims JD must not surface SAS/R/Cox, and the
    biostatistics JD must not surface MarketScan/Python/Go.
    """
    result = summarize_jd(data["title"], data["description"])
    skills = set(result["skills"])

    forbidden = data.get("must_not_match", [])
    leaked = [s for s in forbidden if s in skills]
    assert not leaked, (
        f"{name}: matched must-not-match skills {leaked}; got {sorted(skills)}"
    )


def test_keyword_backend_matches_summarize_jd_on_fixtures():
    """KeywordBackend.summarize agrees with the default summarize_jd path."""
    backend = KeywordBackend()
    for name, data in _FIXTURES:
        direct = backend.summarize(data["title"], data["description"])
        viajd = summarize_jd(data["title"], data["description"])
        assert direct["skills"] == viajd["skills"], name
        assert viajd["backend"] == "keyword", name


# ---------------------------------------------------------------------------
# Skills_Dictionary group coverage (Requirement 11.9)
# ---------------------------------------------------------------------------

# Canonical names grouped exactly as Requirement 11.9 enumerates them. The
# dictionary may carry more skills than these; the requirement only pins the
# minimum coverage per group.
_REQUIRED_GROUPS = {
    "languages and libraries": ["Python", "SQL", "R", "SAS"],
    "analytics and BI tools": ["Tableau", "Power BI", "Looker", "Excel"],
    "data platforms": ["Snowflake", "Databricks", "Spark", "dbt", "Airflow"],
    "cloud": ["AWS", "GCP", "Azure"],
    "ML": [
        "scikit-learn",
        "PyTorch",
        "TensorFlow",
        "Machine Learning",
        "NLP",
        "Deep Learning",
    ],
    "statistics": [
        "Regression",
        "A/B Testing",
        "Causal Inference",
        "Survival Analysis",
    ],
    "healthcare and RWE": [
        "Claims Data",
        "EHR/EMR",
        "OMOP",
        "HEOR",
        "Epidemiology",
        "CDISC",
        "FHIR",
    ],
}


@pytest.mark.parametrize("group, names", sorted(_REQUIRED_GROUPS.items()))
def test_skills_dictionary_covers_group(group, names):
    """Every Requirement 11.9 group is covered by canonical name in SKILLS."""
    canonical = {skill.name for skill in SKILLS}
    missing = [n for n in names if n not in canonical]
    assert not missing, f"group {group!r}: SKILLS is missing {missing}"


# ---------------------------------------------------------------------------
# Pattern lint: no negative lookaround (Requirement 11.8 support)
# ---------------------------------------------------------------------------

# Negative lookahead ``(?!`` or negative lookbehind ``(?<!`` would let an
# appended-text match disappear, breaking monotonicity. Positive lookaround
# (``(?=`` / ``(?<=``) is allowed and used for short-skill context.
_NEGATIVE_LOOKAROUND = ("(?!", "(?<!")


def test_no_skill_pattern_uses_negative_lookaround():
    """No Skill pattern source uses a negative lookahead/lookbehind."""
    offenders = []
    for skill in SKILLS:
        assert isinstance(skill, Skill)
        for source in skill.patterns:
            if any(token in source for token in _NEGATIVE_LOOKAROUND):
                offenders.append((skill.name, source))
    assert not offenders, f"patterns with negative lookaround: {offenders}"


# ---------------------------------------------------------------------------
# Backend selection (Requirements 12.1, 12.2, 12.4)
# ---------------------------------------------------------------------------


def test_backend_default_is_keyword_when_unset(monkeypatch):
    """JD_BACKEND unset selects the Keyword_Backend (Requirement 12.1)."""
    monkeypatch.delenv("JD_BACKEND", raising=False)
    assert get_backend().name == "keyword"


def test_backend_keyword_value_selects_keyword(monkeypatch):
    """JD_BACKEND=keyword selects the Keyword_Backend (Requirement 12.1)."""
    monkeypatch.setenv("JD_BACKEND", "keyword")
    assert get_backend().name == "keyword"
    # Casing and whitespace are normalized.
    monkeypatch.setenv("JD_BACKEND", "  KEYWORD  ")
    assert get_backend().name == "keyword"


def test_llm_backend_falls_back_to_keyword_with_warning(monkeypatch, caplog):
    """JD_BACKEND=llm falls back to keyword and warns once (Requirement 12.2)."""
    monkeypatch.setenv("JD_BACKEND", "llm")
    with caplog.at_level(logging.WARNING, logger="jd_summarizer"):
        result = summarize_jd("Data Scientist", "We use Python and SQL.")

    assert result["backend"] == "keyword"
    assert "Python" in result["skills"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "llm" in warnings[0].getMessage()


def test_unknown_backend_uses_keyword_with_warning(monkeypatch, caplog):
    """An unknown JD_BACKEND warns naming the value and uses keyword (12.4)."""
    monkeypatch.setenv("JD_BACKEND", "banana")
    with caplog.at_level(logging.WARNING, logger="jd_summarizer"):
        backend = get_backend()

    assert backend.name == "keyword"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "banana" in warnings[0].getMessage()


def test_unknown_backend_warns_only_once_per_run(monkeypatch, caplog):
    """The unknown-value warning is emitted at most once per run (12.4)."""
    monkeypatch.setenv("JD_BACKEND", "banana")
    with caplog.at_level(logging.WARNING, logger="jd_summarizer"):
        get_backend()
        get_backend()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


# ---------------------------------------------------------------------------
# No-network keyword path (Requirement 12.6)
# ---------------------------------------------------------------------------


def test_keyword_path_needs_no_network(monkeypatch):
    """summarize_jd works with sockets disabled and JD_BACKEND cleared (12.6).

    ``socket.socket`` is patched to raise on any construction attempt, so any
    network call in the keyword path would fail the test. JD_BACKEND is
    cleared so selection takes the default keyword branch.
    """
    monkeypatch.delenv("JD_BACKEND", raising=False)

    def _no_sockets(*args, **kwargs):
        raise AssertionError("keyword backend must not open a socket")

    monkeypatch.setattr(socket, "socket", _no_sockets)

    data = next(d for n, d in _FIXTURES if n == "heor_claims")
    result = summarize_jd(data["title"], data["description"])

    assert result["backend"] == "keyword"
    assert "HEOR" in result["skills"]
    assert "MarketScan" in result["skills"]
    assert set(result.keys()) == {"skills", "summary", "backend"}
