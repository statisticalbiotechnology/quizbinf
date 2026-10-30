"""Importing questions from a Markdown file.

The format is the one the Markdown handout writes (see app/quiz_import.py):
`##` per question, the text, `- [ ]`/`- [x]` choices with exactly one `[x]`,
and an optional `>` explanation. A question that breaks a rule is refused with
a reason and the rest are still imported: the teacher fixes the file, not a
half-guessed question.
"""

import base64

from app.quiz_import import parse
from tests.conftest import login

# Smallest valid PNG: 1x1, transparent.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

GOOD = """# Alignment

## 1. Local or global

Which algorithm finds the optimal **local** alignment?

- [ ] Needleman-Wunsch
- [x] Smith-Waterman
- [ ] BLAST

> Smith-Waterman lets the score restart at zero, which is what makes it local.
>
> Needleman-Wunsch aligns end to end.

## 2.

A score $S = \\log_2 \\frac{p}{q}$ above zero means:

![](dotplot.png){width=60%}

```python
# a list item in a code block is not a choice
- [x] not a choice
```

- [x] more often than chance
- [ ] less often than chance
"""


def _quiz(client, title="Imported") -> int:
    return client.post("/api/quizzes", json={"title": title}).json()["id"]


def _upload(client, quiz_id, markdown: str, figures=None, dry_run=True, name="quiz.md"):
    files = [("files", (name, markdown.encode(), "text/markdown"))]
    for fname, data in (figures or {}).items():
        files.append(("files", (fname, data, "application/octet-stream")))
    return client.post(
        f"/api/quizzes/{quiz_id}/import?dry_run={'true' if dry_run else 'false'}", files=files
    )


# --- the parser ------------------------------------------------------------


def test_a_well_formed_file_parses_completely():
    quiz = parse(GOOD)
    assert quiz.title == "Alignment"
    first, second = quiz.questions
    assert first.ok and second.ok
    assert first.text.startswith("**Local or global**\n\n")
    assert first.choices == [
        ("Needleman-Wunsch", False), ("Smith-Waterman", True), ("BLAST", False)]
    assert first.explanation.startswith("Smith-Waterman lets")
    assert "Needleman-Wunsch aligns" in first.explanation
    # A heading with no title adds nothing; the code block stays in the text.
    assert not second.text.startswith("**")
    assert "- [x] not a choice" in second.text
    assert len(second.choices) == 2
    assert second.images == ["dotplot.png"]


def _errors(question_body: str) -> list[str]:
    return parse("## 1.\n\n" + question_body).questions[0].errors


def test_each_formatting_rule_refuses_its_question():
    assert any("Exactly one" in e for e in _errors("Q?\n\n- [ ] a\n- [ ] b\n"))
    assert any("Exactly one" in e for e in _errors("Q?\n\n- [x] a\n- [x] b\n"))
    assert any("at least two" in e for e in _errors("Q?\n\n- [x] a\n"))
    assert any("no text" in e for e in _errors("- [x] a\n- [ ] b\n"))
    assert any("without [ ] or [x]" in e for e in _errors("Q?\n\n- [x] a\n- b\n"))
    assert any("not a block quote" in e for e in _errors("Q?\n\n- [x] a\n- [ ] b\n\nMore text\n"))
    assert any("before the choices" in e for e in _errors("Q?\n\n> why\n\n- [x] a\n- [ ] b\n"))
    assert _errors("Q?\n\n- [x] a\n- [ ] b\n") == []


def test_a_file_without_questions_says_so():
    quiz = parse("# Only a title\n\nSome prose.\n")
    assert quiz.questions == []
    assert any("No questions found" in n for n in quiz.notes)


# --- the endpoint ----------------------------------------------------------


def test_a_dry_run_reports_and_stores_nothing(teacher_client):
    quiz_id = _quiz(teacher_client)
    resp = _upload(teacher_client, quiz_id, GOOD, {"dotplot.png": PNG})
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert (report["accepted"], report["refused"], report["imported"]) == (2, 0, 0)
    assert teacher_client.get(f"/api/quizzes/{quiz_id}").json()["questions"] == []


