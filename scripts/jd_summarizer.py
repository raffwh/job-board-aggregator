# jd_summarizer.py
"""
JD skills and summary extraction behind a swappable backend.

This module exposes the JD_Summarizer interface the Scraper and the
Company_Harness call. A :class:`JDBackend` turns a ``(title, description)``
pair into ``{"skills": [...], "summary": str}``.

Backends (design Component 7):

- :class:`KeywordBackend` (implemented here) uses the Skills_Dictionary in
  ``jd_skills.py`` and needs no network, API keys, or secrets
  (Requirements 11.2-11.9, 12.6).
- :class:`LLMBackend` is a stub reserved for a later LLM integration. It
  raises ``NotImplementedError``, so selecting it falls back to the keyword
  backend with one warning per run (Requirements 12.2, 12.3).

Public entry points (design Component 7):

- :func:`get_backend` selects a backend by name, defaulting to the
  ``JD_BACKEND`` environment variable, then ``"keyword"`` (Requirements 12.1,
  12.2, 12.4).
- :func:`summarize_jd` always returns ``{"skills": [...], "summary": str,
  "backend": name}``, validating the chosen backend's output and falling back
  to the Keyword_Backend on error or a bad schema (Requirements 11.1, 12.3,
  12.5).

Validates: Requirements 11.1, 11.2, 11.3, 11.5, 11.6, 11.7, 11.8,
12.1, 12.2, 12.3, 12.4, 12.6
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Dict, List, Optional

from jd_skills import SKILLS, Skill

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Summary extraction constants
#
# Boilerplate heading lines are dropped whole. Preferred-section headings mark
# the line we would rather start the summary from. Both are matched after a
# line has been stripped of surrounding whitespace.
# ---------------------------------------------------------------------------

_BOILERPLATE_HEADING = re.compile(
    r"^(about (us|the (role|company|team))"
    r"|job description|summary|overview|responsibilities"
    r"|what you('ll| will) do|location|who we are)\s*:?\s*$",
    re.IGNORECASE,
)

# EEO, benefits, and compensation boilerplate lines (matched anywhere in line).
_BOILERPLATE_BODY = re.compile(
    r"(equal opportunity|eeo|accommodation|benefits"
    r"|401\(k\)|pay range|salary range)",
    re.IGNORECASE,
)

# Headings after which the following line is the preferred start of a summary.
_PREFERRED_HEADING = re.compile(
    r"^(summary|about the role|overview"
    r"|what you('ll| will) do|responsibilities)\s*:?\s*$",
    re.IGNORECASE,
)

# A leading "Summary:" or "Location: ..." prefix stripped from a kept line.
_LEADING_PREFIX = re.compile(r"^(summary|location)\s*:\s*", re.IGNORECASE)

# Sentence boundary: split after ., !, or ? followed by whitespace.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

_ELLIPSIS = "\u2026"  # single-character ellipsis "…"


class KeywordBackend:
    """Keyword JD backend over the Skills_Dictionary.

    Extraction is deterministic and monotonic under appended text: it relies
    only on the compiled patterns in :data:`jd_skills.SKILLS`, uses no sets for
    ordering, no randomness, and no time, and no pattern anchors on the end of
    the text (Requirements 11.7, 11.8).
    """

    name = "keyword"

    def __init__(self, skills: List[Skill] = SKILLS, max_summary_chars: int = 300):
        self.skills = skills
        self.max_summary_chars = max_summary_chars

    # -- public API --------------------------------------------------------

    def summarize(self, title: Optional[str], description: Optional[str]) -> Dict:
        """Return ``{"skills": [...], "summary": str}`` for one posting.

        ``None`` inputs are treated as empty strings (Requirement 11.1). Skills
        are scanned over ``title + "\\n" + description``; the summary is drawn
        from the description only, so an empty/missing description yields an
        empty summary while title-only skills still appear (Requirement 11.6).
        """
        title = title or ""
        description = description or ""

        skills = self._extract_skills(title, description)
        summary = self._build_summary(description)
        return {"skills": skills, "summary": summary}

    # -- skills ------------------------------------------------------------

    def _extract_skills(self, title: str, description: str) -> List[str]:
        """Canonical skill names, unique, ordered by first appearance.

        For each skill we take the earliest match position across its
        patterns. Matched skills are sorted by ``(position, dictionary_index)``
        so that ties at the same position fall back to Skills_Dictionary order
        (Requirements 11.2, 11.3).
        """
        text = title + "\n" + description

        matched = []  # (position, dictionary_index, name)
        for index, skill in enumerate(self.skills):
            position = self._earliest_match(skill, text)
            if position is not None:
                matched.append((position, index, skill.name))

        matched.sort(key=lambda item: (item[0], item[1]))

        # Canonical names, de-duplicated while preserving the sorted order.
        # Some canonical names (e.g. "GEE") appear under more than one Skill
        # entry; keep only the first occurrence.
        result: List[str] = []
        seen = set()
        for _position, _index, name in matched:
            if name not in seen:
                seen.add(name)
                result.append(name)
        return result

    @staticmethod
    def _earliest_match(skill: Skill, text: str) -> Optional[int]:
        """Earliest start position of any of the skill's patterns, or None."""
        earliest: Optional[int] = None
        for pattern in skill.compiled:
            match = pattern.search(text)
            if match is not None and (earliest is None or match.start() < earliest):
                earliest = match.start()
        return earliest

    # -- summary -----------------------------------------------------------

    def _build_summary(self, description: str) -> str:
        """Extractive summary of the description's leading sentences.

        Drops boilerplate heading and EEO/benefits lines, strips a leading
        ``Summary:``/``Location:`` prefix, prefers the line after a preferred
        heading, then accumulates whole sentences up to ``max_summary_chars``
        (Requirement 11.5). An empty description gives ``""``
        (Requirement 11.6).
        """
        if not description.strip():
            return ""

        start_line = self._select_start_line(description)
        if not start_line:
            return ""

        return self._truncate_sentences(start_line)

    def _select_start_line(self, description: str) -> str:
        """Pick the body line the summary should start from.

        Keeps non-boilerplate lines in order, remembering the first line that
        directly follows a preferred-section heading. Returns that preferred
        line when one exists, otherwise the first kept line.
        """
        preferred: Optional[str] = None
        first_kept: Optional[str] = None
        prev_was_preferred_heading = False

        for raw_line in description.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            if _BOILERPLATE_HEADING.match(line):
                prev_was_preferred_heading = bool(_PREFERRED_HEADING.match(line))
                continue

            if _BOILERPLATE_BODY.search(line):
                prev_was_preferred_heading = False
                continue

            line = _LEADING_PREFIX.sub("", line).strip()
            if not line:
                prev_was_preferred_heading = False
                continue

            if prev_was_preferred_heading and preferred is None:
                preferred = line
            if first_kept is None:
                first_kept = line
            prev_was_preferred_heading = False

        if preferred is not None:
            return preferred
        return first_kept or ""

    def _truncate_sentences(self, text: str) -> str:
        """Whole sentences up to the char cap; single long sentence is cut.

        Splits on sentence boundaries and accumulates whole sentences while the
        running length stays within ``max_summary_chars``. If even the first
        sentence is longer than the cap, cuts at the last space before the cap
        and appends ``…`` (Requirement 11.5).
        """
        cap = self.max_summary_chars
        sentences = _SENTENCE_SPLIT.split(text.strip())

        accumulated = ""
        for sentence in sentences:
            if not sentence:
                continue
            candidate = sentence if not accumulated else accumulated + " " + sentence
            if len(candidate) <= cap:
                accumulated = candidate
            else:
                break

        if accumulated:
            return accumulated

        # First sentence alone exceeds the cap: cut at the last space before
        # the limit and append a single-character ellipsis.
        first = sentences[0].strip() if sentences else text.strip()
        head = first[: cap - 1]
        cut = head.rfind(" ")
        if cut > 0:
            head = head[:cut]
        return head.rstrip() + _ELLIPSIS


