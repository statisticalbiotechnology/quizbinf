import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

/**
 * How big a figure actually draws.
 *
 * Only a browser can answer this. Every view had a CSS rule capping question
 * figures and not one of them applied: question Markdown is bound with
 * [innerHTML], so its elements are created outside the component template and
 * carry none of Angular's style-scoping attributes, which turns `.qtext img`
 * into `img[_ngcontent-xxx]` — a selector that matches nothing. A 1257px-wide
 * figure rendered at 1257px on a 390px phone, with the caps apparently in
 * place and every unit test green.
 *
 * So these measure pixels rather than assert that a rule exists.
 */

const FIXTURE = fileURLToPath(new URL('./fig.png', import.meta.url));

async function loginAs(page, username) {
  await page.goto('/login');
  await page.getByPlaceholder('e.g. lukask').fill(username);
  await page.getByRole('button', { name: 'Log in' }).click();
}

/** Upload the fixture and return the URL the question should reference. */
async function uploadFigure(page) {
  const response = await page.request.post('/api/images', {
    multipart: {
      file: { name: 'fig.png', mimeType: 'image/png', buffer: readFileSync(FIXTURE) },
    },
  });
  expect(response.status()).toBe(201);
  return (await response.json()).url;
}

async function authorQuestionWithFigure(page, title, text) {
  await page.fill('input[name="title"]', title);
  await page.click('form.new-quiz button');
  const quiz = page
    .locator('section.quiz')
    .filter({ has: page.getByRole('heading', { name: title, exact: true }) })
    .last();
  await quiz.locator('details summary').click();
  await quiz.locator('textarea[name="qtext"]').fill(text);
  const inputs = quiz.locator('.add-q input[placeholder="Choice text"]');
  await inputs.nth(0).fill('6');
  await inputs.nth(1).fill('4');
  await quiz.locator('.add-q input[type="radio"]').first().check();
  await quiz.getByRole('button', { name: 'Save question' }).click();
  return quiz;
}

/** Width of an element, and of the block it sits in. */
async function widths(locator) {
  return locator.evaluate((img) => ({
    drawn: img.getBoundingClientRect().width,
    column: img.parentElement.getBoundingClientRect().width,
  }));
}

test('an oversized figure is kept inside its column everywhere', async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 1200, height: 900 } });
  const teacher = await ctx.newPage();
  await loginAs(teacher, 'teacher');
  await teacher.waitForURL('**/teacher');

  const url = await uploadFigure(teacher);
  // The fixture is 1257px wide — wider than any column in the app.
  const quiz = await authorQuestionWithFigure(
    teacher,
    'Figure quiz',
    `Score this alignment.\n\n![](${url})`,
  );

  const onDashboard = await widths(quiz.locator('ol li img').first());
  expect(onDashboard.drawn).toBeLessThanOrEqual(onDashboard.column + 1);
  expect(onDashboard.drawn).toBeLessThan(1257);

  await quiz.getByRole('button', { name: 'Run session' }).click();
  await teacher.waitForURL('**/teacher/session/*/join');
  // The join URL arrives from its own request; reading it too early gives "".
  await expect(teacher.locator('code.url')).toContainText('/s/');
  const joinUrl = (await teacher.locator('code.url').textContent()).trim();

  const projected = await widths(teacher.locator('app-question-panel img').first());
  expect(projected.drawn).toBeLessThanOrEqual(projected.column + 1);

  // …and on a phone, which is where an uncapped figure is worst.
  const phoneCtx = await browser.newContext({ viewport: { width: 390, height: 780 } });
  const student = await phoneCtx.newPage();
  await loginAs(student, 'figstudent');
  await student.goto(new URL(joinUrl).pathname);

  await teacher.getByRole('link', { name: 'Control', exact: true }).click();
  await teacher.getByRole('button', { name: 'Open 1st bout (pre)' }).click();

  const onPhone = student.locator('.qtext img').first();
  await expect(onPhone).toBeVisible({ timeout: 10000 });
  const phone = await widths(onPhone);
  expect(phone.drawn).toBeLessThanOrEqual(phone.column + 1);
  expect(phone.drawn, 'the figure overflowed the phone screen').toBeLessThan(390);

  await ctx.close();
  await phoneCtx.close();
});

test('a percentage in the Markdown sizes the figure against its column', async ({
  browser,
}) => {
  const ctx = await browser.newContext({ viewport: { width: 1200, height: 900 } });
  const teacher = await ctx.newPage();
  await loginAs(teacher, 'teacher');
  await teacher.waitForURL('**/teacher');

  const url = await uploadFigure(teacher);
  const quiz = await authorQuestionWithFigure(
    teacher,
    'Sized figure quiz',
    `Score this alignment.\n\n![](${url}){width=50%}`,
  );

  // The attribute has to survive Angular's own sanitiser pass on [innerHTML],
  // which is the step that would break this silently.
  const img = quiz.locator('ol li img').first();
  await expect(img).toHaveAttribute('width', '50%');

  const { drawn, column } = await widths(img);
  expect(drawn).toBeGreaterThan(column * 0.45);
  expect(drawn).toBeLessThan(column * 0.55);

  await ctx.close();
});
