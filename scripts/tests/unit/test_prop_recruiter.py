"""Property-based test for recruiter detection by whole-token match.

Property 9: Recruiter detection uses whole tokens only.

For any company slug built by joining random tokens with ``-``, ``_``, or
nothing, the Recruiter_Detector flags the slug exactly when at least one
separator-delimited token equals a configured recruiter term and the slug is
not in ``RECRUITER_ALLOWLIST``. In particular, a slug whose only recruiter-like
text is inside a longer token (such as ``search`` inside ``research`` or
``iqviaresearch``) is not flagged.

Validates: Requirements 7.11, 7.12
"""

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from filters import RecruiterDetector
import personal_config


# The source tokenizer treats any run of non-[a-z0-9] characters as a
# separator, so a whole token is a maximal run of [a-z0-9]. We mirror that here
# to compute the expected outcome independently of the implementation details.
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

_TERMS = list(personal_config.RECRUITER_TERMS)
_TERM_SET = {t.lower() for t in _TERMS}


def _tokens(text):
    if not text:
        return set()
    return {tok for tok in _TOKEN_SPLIT.split(text.lower()) if tok}


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# Non-recruiter tokens: lowercase alnum tokens that are not themselves a
# recruiter term. Longer tokens such as "research" (which contains "search")
# are allowed and are exactly the substring-only case the property guards.
_plain_token = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=10
).filter(lambda t: t not in _TERM_SET)

_recruiter_token = st.sampled_from(_TERMS)

# Separators the slug builder may use between tokens: hyphen, underscore, or the
# empty string (which merges adjacent tokens into one larger token).
_separator = st.sampled_from(["-", "_", ""])


@st.composite
def random_slugs(draw):
    """A slug built from a mix of recruiter and plain tokens joined by a
    per-gap choice of ``-``, ``_``, or nothing."""
    n = draw(st.integers(min_value=1, max_value=5))
    tokens = []
    for _ in range(n):
        if draw(st.booleans()):
            tokens.append(draw(_recruiter_token))
        else:
            tokens.append(draw(_plain_token))
    # Choose a separator independently for each gap between tokens.
    out = tokens[0]
    for tok in tokens[1:]:
        out += draw(_separator) + tok
    return out


# Slugs whose recruiter-like text only ever appears as a substring of a larger
# token, so no whole token equals a recruiter term. Examples: "research",
# "iqviaresearch", "researchgate".
@st.composite
def substring_only_slugs(draw):
    term = draw(_recruiter_token)
    prefix = draw(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=0, max_size=8)
    )
    suffix = draw(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=0, max_size=8)
    )
    # Guarantee the term is embedded in a strictly larger single token so it is
    # never a standalone token. If both affixes are empty, force a suffix.
    if not prefix and not suffix:
        suffix = "corp"
    token = f"{prefix}{term}{suffix}"
    # Optionally surround with other plain tokens joined by separators.
    lead = draw(st.one_of(st.just(""), _plain_token))
    trail = draw(st.one_of(st.just(""), _plain_token))
    sep = draw(st.sampled_from(["-", "_"]))
    parts = [p for p in (lead, token, trail) if p]
    return sep.join(parts)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=500)
@given(slug=random_slugs())
def test_flagged_exactly_when_whole_token_matches(slug):
    """A slug is flagged exactly when one of its separator-delimited tokens
    equals a recruiter term (Requirement 7.11)."""
    detector = RecruiterDetector(terms=_TERMS, allowlist=set())
    expected = bool(_tokens(slug) & _TERM_SET)
    assert detector.is_recruiter(slug) is expected


@settings(max_examples=500)
@given(slug=substring_only_slugs())
def test_substring_only_is_not_flagged(slug):
    """A slug whose recruiter text only appears inside a larger token (such as
    ``search`` within ``research`` or ``iqviaresearch``) is never flagged
    (Requirement 7.11)."""
    detector = RecruiterDetector(terms=_TERMS, allowlist=set())
    # Precondition: no standalone token equals a recruiter term.
    assert not (_tokens(slug) & _TERM_SET)
    assert detector.is_recruiter(slug) is False


def test_known_substring_examples_not_flagged():
    """Concrete regression examples from the spec: substrings must not flag."""
    detector = RecruiterDetector(terms=_TERMS, allowlist=set())
    for slug in ("research", "iqviaresearch", "research-triangle", "researchgate"):
        assert detector.is_recruiter(slug) is False


@settings(max_examples=500)
@given(slug=random_slugs())
def test_allowlisted_slug_is_never_flagged(slug):
    """A slug in RECRUITER_ALLOWLIST is reported as not a recruiter even when it
    contains a whole recruiter token (Requirement 7.12)."""
    detector = RecruiterDetector(terms=_TERMS, allowlist={slug.lower()})
    assert detector.is_recruiter(slug) is False
