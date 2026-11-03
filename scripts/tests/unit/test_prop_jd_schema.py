"""Property-based test for the JD_Summarizer schema and keyword fallback.

Property 21: summarize_jd always returns the schema, with keyword fallback.

For any title and description (including ``None``, empty, whitespace-only,
Unicode, and long text), and any backend behavior (valid output, raising any
exception, or returning a non-schema value), ``summarize_jd`` returns a dict
with exactly the keys ``skills`` (a list of strings), ``summary`` (a string),
and ``backend`` (a string). A well-formed backend's output is passed through
with its own ``name``; whenever the selected backend fails or returns a value
that does not match the ``{skills, summary}`` schema, the result equals the
Keyword_Backend output with ``backend == "keyword"``.

Validates: Requirements 11.1, 11.6, 12.3
"""

from hypothesis import given, settings
from hypothesis import strategies as st

import jd_summarizer
from jd_summarizer import KeywordBackend, summarize_jd


# ---------------------------------------------------------------------------
# Input generators
# ---------------------------------------------------------------------------

# Title/description text: None, empty, whitespace-only, Unicode, and text up to
# a few thousand characters. Capped well below the 20k ceiling to keep the
# keyword scan fast while still covering the "long text" case.
_text = st.one_of(
    st.none(),
    st.just(""),
    st.just("   \n\t  "),
    st.text(max_size=400),
    st.text(
        alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x2FFF),
        max_size=800,
    ),
)


# ---------------------------------------------------------------------------
# Fake backends injected via the `backend` argument
# ---------------------------------------------------------------------------


class RaisingBackend:
    """A backend whose ``summarize`` raises an arbitrary exception."""

    name = "raiser"

    def __init__(self, exc):
        self._exc = exc

    def summarize(self, title, description):
        raise self._exc


class ConstantBackend:
    """A backend that returns a fixed (possibly malformed) value."""

    name = "constant"

    def __init__(self, value):
        self._value = value

    def summarize(self, title, description):
        return self._value


class WellFormedBackend:
    """A backend that returns a valid ``{skills, summary}`` schema."""

    name = "fake-llm"

    def __init__(self, skills, summary):
        self._skills = skills
        self._summary = summary

    def summarize(self, title, description):
        # Return fresh objects so pass-through identity is not relied upon.
        return {"skills": list(self._skills), "summary": self._summary}


# Exceptions a misbehaving backend might raise, including the stub's
# NotImplementedError and ordinary runtime failures.
_exceptions = st.sampled_from(
    [
        NotImplementedError("llm backend not configured"),
        ValueError("boom"),
        KeyError("missing"),
        RuntimeError("network down"),
        TypeError("bad arg"),
    ]
)

# Values that do NOT match the {skills, summary} schema: non-dicts, missing or
# extra keys, and wrong value types.
_bad_values = st.sampled_from(
    [
        None,
        "not a dict",
        42,
        ["skills", "summary"],
        {},
        {"skills": ["Python"]},  # missing summary
        {"summary": "text"},  # missing skills
        {"skills": ["Python"], "summary": "text", "extra": 1},  # extra key
        {"skills": "Python", "summary": "text"},  # skills not a list
        {"skills": [1, 2, 3], "summary": "text"},  # skills not list[str]
        {"skills": ["Python"], "summary": 5},  # summary not a str
        {"skills": ["Python", None], "summary": "text"},  # mixed skills
    ]
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_schema(result):
    """Assert ``result`` has exactly skills/summary/backend of correct types."""
    assert isinstance(result, dict)
    assert set(result.keys()) == {"skills", "summary", "backend"}
    assert isinstance(result["skills"], list)
    assert all(isinstance(s, str) for s in result["skills"])
    assert isinstance(result["summary"], str)
    assert isinstance(result["backend"], str)


def _keyword_result(title, description):
    """The Keyword_Backend output the fallback must equal, as a full record."""
    kw = KeywordBackend().summarize(title, description)
    return {"skills": kw["skills"], "summary": kw["summary"], "backend": "keyword"}


def _reset_warn_guards():
    """Clear the module's one-warning-per-run guards between examples so the
    fallback path is exercised identically regardless of call order."""
    jd_summarizer._warned_fallback.clear()
    jd_summarizer._warned_unknown_backend.clear()


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=300)
@given(title=_text, description=_text, exc=_exceptions)
def test_raising_backend_falls_back_to_keyword(title, description, exc):
    """A backend that raises any exception yields the keyword result with
    ``backend == "keyword"`` and still the full schema (Requirement 12.3)."""
    _reset_warn_guards()
    result = summarize_jd(title, description, backend=RaisingBackend(exc))
    _assert_schema(result)
    assert result["backend"] == "keyword"
    assert result == _keyword_result(title, description)


@settings(max_examples=300)
@given(title=_text, description=_text, value=_bad_values)
def test_bad_schema_backend_falls_back_to_keyword(title, description, value):
    """A backend returning a non-dict, or a dict with wrong/extra keys or wrong
    value types, falls back to the keyword result (Requirements 11.1, 12.3)."""
    _reset_warn_guards()
    result = summarize_jd(title, description, backend=ConstantBackend(value))
    _assert_schema(result)
    assert result["backend"] == "keyword"
    assert result == _keyword_result(title, description)


@settings(max_examples=300)
@given(
    title=_text,
    description=_text,
    skills=st.lists(st.text(max_size=20), max_size=6),
    summary=st.text(max_size=100),
)
def test_well_formed_backend_passes_through(title, description, skills, summary):
    """A well-formed backend's output is passed through unchanged with its own
    backend name (Requirements 11.1, 12.3)."""
    _reset_warn_guards()
    backend = WellFormedBackend(skills, summary)
    result = summarize_jd(title, description, backend=backend)
    _assert_schema(result)
    assert result == {
        "skills": skills,
        "summary": summary,
        "backend": "fake-llm",
    }


@settings(max_examples=300)
@given(title=_text, description=_text)
def test_result_always_has_valid_schema(title, description):
    """Regardless of inputs (including None/empty), the default-backend call
    always returns the full schema (Requirements 11.1, 11.6)."""
    _reset_warn_guards()
    result = summarize_jd(title, description)
    _assert_schema(result)
    # The default path uses the keyword backend when JD_BACKEND is unset.
    assert result["backend"] == "keyword"
    # Empty/missing description yields an empty summary (Requirement 11.6).
    if not (description or "").strip():
        assert result["summary"] == ""
