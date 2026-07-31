import { expect, test } from '@playwright/test';

/**
 * Regression: the teacher view used to find a session's questions by matching
 * the quiz *title*, so two quizzes sharing a name left the teacher looking at
 * the wrong quiz — no round controls, and no way to start collecting answers
 * mid-lecture. The session state now carries the quiz id.
 */
test('round controls appear when two quizzes share a title', async ({ browser }) => {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();

  await page.goto('/login');
  await page.getByPlaceholder('e.g. lukask').fill('teacher');
  await page.getByRole('button', { name: 'Log in' }).click();
  await page.waitForURL('**/teacher');

  // First quiz with this title — deliberately left without questions.
  await page.fill('input[name="title"]', 'Lecture 3');
  await page.click('form.new-quiz button');
  await page.waitForSelector('section.quiz');

  // Second quiz, same title, with the question we actually want to run.
  await page.fill('input[name="title"]', 'Lecture 3');
  await page.click('form.new-quiz button');
  await expect(page.locator('section.quiz')).toHaveCount(2);

  const second = page.locator('section.quiz').nth(1);
  await second.locator('details summary').click();
  await second.locator('textarea[name="qtext"]').fill('Which aligns locally?');
  const inputs = second.locator('.add-q input[placeholder="Choice text"]');
  await inputs.nth(0).fill('Smith-Waterman');
  await inputs.nth(1).fill('Needleman-Wunsch');
  await second.locator('.add-q input[type="radio"]').first().check();
  await second.getByRole('button', { name: 'Save question' }).click();
  await expect(second.locator('ol li').first()).toContainText('Which aligns locally?');

  await second.getByRole('button', { name: 'Run session' }).click();
  await page.waitForURL('**/teacher/session/*/join');

  // Round controls live on the Control view.
  await page.getByRole('link', { name: 'Control', exact: true }).click();
  await page.waitForURL('**/control');

  await expect(
    page.getByRole('button', { name: 'Open 1st bout (pre)' }),
    'teacher cannot start collecting answers: no round controls rendered',
  ).toBeVisible();

  await ctx.close();
});
