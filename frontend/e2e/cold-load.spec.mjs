import { expect, test } from '@playwright/test';

/**
 * Opening a teacher URL cold — a reload, a bookmark, or the second window used
 * for projecting — must not bounce a logged-in teacher to the login page.
 *
 * The current user is fetched asynchronously, so a route guard that reads it
 * synchronously loses a race it cannot see on localhost, where the lookup
 * returns in under a millisecond. The delay below reproduces a real network,
 * which is where this actually bit.
 */
test('a teacher can reload a session view on a slow connection', async ({ browser }) => {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();

  await page.goto('/login');
  await page.getByPlaceholder('e.g. lukask').fill('teacher');
  await page.getByRole('button', { name: 'Log in' }).click();
  await page.waitForURL('**/teacher');

  await page.fill('input[name="title"]', 'Cold load quiz');
  await page.click('form.new-quiz button');
  const quiz = page
    .locator('section.quiz')
    .filter({ has: page.getByRole('heading', { name: 'Cold load quiz', exact: true }) })
    .last();
  await quiz.locator('details summary').click();
  await quiz.locator('textarea[name="qtext"]').fill('Cold?');
  const inputs = quiz.locator('.add-q input[placeholder="Choice text"]');
  await inputs.nth(0).fill('yes');
  await inputs.nth(1).fill('no');
  await quiz.locator('.add-q input[type="radio"]').first().check();
  await quiz.getByRole('button', { name: 'Save question' }).click();
  await quiz.getByRole('button', { name: 'Run session' }).click();
  await page.waitForURL('**/teacher/session/*/join');
  const sessionUrl = page.url();

  // Make "who am I?" take as long as it would over a real connection.
  await page.route('**/api/auth/me', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 700));
    await route.continue();
  });

  for (const target of [sessionUrl, sessionUrl.replace('/join', '/control'), '/teacher']) {
    await page.goto(target);
    await page.waitForTimeout(1500);
    expect(
      page.url(),
      `cold load of ${target} bounced the teacher to the login page`,
    ).not.toContain('/login');
  }

  // And the view is usable, not showing a stale error.
  await page.goto(sessionUrl);
  await expect(page.locator('img.qr')).toBeVisible();
  await expect(page.locator('.error')).toHaveCount(0);

  await ctx.close();
});
