import { expect, test } from '@playwright/test';

/**
 * Roster identification, driven the way a student meets it: scan the QR,
 * land on the session, get sent to the login page, come back to the same
 * question.
 *
 * The e2e backend runs with ROSTER_LOGIN on and a known teacher password.
 * The teacher signs in with mock login (still enabled here) and syncs no
 * Canvas roster, so the student path is exercised against an *empty* roster
 * as well as a populated one.
 */

const TEACHER_PASSWORD = 'e2e-teacher-password';

test('the login page offers the roster form', async ({ page }) => {
  await page.goto('/login');
  await expect(page.getByLabel('Your KTH email address')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Continue' })).toBeVisible();

  // The class list is never rendered: a dropdown here would publish the
  // roster to anyone who opens the page.
  await expect(page.locator('select')).toHaveCount(0);
  await expect(page.locator('datalist')).toHaveCount(0);
});

test('someone not on the roster is refused, and told why', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Your KTH email address').fill('nobody@kth.se');
  await page.getByRole('button', { name: 'Continue' }).click();

  await expect(page.locator('.error')).toContainText('not on the course roster');
  expect(page.url()).toContain('/login');
});

test('a teacher signs in with the shared password and lands on the dashboard', async ({
  page,
}) => {
  await page.goto('/login');
  await page.getByLabel('Your KTH email address').fill('teacher@kth.se');

  // The password is required: teachers hold every student's participation.
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.locator('.error')).toContainText('teacher password');

  await page.getByLabel(/Teacher password/).fill(TEACHER_PASSWORD);
  await page.getByRole('button', { name: 'Continue' }).click();

  await page.waitForURL('**/teacher');
  await expect(page.getByRole('heading', { name: 'Your quizzes' })).toBeVisible();
});

test('a scanned session survives the trip through login', async ({ browser }) => {
  // Teacher sets up a session first.
  const tctx = await browser.newContext();
  const teacher = await tctx.newPage();
  await teacher.goto('/login');
  await teacher.getByLabel('Your KTH email address').fill('teacher@kth.se');
  await teacher.getByLabel(/Teacher password/).fill(TEACHER_PASSWORD);
  await teacher.getByRole('button', { name: 'Continue' }).click();
  await teacher.waitForURL('**/teacher');

  await teacher.fill('input[name="title"]', 'Roster login quiz');
  await teacher.click('form.new-quiz button');
  const quiz = teacher
    .locator('section.quiz')
    .filter({ has: teacher.getByRole('heading', { name: 'Roster login quiz', exact: true }) })
    .last();
  await quiz.locator('details').locator('summary').click();
  await quiz.locator('textarea[name="qtext"]').fill('Does it come back?');
  const choices = quiz.locator('input[placeholder="Choice text"]');
  await choices.nth(0).fill('Yes it does');
  await choices.nth(1).fill('No');
  await quiz.locator('input[type="radio"]').first().check();
  await quiz.getByRole('button', { name: 'Save question' }).click();
  await quiz.getByRole('button', { name: 'Run session' }).click();
  await teacher.waitForURL('**/teacher/session/*/join');
  const code = teacher.url().match(/session\/([a-z0-9]+)\//)[1];

  // A logged-out visitor scanning the QR is sent to login and brought back.
  const sctx = await browser.newContext();
  const student = await sctx.newPage();
  await student.goto(`/s/${code}`);
  await student.waitForURL('**/login**');
  expect(student.url(), 'the scanned session must be preserved').toContain(
    encodeURIComponent(`/s/${code}`),
  );

  await sctx.close();
  await tctx.close();
});