# ---------------------------------------------------------------------------
# LLM backend stub
#
# The LLM backend is out of scope for this spec: only the interface and a stub
# are required. It raises ``NotImplementedError`` so that selecting ``llm`` via
# JD_BACKEND exercises the fallback path in :func:`summarize_jd` and lands on
# the Keyword_Backend with a single warning (Requirements 12.2, 12.3).
# ---------------------------------------------------------------------------


class LLMBackend:
    """Placeholder LLM backend reserved for a later LLM integration."""

    name = "llm"

    def summarize(self, title: Optional[str], description: Optional[str]) -> Dict:
        # Later: reads its own env key and calls an LLM API. For now the stub
        # always raises, so summarize_jd falls back to the keyword backend.
        raise NotImplementedError("LLM backend not configured")


# Backend registry: JD_BACKEND values map to backend classes (design
# Component 7). ``keyword`` is the default and only network-free backend.
BACKENDS = {"keyword": KeywordBackend, "llm": LLMBackend}

_DEFAULT_BACKEND = "keyword"

# One warning per run for an unrecognized JD_BACKEND value (Requirement 12.4)
# and one for a fallback to keyword after a backend error/bad schema
# (Requirement 12.3). Guarded by module-level events so repeated calls within a
# single run do not spam the log.
_warned_unknown_backend = threading.Event()
_warned_fallback = threading.Event()


