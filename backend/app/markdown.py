"""Render question text written in Markdown.

Rendered on the server so every client shows the same thing and the
sanitisation lives in one place. The output is still bound with Angular's
`[innerHTML]`, which sanitises again — a teacher account is trusted, but the
result is displayed to every student in the room, so one layer is not enough.
"""

import re

import nh3
from markdown_it import MarkdownIt
from mdit_py_plugins.attrs import attrs_plugin
from mdit_py_plugins.dollarmath import dollarmath_plugin

# Sizing a figure: `![](/api/images/x.png){width=60%}` sets it to 60% of the
# column it is in, which is what a teacher wants — the column is a phone on a
# student's screen and the projected panel in the hall, and one percentage
# suits both. Only these two attributes are accepted, so the general-purpose
# attribute syntax cannot be used to attach anything else.
_SIZE_ATTRIBUTES = ["width", "height"]

# The attribute parser treats `%` as the start of a comment, so a bare
# `{width=60%}` fails to parse and the braces end up in the question text.
# Quote it first: the unquoted spelling is the one Pandoc uses and therefore
# the one a teacher will write.
#
# Only a block that directly follows a `)` is touched, so that braces in the
# question's own prose are left exactly as the teacher typed them.
_ATTR_BLOCK = re.compile(r"(?<=\))\{[^{}\n]*\}")
_BARE_PERCENT = re.compile(r"\b((?:width|height)=)(\d{1,3}%)")


def _quote_percentages(text: str) -> str:
    return _ATTR_BLOCK.sub(
        lambda block: _BARE_PERCENT.sub(r'\1"\2"', block.group()), text
    )

# CommonMark, plus tables (handy for small data in a question). No raw HTML:
# `html=False` means anything HTML-looking in the source is escaped rather
# than passed through, so the sanitiser below is a second line of defence and
# not the only one.
#
# Formulas are written as in LaTeX: `$...$` inline, `$$...$$` on a line of
# their own. The server only marks them, as `<span class="math inline">` and
# `<div class="math block">` holding the TeX source as text; the browser draws
# them with KaTeX. A dollar followed by a space or closed before a digit is not
# a formula, so "costs $5 and $10" stays prose.
_md = (
    MarkdownIt("commonmark", {"html": False, "linkify": True})
    .enable("table")
    .use(attrs_plugin, allowed=_SIZE_ATTRIBUTES)
    .use(dollarmath_plugin, allow_space=False, allow_digits=False, double_inline=True)
)

# Formatting a question needs: emphasis, code (sequences, gene names), lists,
# small tables, headings, and images/links. Nothing that can execute.
_ALLOWED_TAGS = {
    "p", "br", "hr",
    "strong", "em", "del", "sub", "sup",
    "code", "pre",
    "ul", "ol", "li",
    "blockquote",
    "h1", "h2", "h3", "h4",
    "a", "img",
    "table", "thead", "tbody", "tr", "th", "td",
    # Carriers for formulas only: no attribute but the two classes below.
    "span", "div",
}

# The only classes that survive, and only on the elements the math plugin
# writes. Anything else a class could mean in the page's own CSS stays out.
_ALLOWED_CLASSES = {
    "span": {"math", "inline"},
    "div": {"math", "block"},
}

_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", *_SIZE_ATTRIBUTES},
    "th": {"align"},
    "td": {"align"},
}


def render(text: str) -> str:
    """Markdown to sanitised HTML. Never raises on odd input."""
    if not text:
        return ""
    html = _md.render(_quote_percentages(text))
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        allowed_classes=_ALLOWED_CLASSES,
        # Images and links may point at an uploaded file (a relative
        # /api/images/... path) or at an external https resource.
        url_schemes={"http", "https"},
        link_rel="noopener noreferrer",
    ).strip()


_ONE_PARAGRAPH = re.compile(r"\A<p>((?:(?!</?p>).)*)</p>\Z", re.DOTALL)


def render_inline(text: str) -> str:
    """The same, for a short piece such as a choice: one paragraph comes back
    without its `<p>`, so it sits on the line of a button or a list item. A
    choice with a display formula or several paragraphs keeps its blocks."""
    html = render(text)
    match = _ONE_PARAGRAPH.match(html)
    return match.group(1) if match else html
