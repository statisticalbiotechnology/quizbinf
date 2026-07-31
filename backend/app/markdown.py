"""Render question text written in Markdown.

Rendered on the server so every client shows the same thing and the
sanitisation lives in one place. The output is still bound with Angular's
`[innerHTML]`, which sanitises again — a teacher account is trusted, but the
result is displayed to every student in the room, so one layer is not enough.
"""

import nh3
from markdown_it import MarkdownIt

# CommonMark, plus tables (handy for small data in a question). No raw HTML:
# `html=False` means anything HTML-looking in the source is escaped rather
# than passed through, so the sanitiser below is a second line of defence and
# not the only one.
_md = MarkdownIt("commonmark", {"html": False, "linkify": True}).enable("table")

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
    "img": {"src", "alt", "title"},
    "th": {"align"},
    "td": {"align"},
}


def render(text: str) -> str:
    """Markdown to sanitised HTML. Never raises on odd input."""
    if not text:
        return ""
    html = _md.render(text)
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        # Images and links may point at an uploaded file (a relative
        # /api/images/... path) or at an external https resource.
        url_schemes={"http", "https"},
        link_rel="noopener noreferrer",
    ).strip()
