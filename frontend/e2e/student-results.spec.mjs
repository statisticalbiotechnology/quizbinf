import { expect, test } from '@playwright/test';

/**
 * What a student's phone shows once a question is finished.
 *
 * Nothing until the second bout is halted, since the bars between the bouts
 * would steer the discussion; after it, the class's distribution before and
 * after, the student's own two answers, and the correct choice.
 */

async function loginAs(page, username) {
  await page.goto('/login');
  await page.getByPlaceholder('e.g. lukask').fill(username);
  await page.getByRole('button', { name: 'Log in' }).click();
}

async function authorQuestion(page, quizTitle, questionText, choices) {
  await page.fill('input[name="title"]', quizTitle);
  await page.click('form.new-quiz button');
  const quiz = page
    .locator('section.quiz')
    .filter({ has: page.getByRole('heading', { name: quizTitle, exact: true }) })
    .last();
  await expect(quiz).toBeVisible();
  await quiz.locator('details summary').click();
  await quiz.locator('textarea[name="qtext"]').fill(questionText);
  const inputs = quiz.locator('.add-q input[placeholder="Choice text"]');
  for (let i = 0; i < choices.length; i++) {
    await inputs.nth(i).fill(choices[i]);
  }
  // First choice is the correct one.
  await quiz.locator('.add-q .choice-row input[type="radio"]').first().check();
  await quiz.getByRole('button', { name: 'Save question' }).click();
  await expect(quiz.locator('ol li').first()).toContainText(questionText);
  return quiz;
}

test('a phone shows the finished question, and nothing before it is finished', async ({
  browser,
}) => {
  const teacherCtx = await browser.newContext();
  const teacher = await teacherCtx.newPage();
  await loginAs(teacher, 'teacher');
  await teacher.waitForURL('**/teacher');

  const quiz = await authorQuestion(teacher, 'Phone results quiz', 'Which is local?', [
    'Smith-Waterman',
    'Needleman-Wunsch',
  ]);
  await quiz.getByRole('button', { name: 'Run session' }).click();
  await teacher.waitForURL('**/teacher/session/*/join');
  await expect(teacher.locator('code.url')).toContainText('/s/');
  const joinUrl = (await teacher.locator('code.url').textContent())?.trim() ?? '';
  const sessionPath = new URL(joinUrl).pathname;

  const phoneCtx = await browser.newContext({ viewport: { width: 390, height: 780 } });
  const student = await phoneCtx.newPage();
  await loginAs(student, 'resultstudent');
  await student.goto(sessionPath);
  await expect(student.locator('.waiting')).toBeVisible();

  const quietCtx = await browser.newContext({ viewport: { width: 390, height: 780 } });
  const quiet = await quietCtx.newPage();
  await loginAs(quiet, 'resultquiet');
  await quiet.goto(sessionPath);
  await expect(quiet.locator('.waiting')).toBeVisible();

  const goto = (name) => teacher.getByRole('link', { name, exact: true }).click();
  await goto('Control');

  await teacher.getByRole('button', { name: 'Open 1st bout (pre)' }).click();
  await student.getByRole('button', { name: 'Needleman-Wunsch' }).click();
  await expect(student.locator('.saved')).toBeVisible();
  await teacher.getByRole('button', { name: 'Halt submission' }).click();
  await expect(student.locator('.waiting')).toBeVisible();
  // Long enough for the spread-out fetch to have happened, had it shown anything.
  await student.waitForTimeout(3000);
  await expect(
    student.locator('.results'),
    'the bars reached the phone before the discussion',
  ).toHaveCount(0);

  await teacher.getByRole('button', { name: 'Open 2nd bout (post)' }).click();
  await student.getByRole('button', { name: 'Smith-Waterman' }).click();
  await expect(student.locator('.saved')).toBeVisible();
  await teacher.getByRole('button', { name: 'Halt submission' }).click();

  const result = student.locator('.result');
  await expect(result).toHaveCount(1, { timeout: 10000 });
  await expect(result.locator('summary')).toContainText('Which is local?');
  const rows = result.locator('.rchoice');
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toHaveClass(/correct/);
  await expect(rows.nth(0).locator('.tick')).toHaveCount(1);
  await expect(rows.nth(1).locator('.tick')).toHaveCount(0);
  await expect(rows.nth(0).locator('.mine')).toHaveText('you, after discussion');
  await expect(rows.nth(1).locator('.mine')).toHaveText('you, first');
  await expect(rows.nth(0).locator('.bar.pre b')).toHaveText('0%');
  await expect(rows.nth(0).locator('.bar.post b')).toHaveText('100%');
  await expect(rows.nth(1).locator('.bar.pre b')).toHaveText('100%');

  // A student who did not answer still sees the class, and is told so.
  await expect(quiet.locator('.result')).toHaveCount(1, { timeout: 10000 });
  await expect(quiet.locator('.result .mine')).toHaveCount(0);
  await expect(quiet.locator('.result .note')).toContainText('did not answer');

  await teacherCtx.close();
  await phoneCtx.close();
  await quietCtx.close();
});
