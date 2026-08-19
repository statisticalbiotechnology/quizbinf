import { expect, test } from '@playwright/test';

/**
 * The type-ahead on the login field, and the one-identity-per-device rule.
 *
 * The e2e backend is seeded with a small roster (see seed_roster.py), so what
 * is asserted here is the behaviour a student actually meets: type a few
 * letters, pick yourself, and be unable to then sign in as a classmate on the
 * same phone.
 */

test('typing narrows the roster, and picking fills the field', async ({ page }) => {
  await page.goto('/login');
  const email = page.getByLabel('Your KTH email address');

  // Nothing is offered for one or two letters — the list is the class.
  await email.fill('sh');
  await page.waitForTimeout(500);
  await expect(page.locator('.suggestions li')).toHaveCount(0);

  await email.fill('shir');
  await expect(page.locator('.suggestions li')).toHaveCount(2);
  await expect(page.locator('.suggestions li').first()).toHaveText('shiraza@kth.se');

  await page.locator('.suggestions li').first().click();
  await expect(email).toHaveValue('shiraza@kth.se');
  await expect(page.locator('.suggestions li')).toHaveCount(0);
});

test('the arrow keys drive the list', async ({ page }) => {
  await page.goto('/login');
  const email = page.getByLabel('Your KTH email address');
  await email.fill('shir');
  await expect(page.locator('.suggestions li')).toHaveCount(2);

  await email.press('ArrowDown');
  await email.press('ArrowDown');
  await expect(page.locator('.suggestions li.active')).toHaveText('shirin@kth.se');

  await email.press('Enter');
  await expect(email).toHaveValue('shirin@kth.se');
});

test('a student signs in from the suggestion and stays signed in', async ({ page }) => {
  await page.goto('/login');
  const email = page.getByLabel('Your KTH email address');
  await email.fill('sofia');
  await page.locator('.suggestions li').first().click();
  await page.getByRole('button', { name: 'Continue' }).click();

  await page.waitForURL((url) => !url.pathname.startsWith('/login'));
  await expect(page.locator('header .who')).toContainText('Sofia Ali');
});

test('one device cannot then sign in as a classmate', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Your KTH email address').fill('ahmaa@kth.se');
  await page.getByRole('button', { name: 'Continue' }).click();
  await page.waitForURL((url) => !url.pathname.startsWith('/login'));

  // Sign out and try to become someone else on the same browser.
  await page.getByText('log out').click();
  await page.waitForURL('**/login**');

  await page.getByLabel('Your KTH email address').fill('linaah2@kth.se');
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.locator('.error')).toContainText('already been used');
});
