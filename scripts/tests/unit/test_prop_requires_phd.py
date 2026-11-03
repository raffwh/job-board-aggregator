"""Property-based test for the PhD-required exclusion helper.

Property 31: requires_phd matches required phrasings, respects soft vetoes,
and is deterministic.

For any description built by embedding a random PHD_REQUIRED_PATTERNS phrasing
and/or a random PHD_SOFT_PATTERNS phrasing into random English filler near a
PhD mention, ``requires_phd`` SHALL:

- return true when a required phrasing is present near a PhD mention and no
  soft phrasing is within the mention's window;
- return false when a soft phrasing is within the mention's window, even when
  a required phrasing is also present;
- return false when no PhD mention is present;
- return the same value on a second call with the same description.

The helper tests each phrasing in a +/- 60 character window around every
PhD / doctorate mention. To keep the soft-veto-near-mention case unambiguous,
these generators place the required and soft phrasings adjacent (one short
connector apart) so both fall inside the same mention window, and build the
"no veto" case by separating any extra text far enough that the only PhD
mention in a window is the required one.

Validates: Requirements 20.1, 20.3, 20.4, 20.6
"""

import re

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from filters import requires_phd, _PHD_MENTION, _PHD_WINDOW
import personal_config


# ---------------------------------------------------------------------------
# Concrete phrasings that match the config pattern sets.
#
# The config patterns are regexes; the property embeds literal phrasings that
# are known to match them, mirroring how a real job description reads. Each
# string below is asserted (see test_fixtures_match_config) to match at least
# one pattern in its set and, for required phrasings, to NOT be vetoed on its
# own by any soft pattern.
# ---------------------------------------------------------------------------

_REQUIRED_PHRASINGS = [
    "PhD required",
    "Ph.D. required",
    "PhD is required",
    "requires a PhD",
    "requires a Ph.D.",
    "must have a PhD",
    "doctorate required",
    "PhD/doctorate required",
    "a PhD in a quantitative field is required",
]

_SOFT_PHRASINGS = [
    "PhD preferred",
    "Ph.D. preferred",
    "PhD a plus",
    "PhD desirable",
    "PhD or equivalent",
    "PhD nice to have",
    "Master's or PhD",
    "MS or PhD",
    "MS/PhD",
    "Bachelor's, Master's, or PhD",
]

# Filler that contains no PhD / doctorate mention, so inserting it never adds a
# spurious mention window. Each word is plain lowercase letters.
_filler_word = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz", min_size=2, max_size=9
).filter(lambda w: not _PHD_MENTION.search(w))


def _matches_any(phrasing, patterns):
    return any(p.search(phrasing) for p in patterns)


def _soft_vetoes_every_required_mention(description):
    """Mirror the helper's windowing to check the soft-veto precondition.

    ``requires_phd`` scans each PhD / doctorate mention and, within a
    +/- ``_PHD_WINDOW`` character window, a soft pattern vetoes that
    mention while a required pattern (with no veto) triggers exclusion.
    The soft veto therefore only genuinely suppresses a required match
    when, for every mention whose window contains a required pattern,
    that same window also contains a soft pattern. For long required
    phrasings (e.g. "a PhD in a quantitative field is required"), the
    first PhD token's window can reach "required" without reaching a
    soft phrase placed after it, so adjacency alone is not enough.

    Returns True only when the soft veto wins for every required-bearing
    window, i.e. exactly the condition under which the generated example
    is a valid soft-veto case.
    """
    text = description.lower()
    soft = personal_config.PHD_SOFT_PATTERNS
    required = personal_config.PHD_REQUIRED_PATTERNS
    saw_required_window = False
    for m in _PHD_MENTION.finditer(text):
        start = max(0, m.start() - _PHD_WINDOW)
        end = m.end() + _PHD_WINDOW
        window = text[start:end]
        has_soft = any(p.search(window) for p in soft)
        has_required = any(p.search(window) for p in required)
        if has_required:
            saw_required_window = True
            if not has_soft:
                return False
    return saw_required_window


@st.composite
def _filler(draw, min_words=0, max_words=12):
    n = draw(st.integers(min_value=min_words, max_value=max_words))
    words = [draw(_filler_word) for _ in range(n)]
    result = " ".join(words)
    # Each word is individually mention-free, but adjacent words can
    # concatenate across the space into a spurious PhD mention (e.g. "ph" +
    # "da" -> "ph da", which _PHD_MENTION matches as "ph d"). Guard the
    # assembled string so generated filler is truly mention-free.
    assume(not _PHD_MENTION.search(result))
    return result


# ---------------------------------------------------------------------------
# Generators for each case.
# ---------------------------------------------------------------------------


@st.composite
def required_only_descriptions(draw):
    """Filler + a required phrasing + filler, with no soft phrasing anywhere.

    The only PhD mention(s) come from the required phrasing, so no soft pattern
    can sit in any mention window.
    """
    phrasing = draw(st.sampled_from(_REQUIRED_PHRASINGS))
    lead = draw(_filler())
    trail = draw(_filler())
    parts = [p for p in (lead, phrasing, trail) if p]
    return " ".join(parts), phrasing