def test_importing_appends_the_accepted_questions(teacher_client):
    quiz_id = _quiz(teacher_client)
    teacher_client.post(
        f"/api/quizzes/{quiz_id}/questions",
        json={"text": "Already here", "choices": [
            {"text": "a", "is_correct": True}, {"text": "b"}]},
    )
    resp = _upload(teacher_client, quiz_id, GOOD, {"dotplot.png": PNG}, dry_run=False)
    assert resp.json()["imported"] == 2

    questions = teacher_client.get(f"/api/quizzes/{quiz_id}").json()["questions"]
    assert [q["position"] for q in questions] == [0, 1, 2]
    imported = questions[1]
    assert [c["is_correct"] for c in imported["choices"]] == [False, True, False]
    assert "<strong>Local or global</strong>" in imported["text_html"]
    assert "restart at zero" in imported["explanation_html"]

    # The figure was stored like any upload, and the link now points at it.
    figure_question = questions[2]
    assert "dotplot.png" not in figure_question["text"]
    src = figure_question["text"].split("](")[1].split(")")[0]
    assert src.startswith("/api/images/")
    assert teacher_client.get(src).content == PNG


def test_a_broken_question_is_refused_and_the_rest_imported(teacher_client):
    quiz_id = _quiz(teacher_client)
    bad = GOOD + "\n## 3. No answer marked\n\nWhich?\n\n- [ ] a\n- [ ] b\n"
    report = _upload(teacher_client, quiz_id, bad, {"dotplot.png": PNG}, dry_run=False).json()
    assert (report["accepted"], report["refused"], report["imported"]) == (2, 1, 2)
    refused = report["questions"][2]
    assert refused["ok"] is False
    assert any("Exactly one" in e for e in refused["errors"])
    assert len(teacher_client.get(f"/api/quizzes/{quiz_id}").json()["questions"]) == 2


def test_a_figure_not_uploaded_is_left_out_not_refused(teacher_client):
    """Text first: the question comes in without the figure, and says so."""
    quiz_id = _quiz(teacher_client)
    report = _upload(teacher_client, quiz_id, GOOD, dry_run=False).json()
    second = report["questions"][1]
    assert second["ok"] is True
    assert any("left out" in w and "not uploaded" in w for w in second["warnings"])
    stored = teacher_client.get(f"/api/quizzes/{quiz_id}").json()["questions"][1]["text"]
    assert "dotplot.png" not in stored and "{width" not in stored

    fake = _upload(teacher_client, quiz_id, GOOD, {"dotplot.png": b"not a png"}).json()
    assert fake["questions"][1]["ok"] is True
    assert any("does not look like" in w for w in fake["questions"][1]["warnings"])


def test_exactly_one_markdown_file(teacher_client):
    quiz_id = _quiz(teacher_client)
    none = teacher_client.post(
        f"/api/quizzes/{quiz_id}/import", files=[("files", ("x.png", PNG, "image/png"))]
    )
    assert none.status_code == 422
    two = teacher_client.post(
        f"/api/quizzes/{quiz_id}/import",
        files=[("files", ("a.md", b"## 1.\n", "text/markdown")),
               ("files", ("b.md", b"## 1.\n", "text/markdown"))],
    )
    assert two.status_code == 422


def test_the_handout_with_answers_imports_back(teacher_client):
    """Out as the Markdown handout, back in as questions: the same quiz."""
    source = _quiz(teacher_client, "Source")
    _upload(teacher_client, source, GOOD, {"dotplot.png": PNG}, dry_run=False)
    handout = teacher_client.get(f"/api/quizzes/{source}/export.md?answers=true").text

    target = _quiz(teacher_client, "Target")
    report = _upload(teacher_client, target, handout, dry_run=False).json()
    assert report["imported"] == 2, report

    def shape(quiz_id):
        return [
            ([c["text"] for c in q["choices"]], [c["is_correct"] for c in q["choices"]],
             q["explanation"])
            for q in teacher_client.get(f"/api/quizzes/{quiz_id}").json()["questions"]
        ]

    assert shape(target) == shape(source)


def test_the_explanation_never_reaches_a_phone(teacher_client, make_client):
    quiz_id = _quiz(teacher_client)
    _upload(teacher_client, quiz_id, GOOD, {"dotplot.png": PNG}, dry_run=False)
    question = teacher_client.get(f"/api/quizzes/{quiz_id}").json()["questions"][0]
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": question["id"], "phase": "pre"}
    )
    student = make_client()
    login(student, "importstudent")
    state = student.get(f"/api/sessions/{code}/state").text
    assert "restart at zero" not in state
    assert "explanation" not in state


def test_only_the_owner_can_import(teacher_client, make_client):
    quiz_id = _quiz(teacher_client)
    other = make_client()
    login(other, "teach2")
    assert _upload(other, quiz_id, GOOD).status_code in (403, 404)
