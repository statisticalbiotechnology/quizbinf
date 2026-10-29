"""Formulas, and Markdown in the choices.

The server marks a formula and leaves drawing it to the browser, so what is
pinned here is the marking, what the sanitiser lets through around it, and
that a choice now goes through the same renderer as its question.
"""

from app.markdown import render, render_inline
from tests.conftest import make_quiz_with_question


def test_a_formula_is_marked_for_the_browser():
    assert render("Energy $E=mc^2$ here") == (
        '<p>Energy <span class="math inline">E=mc^2</span> here</p>'
    )
    assert render("$$\n\\sum_i x_i\n$$") == '<div class="math block">\n\\sum_i x_i\n</div>'


def test_money_is_not_a_formula():
    assert render("It costs $5 and $10") == "<p>It costs $5 and $10</p>"


def test_tex_inside_a_formula_is_text_not_markup():
    """`<` in a formula must stay a character, never become a tag."""
    html = render("$a<b$ and $x<script>alert(1)</script>$")
    assert "<script" not in html
    assert '<span class="math inline">a&lt;b</span>' in html


def test_no_other_class_or_element_gets_through():
    """span and div are allowed only as formula carriers: raw HTML is escaped
    by the parser, and any class but the math ones is dropped."""
    html = render('<span class="evil">x</span>')
    assert "<span" not in html
    assert "&lt;span" in html


def test_a_one_line_choice_renders_without_a_paragraph():
    assert render_inline("**SW** with $O(nm)$") == (
        '<strong>SW</strong> with <span class="math inline">O(nm)</span>'
    )
    # A display formula is a block and stays one.
    assert render_inline("$$\nx^2\n$$").startswith('<div class="math block">')


def test_choices_carry_rendered_markdown_to_students(teacher_client, student_client):
    quiz = teacher_client.post("/api/quizzes", json={"title": "Maths"}).json()
    q = teacher_client.post(
        f"/api/quizzes/{quiz['id']}/questions",
        json={
            "text": "Which is the score?",
            "choices": [
                {"text": "$\\log_2(p/q)$", "is_correct": True},
                {"text": "![](/api/images/abc.png){width=40%}"},
            ],
        },
    ).json()
    assert q["choices"][0]["text_html"] == '<span class="math inline">\\log_2(p/q)</span>'

    code = teacher_client.post(f"/api/sessions?quiz_id={quiz['id']}").json()["code"]
    teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": q["id"], "phase": "pre"}
    )
    state = student_client.get(f"/api/sessions/{code}/state").json()
    choices = state["question"]["choices"]
    assert '<img src="/api/images/abc.png"' in choices[1]["text_html"]
    assert 'width="40%"' in choices[1]["text_html"]
    # Still nothing that says which is right.
    assert all("is_correct" not in c for c in choices)


def test_the_html_handout_renders_choices(teacher_client):
    quiz_id, _, _ = make_quiz_with_question(teacher_client)
    teacher_client.post(
        f"/api/quizzes/{quiz_id}/questions",
        json={"text": "Pick", "choices": [
            {"text": "**bold** one", "is_correct": True}, {"text": "plain"}]},
    )
    body = teacher_client.get(f"/api/quizzes/{quiz_id}/export.html").text
    assert "<strong>bold</strong> one" in body
    assert "**bold**" not in body
