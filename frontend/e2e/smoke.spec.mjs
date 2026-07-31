import { expect, test } from '@playwright/test';

/**
 * One pass through the lecture: author a question, run a session, have a
 * student follow it live, and check the pre/post distributions appear only
 * after each round is halted.
 *
 * Covers the two failures unit tests and a green build did not catch: the
 * projected QR code rendering at all, and a student's page updating over SSE
 * without a reload.
 */

async function login(page, username) {
  await page.goto('/login');
  await page.getByPlaceholder('e.g. lukask').fill(username);
  await page.getByRole('button', { name: 'Log in' }).click();
}

/**
 * Create a quiz with one question and return that quiz's section.
 *
 * Everything is scoped to the newly created section: other quizzes may already
 * exist (from an earlier spec sharing this server), and page-wide selectors
 * would then act on the wrong one.
 */
async function authorQuestion(page, quizTitle, questionText, choices) {
  await page.fill('input[name="title"]', quizTitle);
  await page.click('form.new-quiz button');

  // Locate by heading rather than by index or count: other quizzes may already
  // exist, and the dashboard loads them asynchronously.
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
  await quiz.locator('.add-q input[type="radio"]').first().check();
  await quiz.getByRole('button', { name: 'Save question' }).click();
  await expect(quiz.locator('ol li').first()).toContainText(questionText);
  return quiz;
}

test('teacher runs a session and a student follows it live', async ({ browser }) => {
  const teacherCtx = await browser.newContext();
  const teacher = await teacherCtx.newPage();

  await login(teacher, 'teacher');
  await teacher.waitForURL('**/teacher');

  const quiz = await authorQuestion(teacher, 'Smoke quiz', 'Which aligns locally?', [
    'Smith-Waterman',
    'Needleman-Wunsch',
  ]);

  await quiz.getByRole('button', { name: 'Run session' }).click();
  await teacher.waitForURL('**/teacher/session/**');

  // --- the projected QR code must actually render ---
  const qr = teacher.locator('img.qr');
  await expect(qr).toBeVisible();
  const qrState = await qr.evaluate((el) => ({
    width: el.naturalWidth,
    height: el.naturalHeight,
  }));
  // A broken image reports naturalWidth 0 — this is the check that a passing
  // build and passing unit tests both missed.
  expect(qrState.width, 'QR image failed to load (broken image)').toBeGreaterThan(0);
  expect(qrState.height).toBeGreaterThan(0);

  const qrResponse = await teacher.request.get(
    (await qr.getAttribute('src')) ?? '',
  );
  expect(qrResponse.status()).toBe(200);
  expect(qrResponse.headers()['content-type']).toContain('image/svg+xml');

  // The join URL must be a real address, not a placeholder.
  const joinUrl = (await teacher.locator('code.url').textContent())?.trim() ?? '';
  expect(joinUrl).toMatch(/^https?:\/\/.+\/s\/[a-z0-9]+$/);

  // --- a student joins and follows the session ---
  const studentCtx = await browser.newContext();
  const student = await studentCtx.newPage();
  const sessionPath = new URL(joinUrl).pathname;

  // The real journey: open the QR target cold, get sent to log in, come back.
  await student.goto(sessionPath);
  await student.waitForURL('**/login**');
  await student.getByPlaceholder('e.g. lukask').fill('student1');
  await student.getByRole('button', { name: 'Log in' }).click();
  await student.waitForURL('**' + sessionPath);
  await expect(student.locator('.waiting')).toBeVisible();

  // Reloading must not bounce an already-logged-in student back to login.
  await student.reload();
  await expect(student.locator('.waiting')).toBeVisible();
  expect(new URL(student.url()).pathname).toBe(sessionPath);

  // Teacher opens the first round; the student's page must update on its own.
  await teacher.getByRole('button', { name: 'Open 1st bout (pre)' }).click();
  await expect(
    student.locator('.qtext'),
    'student page did not update over SSE without a reload',
  ).toContainText('Which aligns locally?');

  // While the round is open the teacher sees a count, never that phase's
  // split — the teacher screen is the projected one, and showing the vote
  // before the discussion defeats the point of asking twice.
  await student.getByRole('button', { name: 'Needleman-Wunsch' }).click();
  await expect(teacher.locator('.status')).toContainText('1 answer');
  await expect(
    teacher.locator('.bar.pre'),
    'pre distribution was visible while the pre round was still open',
  ).toHaveCount(0);

  // Halt: now the distribution may be shown, one bar per choice.
  await teacher.getByRole('button', { name: 'Halt submission' }).click();
  await expect(teacher.locator('.status')).toContainText('CLOSED');
  await expect(teacher.locator('.bar.pre')).toHaveCount(2);

  // --- second round, after the "discussion" ---
  await teacher.getByRole('button', { name: 'Open 2nd bout (post)' }).click();
  await expect(student.locator('.qtext')).toContainText('Which aligns locally?');
  // The post split must stay hidden while the post round is open, even though
  // the pre bars are now on screen.
  await expect(teacher.locator('.bar.post')).toHaveCount(0);

  await student.getByRole('button', { name: 'Smith-Waterman' }).click();
  await expect(teacher.locator('.status')).toContainText('1 answer');

  await teacher.getByRole('button', { name: 'Halt submission' }).click();
  await expect(teacher.locator('.status')).toContainText('CLOSED');

  // Both phases now have bars, ready to compare.
  await expect(teacher.locator('.bar.pre')).toHaveCount(2);
  await expect(teacher.locator('.bar.post')).toHaveCount(2);

  // Answering is refused once the round is halted.
  await expect(student.locator('.waiting')).toBeVisible();

  await teacherCtx.close();
  await studentCtx.close();
});
