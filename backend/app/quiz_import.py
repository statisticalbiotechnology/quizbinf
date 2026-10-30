"""Reading questions from a Markdown file, in the format the handout writes.

A quiz written in an editor is faster to produce than one typed into a form
question by question, and the Markdown handout (`export.as_markdown`) already
writes this shape, so a quiz can go out and come back. One question looks
like this:

    ## 3. Classes that do not overlap

    The question, in Markdown: paragraphs, code, `$formulas$`, figures.

    ![](figure.png){width=60%}

    - [ ] A wrong choice
    - [x] The right choice
    - [ ] Another wrong choice

    > The explanation, optional, after the choices.

Rules, each of which refuses the question it is broken in rather than
guessing what was meant:

* a question starts at a `##` heading; the number is optional and its title,
  if any, becomes a bold first line of the question text;
* the text comes first and must not be empty;
* then at least two choices, each `- [ ]` or `- [x]`, with exactly one `[x]`;
* then, optionally, the explanation as a block quote;
* nothing else after the choices, and no block quote before them.

A `#` heading before the first question is the quiz's title and is otherwise
ignored. Figures named by a bare filename must be uploaded with the file; they
are stored like any upload and the link is rewritten to point at the copy. One
that was not uploaded does not refuse its question: the figure is left out and
the teacher told, since a figure is easily added when editing and a broken
image on every phone is worse than none.

Parsing is a pure function of the text, so it is tested without a server and
the same result drives both the check shown to the teacher and the import.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_QUESTION_HEADING = re.compile(r"^##(?!#)\s*(?:(\d+)[.)]?)?\s*(.*?)\s*#*\s*$")
_TITLE_HEADING = re.compile(r"^#(?!#)\s+(.*?)\s*#*\s*$")
_TASK_ITEM = re.compile(r"^\s{0,3}[-*+]\s+\[( |x|X)\]\s?(.*)$")
_PLAIN_ITEM = re.compile(r"^\s{0,3}(?:[-*+]|\d+[.)])\s+")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
# A figure whose target is a bare file name rather than a URL or an upload.
_IMAGE = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)(\))")


@dataclass
class ParsedQuestion:
    number: int | None
    heading: str
    text: str = ""
    choices: list[tuple[str, bool]] = field(default_factory=list)
    explanation: str = ""
    errors: list[str] = field(default_factory=list)
    #: Imported anyway, but worth telling the teacher (a figure left out).
    warnings: list[str] = field(default_factory=list)
    #: Bare file names of figures it refers to, which must come with the upload.
    images: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class ParsedQuiz:
    title: str | None
    questions: list[ParsedQuestion]
    #: Problems with the file as a whole rather than with one question.
    notes: list[str] = field(default_factory=list)


def local_images(markdown: str) -> list[str]:
    """Figure targets that are neither a URL nor an app upload."""
    names = []
    for match in _IMAGE.finditer(markdown):
        target = match.group(2)
        if "://" in target or target.startswith("/"):
            continue
        names.append(target)
    return names


def drop_image(markdown: str, name: str) -> str:
    """Remove a figure, and its {width=…} sizing, from Markdown."""
    pattern = re.compile(r"!\[[^\]]*\]\(" + re.escape(name) + r"\)(\{[^{}\n]*\})?[ \t]*")
    return pattern.sub("", markdown)


def rewrite_images(markdown: str, mapping: dict[str, str]) -> str:
    """Point bare-file-name figures at their stored copies."""
    return _IMAGE.sub(
        lambda m: m.group(1) + mapping.get(m.group(2), m.group(2)) + m.group(3), markdown
    )


def parse(source: str) -> ParsedQuiz:
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    title: str | None = None
    notes: list[str] = []
    sections: list[tuple[re.Match, list[str]]] = []
    preamble: list[str] = []
    in_fence = False

    for line in lines:
        if _FENCE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else _QUESTION_HEADING.match(line)
        if heading:
            sections.append((heading, []))
        elif sections:
            sections[-1][1].append(line)
        else:
            preamble.append(line)

    for line in preamble:
        match = _TITLE_HEADING.match(line)
        if match and title is None:
            title = match.group(1) or None
        elif line.strip():
            notes.append(
                "Text before the first ## heading is not part of any question and was ignored."
            )
            break

    if not sections:
        notes.append(
            "No questions found: each question must start with a ## heading, such as '## 1.'."
        )
    questions = [_parse_question(match, body) for match, body in sections]
    return ParsedQuiz(title=title, questions=questions, notes=notes)


def _parse_question(heading: re.Match, body: list[str]) -> ParsedQuestion:
    number = int(heading.group(1)) if heading.group(1) else None
    title = heading.group(2).strip()
    question = ParsedQuestion(number=number, heading=title)

    text_lines: list[str] = []
    choice_lines: list[list[str]] = []  # each choice's lines, the first holding the mark
    marks: list[bool] = []
    quote_lines: list[str] = []
    stray: list[str] = []
    stage = "text"  # text -> choices -> explanation
    in_fence = False

    for line in body:
        fence = _FENCE.match(line)
        if stage == "text":
            if in_fence or fence:
                if fence:
                    in_fence = not in_fence
                text_lines.append(line)
                continue
            task = _TASK_ITEM.match(line)
            if task:
                stage = "choices"
                marks.append(task.group(1).lower() == "x")
                choice_lines.append([task.group(2)])
            elif _QUOTE.match(line):
                question.errors.append(
                    "A block quote (>) comes before the choices. The explanation "
                    "goes after them."
                )
                stage = "broken"
            else:
                text_lines.append(line)
        elif stage == "choices":
            task = _TASK_ITEM.match(line)
            if task:
                marks.append(task.group(1).lower() == "x")
                choice_lines.append([task.group(2)])
            elif not line.strip():
                continue
            elif _QUOTE.match(line):
                stage = "explanation"
                quote_lines.append(_QUOTE.match(line).group(1))
            elif _PLAIN_ITEM.match(line):
                question.errors.append(
                    f"A list item without [ ] or [x] among the choices: '{line.strip()}'."
                )
            elif line.startswith(("  ", "\t")):
                # An indented line continues the choice above it.
                choice_lines[-1].append(line.strip())
            else:
                stray.append(line.strip())
        elif stage == "explanation":
            quote = _QUOTE.match(line)
            if quote:
                quote_lines.append(quote.group(1))
            elif not line.strip():
                # A blank line ends a block quote; anything that follows is stray.
                stage = "after"
            else:
                stray.append(line.strip())
        elif stage == "after":
            if line.strip():
                if _QUOTE.match(line):
                    quote_lines.append("")
                    quote_lines.append(_QUOTE.match(line).group(1))
                    stage = "explanation"
                else:
                    stray.append(line.strip())

    if stray:
        question.errors.append(
            f"Text after the choices that is not a block quote: '{stray[0]}'. "
            "Only the explanation, as > lines, may follow the choices."
        )

    body_text = "\n".join(text_lines).strip()
    if not body_text:
        question.errors.append("The question has no text before its choices.")
    question.text = f"**{title}**\n\n{body_text}" if title and body_text else body_text

    choices = [" ".join(part.strip() for part in lines).strip() for lines in choice_lines]
    question.choices = list(zip(choices, marks))
    if len(choices) < 2:
        question.errors.append(
            "A question needs at least two choices, each written as '- [ ] …' or '- [x] …'."
        )
    if any(not text for text in choices):
        question.errors.append("A choice is empty.")
    correct = sum(marks)
    if choices and correct != 1:
        question.errors.append(
            f"Exactly one choice must be marked [x]; this question has {correct}."
        )

    question.explanation = "\n".join(quote_lines).strip()

    for part in [question.text, *choices, question.explanation]:
        for name in local_images(part):
            if name not in question.images:
                question.images.append(name)
    return question
