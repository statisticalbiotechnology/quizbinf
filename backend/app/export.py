"""Exporting a quiz as study material.

Students ask for the questions after a lecture, and the place they look is
Canvas. So this produces something a teacher can paste into a Canvas page:
Markdown for a source they may want to edit, HTML for a straight paste into
Canvas's rich-text editor.

The one thing that must not be lost in the move is the figures. A question
references an uploaded figure by a relative path (`/api/images/x.png`), which
resolves against quizbinf and would resolve against Canvas — that is, not at
all — once pasted there. Every export therefore rewrites those to absolute
URLs pointing back at this deployment. The files are public and unguessable,
so they load for a student reading the page in Canvas.
"""

import re

from .markdown import render
from .models import Quiz

# Only the app's own upload paths are made absolute. A link a teacher wrote to
# somewhere else is already whatever they meant it to be.
_LOCAL_IMAGE = re.compile(r"(?P<prefix>!\[[^\]]*\]\()(?P<path>/api/images/[^)\s]+)")
_LOCAL_SRC = re.compile(r'(?P<prefix>src=")(?P<path>/api/images/[^"]+)')


def absolute_markdown(text: str, base_url: str) -> str:
    return _LOCAL_IMAGE.sub(lambda m: m["prefix"] + base_url + m["path"], text)


def absolute_html(html: str, base_url: str) -> str:
    return _LOCAL_SRC.sub(lambda m: m["prefix"] + base_url + m["path"], html)


def as_markdown(quiz: Quiz, base_url: str, answers: bool = True) -> str:
    """The quiz as Markdown, questions numbered in their running order."""
    lines = [f"# {quiz.title}", ""]
    for question in quiz.questions:
        lines.append(f"## {question.position + 1}.")
        lines.append("")
        lines.append(absolute_markdown(question.text, base_url))
        lines.append("")
        for choice in question.choices:
            # A checked box for the right answer: it survives a paste into
            # anything that renders Markdown, and reads plainly where it does
            # not.
            mark = "[x]" if (answers and choice.is_correct) else "[ ]"
            lines.append(f"- {mark} {choice.text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def as_html(quiz: Quiz, base_url: str, answers: bool = True) -> str:
    """The quiz as a standalone HTML document.

    Question text goes through the same renderer and sanitiser the app uses,
    so what a student reads in Canvas is what they saw in the lecture.
    """
    parts = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        f"<title>{_escape(quiz.title)}</title>",
        "<style>",
        "body { font-family: system-ui, sans-serif; line-height: 1.5;",
        "       max-width: 44rem; margin: 2rem auto; padding: 0 1rem; }",
        "img { max-width: 100%; height: auto; }",
        "li.correct { font-weight: 700; }",
        ".answer { color: #2c7a51; }",
        "</style>",
        "</head>",
        "<body>",
        f"<h1>{_escape(quiz.title)}</h1>",
    ]
    for question in quiz.questions:
        parts.append(f"<h2>{question.position + 1}.</h2>")
        parts.append(absolute_html(render(question.text), base_url))
        parts.append("<ul>")
        for choice in question.choices:
            correct = answers and choice.is_correct
            tick = ' <span class="answer">&#10003;</span>' if correct else ""
            css = ' class="correct"' if correct else ""
            parts.append(f"<li{css}>{_escape(choice.text)}{tick}</li>")
        parts.append("</ul>")
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts) + "\n"


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
