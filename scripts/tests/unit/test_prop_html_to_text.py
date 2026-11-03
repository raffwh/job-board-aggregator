"""Property-based test for ``html_to_text``.

Property 20: HTML-to-text output is tag-free and idempotent.

For any string generated from HTML tags, attributes, text, named and numeric
entities, entity-escaped HTML, and stray ``<`` and ``&`` characters,
``html_to_text`` SHALL return a string:

- with no substring matching ``<[A-Za-z/!?]`` (no parseable HTML tag opener);
- with no runs of two or more spaces;
- that is a fixed point: ``html_to_text(html_to_text(x)) == html_to_text(x)``;
- of length at most 20,000 characters.

The generators assemble inputs from HTML tag fragments (including block tags,
script/style blocks, and self-closing tags), free-form Unicode text, named and
numeric entities, entity-escaped HTML (the Greenhouse case), and stray ``<``
and ``&`` characters, so the examples exercise every branch of the converter
rather than just well-formed markup.

Validates: Requirements 10.4, 10.8
"""

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from detail_enricher import _MAX_LEN, html_to_text


# A parseable tag opener: ``<`` immediately followed by a letter, ``/``, ``!``
# or ``?``. The output must never contain this (design Property 20).
_TAG_OPENER_RE = re.compile(r"<[A-Za-z/!?]")


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# Tag names: a mix of block tags, dropped tags, and arbitrary inline tags.
_TAG_NAMES = st.sampled_from(
    [
        "p", "br", "li", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr",
        "script", "style", "span", "a", "b", "strong", "em", "ul", "ol",
        "table", "td", "section",
    ]
)

# Optional attribute text appended inside a tag, e.g. ``<a href="x">``.
_ATTRS = st.sampled_from(
    ["", ' href="https://example.com"', ' class="c"', " data-x='y'", " disabled"]
)

# A start, end, or self-closing tag.
_tag = st.one_of(
    st.builds(lambda name, attrs: f"<{name}{attrs}>", _TAG_NAMES, _ATTRS),
    st.builds(lambda name: f"</{name}>", _TAG_NAMES),
    st.builds(lambda name, attrs: f"<{name}{attrs}/>", _TAG_NAMES, _ATTRS),
)

# Named and numeric HTML entities.
_entity = st.sampled_from(
    ["&amp;", "&lt;", "&gt;", "&quot;", "&#39;", "&#x27;", "&nbsp;", "&copy;", "&#65;"]
)

# Stray characters that could look like the start of markup or an entity.
_stray = st.sampled_from(["<", "&", "< ", "<<", "&&", "a < b", "x & y", "<3", "&foo"])

# Free-form text, including whitespace, newlines, and Unicode.
_free_text = st.text(max_size=120)

# A script or style block whose inner content must be dropped from the output.
_drop_block = st.builds(
    lambda tag, body: f"<{tag}>{body}</{tag}>",
    st.sampled_from(["script", "style"]),
    st.sampled_from(
        [
            "var x = 1 < 2;",
            "function f(){return '<div>';}",
            "body { color: red; }",
            "alert('hi'); & more",
            "",
        ]
    ),
)

# One fragment is any of the above building blocks.
_fragment = st.one_of(_tag, _entity, _stray, _free_text, _drop_block)

# An HTML-ish document: a sequence of fragments joined together.
_html_doc = st.lists(_fragment, min_size=0, max_size=20).map("".join)

# The Greenhouse case: entity-escaped HTML with no real ``<`` characters.
_escaped_html = _html_doc.map(
    lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
).filter(lambda s: "<" not in s)

# The full input space, plus the None/empty degenerate cases.
_input = st.one_of(
    st.none(),
    st.just(""),
    _free_text,
    _html_doc,
    _escaped_html,
)


# ---------------------------------------------------------------------------
# Property 20
# ---------------------------------------------------------------------------


@settings(max_examples=500)
@given(x=_input)
def test_html_to_text_tag_free_and_idempotent(x):
    out = html_to_text(x)

    # Always returns a string.
    assert isinstance(out, str)

    # No parseable HTML tag opener survives.
    assert _TAG_OPENER_RE.search(out) is None, (
        f"output still contains a tag opener: {out!r}"
    )

    # Whitespace is collapsed: no run of two or more spaces.
    assert "  " not in out, f"output contains a run of spaces: {out!r}"

    # Length is capped at _MAX_LEN (20,000).
    assert len(out) <= _MAX_LEN, f"output length {len(out)} exceeds {_MAX_LEN}"

    # Idempotent: converting the output again changes nothing.
    assert html_to_text(out) == out, (
        f"html_to_text is not a fixed point for {x!r}: {html_to_text(out)!r} != {out!r}"
    )


def test_html_to_text_degenerate_inputs():
    """Explicit edge cases: None and empty string both yield ``\"\"``."""
    assert html_to_text(None) == ""
    assert html_to_text("") == ""


def test_html_to_text_length_cap_enforced():
    """A long input is truncated to at most _MAX_LEN characters."""
    long_text = "a " * (_MAX_LEN)  # well over the cap once collapsed
    out = html_to_text(long_text)
    assert len(out) <= _MAX_LEN
