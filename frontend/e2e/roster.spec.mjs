import { expect, test } from '@playwright/test';

/**
 * The Canvas roster panel.
 *
 * The e2e backend has no Canvas token, which is the state every deployment
 * starts in — so what is asserted here is that the panel renders and tells the
 * teacher exactly what to do, rather than failing silently or showing an empty
 * control that does nothing when clicked.
 */
test('the roster panel explains how to configure Canvas', async ({ page }) => {
  await page.goto('/login');
  await page.getByPlaceholder('e.g. lukask').fill('teacher');
  await page.getByRole('button', { name: 'Log in' }).click();
  await page.waitForURL('**/teacher');

  const panel = page.locator('details.roster');
  await expect(panel).toBeVisible();

  // Nothing is fetched until it is opened.
  await panel.locator('summary').click();

  await expect(panel).toContainText('CANVAS_TOKEN');
  await expect(panel).toContainText('/profile/settings');
  // No sync control is offered while it cannot work.
  await expect(panel.getByRole('button', { name: /Sync roster/ })).toHaveCount(0);
});

test('an already-synced roster is still reported without a token', async ({ page }) => {
  /**
   * Removing the token after syncing is the recommended state — the roster
   * lives in the database, not in Canvas. Reporting only "not configured"
   * made a correctly set up deployment look broken, and gave the teacher no
   * way to see that their import had in fact worked.
   */
  await page.goto('/login');
  await page.getByPlaceholder('e.g. lukask').fill('teacher');
  await page.getByRole('button', { name: 'Log in' }).click();
  await page.waitForURL('**/teacher');

  const panel = page.locator('details.roster');
  await panel.locator('summary').click();

  // The seeded roster is visible even though Canvas is unreachable.
  const row = panel.locator('table.synced tr').nth(1);
  await expect(row).toBeVisible();
  await expect(row).toContainText('5');

  // And the wording does not imply nothing works.
  await expect(panel).toContainText('keeps working');
  await expect(panel).not.toContainText('No roster has been synced yet');
});

test('the roster panel never exposes the access token', async ({ page }) => {
  const bodies = [];
  page.on('response', async (res) => {
    if (res.url().includes('/api/roster')) {
      bodies.push(await res.text().catch(() => ''));
    }
  });

  await page.goto('/login');
  await page.getByPlaceholder('e.g. lukask').fill('teacher');
  await page.getByRole('button', { name: 'Log in' }).click();
  await page.waitForURL('**/teacher');
  await page.locator('details.roster summary').click();
  await expect(page.locator('details.roster')).toContainText('CANVAS_TOKEN');

  expect(bodies.length).toBeGreaterThan(0);
  for (const body of bodies) {
    // The status endpoint reports whether a token exists, never its value.
    expect(body).not.toMatch(/"canvas_token"/);
  }
});
