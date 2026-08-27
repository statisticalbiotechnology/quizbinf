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
_md = (
    MarkdownIt("commonmark", {"html": False, "linkify": True})
    .enable("table")
    .use(attrs_plugin, allowed=_SIZE_ATTRIBUTES)
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
        # Images and links may point at an uploaded file (a relative
        # /api/images/... path) or at an external https resource.
        url_schemes={"http", "https"},
        link_rel="noopener noreferrer",
    ).strip()
