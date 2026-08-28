import { expect, test } from '@playwright/test';

/**
 * The things a teacher does with the app in front of a class: put the
 * questions in the order they will be asked, drive one question without the
 * rest of the quiz in the way, keep a way in on screen for whoever failed to
 * log in, and hand the questions out afterwards.
 */

async function loginAs(page, username) {
  await page.goto('/login');
  await page.getByPlaceholder('e.g. lukask').fill(username);
  await page.getByRole('button', { name: 'Log in' }).click();
}

async function newQuiz(page, title) {
  await page.fill('input[name="title"]', title);
  await page.click('form.new-quiz button');
  const quiz = page
    .locator('section.quiz')
    .filter({ has: page.getByRole('heading', { name: title, exact: true }) })
    .last();
  await expect(quiz).toBeVisible();
  return quiz;
}

async function addQuestion(quiz, text, choices) {
  // Only open the form if it is closed: clicking the summary a second time
  // collapses it, and the fill below then waits on a hidden textarea.
  const details = quiz.locator('details').first();
  if (!(await details.evaluate((d) => d.open))) {
    await details.locator('summary').click();
  }
  await quiz.locator('textarea[name="qtext"]').fill(text);
  const inputs = quiz.locator('.add-q input[placeholder="Choice text"]');
  for (let i = 0; i < choices.length; i++) await inputs.nth(i).fill(choices[i]);
  await quiz.locator('.add-q input[type="radio"]').first().check();
  await quiz.getByRole('button', { name: 'Save question' }).click();
  // Markdown is rendered, so match the drawn text rather than the source.
  const rendered = text.replace(/\*\*/g, '');
  await expect(quiz.getByText(rendered, { exact: false }).first()).toBeVisible();
}

/** The question text of each row of a quiz's list, in order. */
function questionTexts(quiz) {
  return quiz.locator('ol > li .q-row .qtext').allTextContents();
}

test('questions can be put in the order they will be asked', async ({ browser }) => {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await loginAs(page, 'teacher');
  await page.waitForURL('**/teacher');

  const quiz = await newQuiz(page, 'Reorder quiz');
  await addQuestion(quiz, 'Question alpha', ['a', 'b']);
  await addQuestion(quiz, 'Question beta', ['a', 'b']);
  await addQuestion(quiz, 'Question gamma', ['a', 'b']);

  const rows = quiz.locator('ol > li');
  // The first cannot move up, the last cannot move down.
  await expect(rows.nth(0).getByTitle('Ask this one earlier')).toBeDisabled();
  await expect(rows.nth(2).getByTitle('Ask this one later')).toBeDisabled();

  await rows.nth(2).getByTitle('Ask this one earlier').click();
  await expect
    .poll(() => questionTexts(quiz))
    .toEqual([
      expect.stringContaining('alpha'),
      expect.stringContaining('gamma'),
      expect.stringContaining('beta'),
    ]);

  // The order is stored, not just rearranged on screen.
  await page.reload();
  const reloaded = page
    .locator('section.quiz')
    .filter({ has: page.getByRole('heading', { name: 'Reorder quiz', exact: true }) })
    .last();
  await expect
    .poll(() => questionTexts(reloaded))
    .toEqual([
      expect.stringContaining('alpha'),
      expect.stringContaining('gamma'),
      expect.stringContaining('beta'),
    ]);

  await ctx.close();
});

