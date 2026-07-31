import { expect, test } from '@playwright/test';

/**
 * Question text is Markdown. What matters is that it arrives on a student's
 * phone as formatting and a visible figure, not as literal asterisks.
 */

// Smallest valid PNG: 1x1, transparent.
const PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';

test('markdown and an uploaded figure reach the student', async ({ browser }) => {
  const teacherCtx = await browser.newContext();
  const teacher = await teacherCtx.newPage();

  await teacher.goto('/login');
  await teacher.getByPlaceholder('e.g. lukask').fill('teacher');
  await teacher.getByRole('button', { name: 'Log in' }).click();
  await teacher.waitForURL('**/teacher');

  await teacher.fill('input[name="title"]', 'Markdown quiz');
  await teacher.click('form.new-quiz button');
  const quiz = teacher
    .locator('section.quiz')
    .filter({ has: teacher.getByRole('heading', { name: 'Markdown quiz', exact: true }) })
    .last();
  await quiz.locator('details summary').click();

  await quiz
    .locator('textarea[name="qtext"]')
    .fill('Which is **local**?\n\n- Smith-Waterman\n- Needleman-Wunsch');

  // The preview renders through the server, so it shows what students get.
  await expect(quiz.locator('.preview strong')).toHaveText('local');
  await expect(quiz.locator('.preview li')).toHaveCount(2);

  // Upload a figure; the Markdown for it is appended to the question.
  await quiz.locator('.upload input[type="file"]').setInputFiles({
    name: 'figure.png',
    mimeType: 'image/png',
    buffer: Buffer.from(PNG_BASE64, 'base64'),
  });
  await expect(quiz.locator('.preview img')).toHaveCount(1);
  await expect(quiz.locator('textarea[name="qtext"]')).toHaveValue(/!\[\]\(\/api\/images\//);

  const inputs = quiz.locator('.add-q input[placeholder="Choice text"]');
  await inputs.nth(0).fill('Smith-Waterman');
  await inputs.nth(1).fill('Needleman-Wunsch');
  await quiz.locator('.add-q input[type="radio"]').first().check();
  await quiz.getByRole('button', { name: 'Save question' }).click();
  await expect(quiz.locator('ol li strong').first()).toHaveText('local');

  await quiz.getByRole('button', { name: 'Run session' }).click();
  await teacher.waitForURL('**/join');
  await expect(teacher.locator('code.url')).toHaveText(/^https?:\/\/.+\/s\/[a-z0-9]+$/);
  const sessionPath = new URL(
    (await teacher.locator('code.url').textContent()).trim(),
  ).pathname;

  // --- what the student actually sees ---
  const student = await (await browser.newContext()).newPage();
  await student.goto(sessionPath);
  await student.waitForURL('**/login**');
  await student.getByPlaceholder('e.g. lukask').fill('anna');
  await student.getByRole('button', { name: 'Log in' }).click();
  await student.waitForURL('**' + sessionPath);

  await teacher.getByRole('link', { name: 'Control', exact: true }).click();
  await teacher.getByRole('button', { name: 'Open 1st bout (pre)' }).click();

  const qtext = student.locator('.qtext');
  await expect(qtext).toBeVisible();
  // Formatting, not literal asterisks.
  await expect(qtext.locator('strong')).toHaveText('local');
  await expect(qtext).not.toContainText('**local**');
  await expect(qtext.locator('li')).toHaveCount(2);

  // The figure must actually load, not merely be present in the markup.
  const figure = qtext.locator('img');
  await expect(figure).toHaveCount(1);
  const width = await figure.evaluate((el) => el.naturalWidth);
  expect(width, 'uploaded figure failed to load for the student').toBeGreaterThan(0);

  await teacherCtx.close();
});