def _valid_schema(result: object) -> bool:
    """True when ``result`` matches the backend contract ``{skills, summary}``.

    The contract is exactly two keys: ``skills`` (a list of ``str``) and
    ``summary`` (a ``str``). Any other shape is rejected so that a misbehaving
    backend triggers the keyword fallback in :func:`summarize_jd`
    (Requirements 11.1, 12.3).
    """
    if not isinstance(result, dict):
        return False
    if set(result.keys()) != {"skills", "summary"}:
        return False
    skills = result["skills"]
    summary = result["summary"]
    if not isinstance(skills, list) or not all(isinstance(s, str) for s in skills):
        return False
    if not isinstance(summary, str):
        return False
    return True


def get_backend(name: Optional[str] = None):
    """Return a JD backend instance for ``name``.

    When ``name`` is ``None`` the selector reads the ``JD_BACKEND`` environment
    variable; an unset/empty value defaults to ``keyword`` (Requirements 12.1,
    12.2). The name is lowercased and stripped. An unrecognized value logs one
    warning naming the value and falls back to the Keyword_Backend
    (Requirement 12.4).
    """
    if name is None:
        name = os.environ.get("JD_BACKEND")

    normalized = (name or "").strip().lower() or _DEFAULT_BACKEND

    backend_cls = BACKENDS.get(normalized)
    if backend_cls is None:
        if not _warned_unknown_backend.is_set():
            _warned_unknown_backend.set()
            logger.warning(
                "unknown JD_BACKEND %r, using %s", name, _DEFAULT_BACKEND
            )
        backend_cls = BACKENDS[_DEFAULT_BACKEND]

    return backend_cls()


def summarize_jd(
    title: Optional[str],
    description: Optional[str],
    backend=None,
) -> Dict:
    """Return ``{"skills": [...], "summary": str, "backend": name}`` for a JD.

    Always returns the full schema regardless of which backend runs
    (Requirement 11.1). ``None`` inputs are treated as empty strings. The
    selected backend (``backend`` argument, else :func:`get_backend`) is
    called; if the call raises (including the ``LLMBackend`` stub's
    ``NotImplementedError``) or returns output that fails :func:`_valid_schema`,
    the function falls back to the Keyword_Backend result with
    ``backend == "keyword"`` and logs one warning per run (Requirements 12.2,
    12.3). The producing backend's name is included so the Scraper can record
    it (Requirement 12.5).
    """
    title = title or ""
    description = description or ""

    if backend is None:
        backend = get_backend()

    backend_name = getattr(backend, "name", _DEFAULT_BACKEND)

    try:
        result = backend.summarize(title, description)
        if _valid_schema(result):
            return {
                "skills": result["skills"],
                "summary": result["summary"],
                "backend": backend_name,
            }
        reason = "returned output that does not match the schema"
    except NotImplementedError:
        reason = "is not implemented"
    except Exception as exc:  # noqa: BLE001 - any backend failure falls back
        reason = f"raised {type(exc).__name__}: {exc}"

    # Fall back to the keyword backend. Warn once per run.
    if not _warned_fallback.is_set():
        _warned_fallback.set()
        logger.warning(
            "JD backend %r %s; falling back to %s",
            backend_name,
            reason,
            _DEFAULT_BACKEND,
        )

    keyword_result = KeywordBackend().summarize(title, description)
    return {
        "skills": keyword_result["skills"],
        "summary": keyword_result["summary"],
        "backend": _DEFAULT_BACKEND,
    }