test('the control view narrows to the live question and lists its choices', async ({
  browser,
}) => {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await loginAs(page, 'teacher');
  await page.waitForURL('**/teacher');

  const quiz = await newQuiz(page, 'Control quiz');
  await addQuestion(quiz, 'Which aligns locally', ['Smith-Waterman', 'Needleman-Wunsch']);
  await addQuestion(quiz, 'Which aligns globally', ['Needleman-Wunsch', 'Smith-Waterman']);

  await quiz.getByRole('button', { name: 'Run session ▶' }).click();
  await page.waitForURL('**/teacher/session/*/join');
  await page.getByRole('link', { name: 'Control', exact: true }).click();

  // Nothing open yet: the whole quiz, so the teacher can pick.
  await expect(page.locator('section.q')).toHaveCount(2);

  // The alternatives are on this screen too, unmarked until asked for.
  const first = page.locator('section.q').first();
  await expect(first.locator('.choices li')).toHaveText([
    'Smith-Waterman',
    'Needleman-Wunsch',
  ]);
  await expect(first.locator('.choices li.correct')).toHaveCount(0);
  await first.getByRole('button', { name: 'Show which is correct' }).click();
  await expect(first.locator('.choices li.correct')).toHaveText('Smith-Waterman');

  // Open a bout: only that question remains, and it is marked as the live one.
  await first.getByRole('button', { name: 'Open 1st bout (pre)' }).click();
  await expect(page.locator('section.q')).toHaveCount(1);
  await expect(page.locator('section.q.live')).toHaveCount(1);
  await expect(page.locator('section.q')).toContainText('Which aligns locally');

  // The rest of the quiz is one click away, and the way back with it.
  await page.getByRole('button', { name: 'Show all 2 questions' }).click();
  await expect(page.locator('section.q')).toHaveCount(2);
  await page.getByRole('button', { name: 'Show only the live question' }).click();
  await expect(page.locator('section.q')).toHaveCount(1);

  // Halted: back to the full list, since the teacher is choosing again.
  await page.getByRole('button', { name: 'Halt submission' }).click();
  await expect(page.locator('section.q')).toHaveCount(2);

  await ctx.close();
});

test('a way in stays on screen once the questions have started', async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 1200, height: 900 } });
  const page = await ctx.newPage();
  await loginAs(page, 'teacher');
  await page.waitForURL('**/teacher');

  const quiz = await newQuiz(page, 'QR quiz');
  await addQuestion(quiz, 'Which aligns locally', ['a', 'b']);
  await quiz.getByRole('button', { name: 'Run session ▶' }).click();
  await page.waitForURL('**/teacher/session/*/join');

  // Recruiting: the code owns the screen.
  const qr = page.locator('img.qr');
  await expect(qr).toBeVisible();
  const big = await qr.boundingBox();
  expect(big.width).toBeGreaterThan(300);
  // It must actually be an image, not a broken one.
  expect(await qr.evaluate((el) => el.naturalWidth)).toBeGreaterThan(0);

  await page.getByRole('link', { name: 'Control', exact: true }).click();
  await page.getByRole('button', { name: 'Open 1st bout (pre)' }).click();
  await page.getByRole('link', { name: 'Join', exact: true }).click();

  // Running: the question leads, but the code is still there for a latecomer.
  const small = await page.locator('img.qr').boundingBox();
  expect(small.width).toBeLessThan(big.width);
  expect(small.width).toBeGreaterThan(80);
  await expect(page.locator('app-question-panel')).toContainText('Which aligns locally');

  await ctx.close();
});

test('the questions can be downloaded as study material', async ({ browser }) => {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await loginAs(page, 'teacher');
  await page.waitForURL('**/teacher');

  const quiz = await newQuiz(page, 'Handout quiz');
  await addQuestion(quiz, 'Which aligns **locally**', ['Smith-Waterman', 'Needleman-Wunsch']);
  await quiz.getByRole('button', { name: 'Run session ▶' }).click();
  await page.waitForURL('**/teacher/session/*/join');
  await page.getByRole('link', { name: 'Report', exact: true }).click();

  const html = page.getByRole('link', { name: 'HTML' });
  await expect(html).toBeVisible();

  // Follow the link the page actually offers, rather than a URL built here.
  const withAnswers = await page.request.get(await html.getAttribute('href'));
  expect(withAnswers.status()).toBe(200);
  const body = await withAnswers.text();
  expect(body).toContain('<strong>locally</strong>');
  expect(body).toContain('class="correct"');

  // Change detection is event-coalesced, so wait for the href to catch up
  // rather than reading it in the same tick as the click.
  await page.getByLabel('with answers').uncheck();
  await expect(html).toHaveAttribute('href', /answers=false/);

  const without = await page.request.get(await html.getAttribute('href'));
  expect(without.status()).toBe(200);
  expect(await without.text()).not.toContain('class="correct"');

  await ctx.close();
});