@st.composite
def soft_near_mention_descriptions(draw):
    """A soft phrasing placed immediately next to a required phrasing.

    The two are one short connector apart so they normally share a mention
    window and the soft veto suppresses the required match ("even when a
    required phrasing is also present"). A long required phrasing can still
    put its own PhD token's window out of reach of the soft phrase, so the
    generator ``assume``s the soft-veto precondition and skips examples where
    a required trigger's window has no soft phrasing in it.
    """
    required = draw(st.sampled_from(_REQUIRED_PHRASINGS))
    soft = draw(st.sampled_from(_SOFT_PHRASINGS))
    connector = draw(st.sampled_from([" ", "; ", ", ", " - ", " but "]))
    # soft first or required first; adjacency keeps both inside one window.
    if draw(st.booleans()):
        core = soft + connector + required
    else:
        core = required + connector + soft
    lead = draw(_filler())
    trail = draw(_filler())
    parts = [p for p in (lead, core, trail) if p]
    description = " ".join(parts)
    # Adjacency is not sufficient when a required phrasing is long enough that
    # its own PhD token's +/- _PHD_WINDOW window reaches the "required" trigger
    # without reaching the soft phrasing. Keep only examples where the soft
    # veto genuinely lands in the same window as every required trigger, so the
    # property asserts real soft-veto behavior rather than a tautology.
    assume(_soft_vetoes_every_required_mention(description))
    return description, required, soft


@st.composite
def soft_only_descriptions(draw):
    """Filler + a soft phrasing + filler, with no required phrasing."""
    phrasing = draw(st.sampled_from(_SOFT_PHRASINGS))
    lead = draw(_filler())
    trail = draw(_filler())
    parts = [p for p in (lead, phrasing, trail) if p]
    return " ".join(parts), phrasing


@st.composite
def no_mention_descriptions(draw):
    """Plain filler with no PhD / doctorate mention at all."""
    description = draw(_filler(min_words=0, max_words=30))
    # _filler already guards the assembled string, but assert the precondition
    # here too so this case can never hand the property a spurious mention.
    assume(not _PHD_MENTION.search(description))
    return description


# ---------------------------------------------------------------------------
# Sanity check: the literal fixtures really match the config patterns.
# ---------------------------------------------------------------------------


def test_fixtures_match_config():
    """Each fixture phrasing matches its config pattern set, and required
    phrasings are not self-vetoed by a soft pattern. This guards the property
    generators against drifting away from PHD_REQUIRED_PATTERNS /
    PHD_SOFT_PATTERNS (Requirement 20.5)."""
    for phrasing in _REQUIRED_PHRASINGS:
        assert _matches_any(phrasing, personal_config.PHD_REQUIRED_PATTERNS), (
            f"required fixture does not match any PHD_REQUIRED_PATTERNS: {phrasing!r}"
        )
        assert not _matches_any(phrasing, personal_config.PHD_SOFT_PATTERNS), (
            f"required fixture is unexpectedly vetoed by a soft pattern: {phrasing!r}"
        )
    for phrasing in _SOFT_PHRASINGS:
        assert _matches_any(phrasing, personal_config.PHD_SOFT_PATTERNS), (
            f"soft fixture does not match any PHD_SOFT_PATTERNS: {phrasing!r}"
        )


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=400)
@given(case=required_only_descriptions())
def test_required_phrasing_with_no_soft_veto_is_true(case):
    """A required phrasing near a PhD mention with no soft phrasing anywhere
    makes requires_phd true (Requirements 20.1, 20.3)."""
    description, _phrasing = case
    assert requires_phd(description) is True


@settings(max_examples=400)
@given(case=soft_near_mention_descriptions())
def test_soft_phrasing_near_mention_vetoes_required(case):
    """A soft phrasing within the mention window vetoes the exclusion even when
    a required phrasing is also present (Requirement 20.4)."""
    description, _required, _soft = case
    assert requires_phd(description) is False


@settings(max_examples=400)
@given(case=soft_only_descriptions())
def test_soft_only_is_false(case):
    """A softened PhD mention with no required phrasing is kept (not required)
    (Requirement 20.4)."""
    description, _phrasing = case
    assert requires_phd(description) is False


@settings(max_examples=400)
@given(description=no_mention_descriptions())
def test_no_phd_mention_is_false(description):
    """Filler with no PhD / doctorate mention is never flagged as PhD-required
    (Requirement 20.3)."""
    # Precondition: the generated filler truly contains no mention.
    assert not _PHD_MENTION.search(description)
    assert requires_phd(description) is False


@settings(max_examples=400)
@given(
    case=st.one_of(
        required_only_descriptions().map(lambda c: c[0]),
        soft_near_mention_descriptions().map(lambda c: c[0]),
        soft_only_descriptions().map(lambda c: c[0]),
        no_mention_descriptions(),
    )
)
def test_determinism(case):
    """Computing requires_phd twice on the same description returns the same
    value (Requirement 20.6)."""
    description = case
    first = requires_phd(description)
    second = requires_phd(description)
    assert first is second


# ---------------------------------------------------------------------------
# Concrete regression examples from the spec's phrasing lists.
# ---------------------------------------------------------------------------


def test_known_required_examples_are_true():
    for text in (
        "PhD required for this role.",
        "The position requires a PhD in statistics.",
        "You must have a PhD.",
        "Doctorate required.",
        "PhD is required; prior industry experience valued.",
    ):
        assert requires_phd(text) is True, text


def test_known_soft_examples_are_false():
    for text in (
        "PhD preferred but not required.",
        "PhD or equivalent experience.",
        "Master's or PhD in a related field.",
        "MS/PhD in a quantitative discipline.",
        "A PhD is a plus.",
    ):
        assert requires_phd(text) is False, text
