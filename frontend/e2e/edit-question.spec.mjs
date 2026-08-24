import { expect, test } from '@playwright/test';

/**
 * Editing and removing questions from the dashboard.
 *
 * The rules worth seeing in a browser rather than only in a unit test: an
 * edit reaches the student's screen, an unused question can be deleted, and
 * one that has already been asked refuses — with the reason shown next to it
 * rather than swallowed.
 */

async function loginAs(page, username) {
  await page.goto('/login');
  await page.getByPlaceholder('e.g. lukask').fill(username);
  await page.getByRole('button', { name: 'Log in' }).click();
}

/** The add form. Two editors can be on the page at once, so scope tightly. */
function addForm(quiz) {
  return quiz.locator('details app-question-editor');
}

/** The inline edit form, which lives in the question list. */
function editForm(quiz) {
  return quiz.locator('ol app-question-editor');
}

async function openAddForm(quiz) {
  const details = quiz.locator('details');
  if (!(await details.evaluate((d) => d.open))) {
    await details.locator('summary').click();
  }
  return addForm(quiz);
}

async function addQuestion(quiz, text, choices) {
  const form = await openAddForm(quiz);
  await form.locator('textarea').fill(text);
  const inputs = form.locator('input[placeholder="Choice text"]');
  for (let i = 0; i < choices.length; i++) {
    if (i >= 2) await form.getByRole('button', { name: '+ choice' }).click();
    await inputs.nth(i).fill(choices[i]);
  }
  await form.locator('input[type="radio"]').first().check();
  await form.getByRole('button', { name: 'Save question' }).click();
  await expect(quiz.getByText(text)).toBeVisible();
}

async function authorQuestion(page, quizTitle, text, choices) {
  await page.fill('input[name="title"]', quizTitle);
  await page.click('form.new-quiz button');
  const quiz = page
    .locator('section.quiz')
    .filter({ has: page.getByRole('heading', { name: quizTitle, exact: true }) })
    .last();
  await addQuestion(quiz, text, choices);
  return quiz;
}

test('a question can be edited and the change reaches students', async ({ browser }) => {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await loginAs(page, 'teacher');
  await page.waitForURL('**/teacher');

  const quiz = await authorQuestion(page, 'Editable quiz', 'Orignal typo here', [
    'Alpha',
    'Beta',
  ]);

  await quiz.getByRole('button', { name: 'Edit' }).click();
  const editor = editForm(quiz);
  await expect(editor.locator('textarea[name="qtext"]')).toHaveValue('Orignal typo here');

  // The edit form is the same tool as the add form: preview included.
  await editor.locator('textarea').fill('Corrected **wording** here');
  await expect(editor.locator('.preview strong')).toHaveText('wording');

  // Reword a choice, and move which one is correct.
  await editor.locator('input[placeholder="Choice text"]').nth(1).fill('Beta (reworded)');
  await editor.locator('input[type="radio"]').nth(1).check();
  await editor.getByRole('button', { name: 'Save changes' }).click();

  await expect(quiz.getByRole('button', { name: 'Save changes' })).toHaveCount(0);
  await expect(quiz.getByText('Corrected wording here')).toBeVisible();
  // The dashboard is on the teacher's screen while the projector is being set
  // up, so nothing is highlighted until it is asked for.
  await expect(quiz.locator('li.correct')).toHaveCount(0);
  await quiz.getByRole('button', { name: 'Show which is correct' }).click();
  // The highlighted choice is the one just marked correct, not the first.
  await expect(quiz.locator('li.correct')).toHaveText('Beta (reworded)');
  await quiz.getByRole('button', { name: 'Hide the answer' }).click();
  await expect(quiz.locator('li.correct')).toHaveCount(0);

  // A student sees the corrected text, not the typo.
  await quiz.getByRole('button', { name: 'Run session' }).click();
  await page.waitForURL('**/teacher/session/*/join');
  const code = page.url().match(/session\/([a-z0-9]+)\//)[1];
  await page.goto(page.url().replace('/join', '/control'));
  await page.getByRole('button', { name: 'Open 1st bout (pre)' }).click();

  const sctx = await browser.newContext();
  const student = await sctx.newPage();
  await loginAs(student, 'editstudent');
  await student.goto(`/s/${code}`);
  await expect(student.getByText('Corrected wording here')).toBeVisible({ timeout: 10000 });
  await expect(student.getByRole('button', { name: 'Beta (reworded)' })).toBeVisible();

  await sctx.close();
  await ctx.close();
});

test('an unused question can be deleted, an asked one cannot', async ({ browser }) => {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  page.on('dialog', (d) => d.accept());
  await loginAs(page, 'teacher');
  await page.waitForURL('**/teacher');

  const quiz = await authorQuestion(page, 'Deletable quiz', 'Throwaway question', [
    'Yes',
    'No',
  ]);

  // Never asked: it goes.
  await quiz.getByRole('button', { name: 'Delete' }).click();
  await expect(quiz.getByText('Throwaway question')).toHaveCount(0);

  // Now one that gets asked and answered.
  await addQuestion(quiz, 'Question that gets used', ['First', 'Second']);

  await quiz.getByRole('button', { name: 'Run session' }).click();
  await page.waitForURL('**/teacher/session/*/join');
  await page.goto(page.url().replace('/join', '/control'));
  await page.getByRole('button', { name: 'Open 1st bout (pre)' }).click();
  await page.getByRole('button', { name: 'Halt submission' }).click();

  await page.goto('/teacher');
  const used = page
    .locator('section.quiz')
    .filter({ has: page.getByRole('heading', { name: 'Deletable quiz', exact: true }) })
    .last();
  await used.getByRole('button', { name: 'Delete' }).click();

  // Refused, and the reason is shown against the question.
  await expect(used.locator('.error')).toContainText('already been asked');
  await expect(used.getByText('Question that gets used')).toBeVisible();

  await ctx.close();
});
