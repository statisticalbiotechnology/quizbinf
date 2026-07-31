import { expect, test } from '@playwright/test';

/**
 * Authoring must not guess which answer is correct, and the teacher must be
 * able to see afterwards who got it right.
 */
test('correct answer is chosen deliberately, and participation is reported', async ({
  browser,
}) => {
  const teacherCtx = await browser.newContext();
  const teacher = await teacherCtx.newPage();

  await teacher.goto('/login');
  await teacher.getByPlaceholder('e.g. lukask').fill('teacher');
  await teacher.getByRole('button', { name: 'Log in' }).click();
  await teacher.waitForURL('**/teacher');

  await teacher.fill('input[name="title"]', 'Participation quiz');
  await teacher.click('form.new-quiz button');
  const quiz = teacher
    .locator('section.quiz')
    .filter({ has: teacher.getByRole('heading', { name: 'Participation quiz', exact: true }) })
    .last();
  await quiz.locator('details summary').click();
  await quiz.locator('textarea[name="qtext"]').fill('Which aligns locally?');
  const inputs = quiz.locator('.add-q input[placeholder="Choice text"]');
  await inputs.nth(0).fill('Smith-Waterman');
  await inputs.nth(1).fill('Needleman-Wunsch');

  // --- nothing is marked correct until the teacher says so ---
  const marks = quiz.locator('.add-q input[type="radio"]');
  expect(await marks.nth(0).isChecked(), 'first choice was pre-selected as correct').toBe(
    false,
  );
  expect(await marks.nth(1).isChecked()).toBe(false);
  const save = quiz.getByRole('button', { name: 'Save question' });
  await expect(save).toBeDisabled();
  await expect(quiz.locator('.hint')).toContainText('Mark which choice is correct');

  // Mark the *second* choice, to prove the marking is not positional.
  await marks.nth(1).check();
  await expect(save).toBeEnabled();
  // Put it back on the first, which is the answer we will grade against.
  await marks.nth(0).check();
  await save.click();
  await expect(quiz.locator('ol li').first()).toContainText('Which aligns locally?');

  await quiz.getByRole('button', { name: 'Run session' }).click();
  await teacher.waitForURL('**/join');
  // The join URL is fetched after the view renders; wait for it before parsing.
  await expect(teacher.locator('code.url')).toHaveText(/^https?:\/\/.+\/s\/[a-z0-9]+$/);
  const joinUrl = (await teacher.locator('code.url').textContent()).trim();
  const sessionPath = new URL(joinUrl).pathname;

  // --- two students, one right and one wrong ---
  const students = {};
  for (const name of ['anna', 'bo']) {
    const page = await (await browser.newContext()).newPage();
    await page.goto(sessionPath);
    await page.waitForURL('**/login**');
    await page.getByPlaceholder('e.g. lukask').fill(name);
    await page.getByRole('button', { name: 'Log in' }).click();
    await page.waitForURL('**' + sessionPath);
    students[name] = page;
  }

  const goto = (name) => teacher.getByRole('link', { name, exact: true }).click();
  await goto('Control');
  await teacher.getByRole('button', { name: 'Open 1st bout (pre)' }).click();
  await students['anna'].getByRole('button', { name: 'Needleman-Wunsch' }).click();
  await students['bo'].getByRole('button', { name: 'Smith-Waterman' }).click();
  await teacher.getByRole('button', { name: 'Halt submission' }).click();
  await expect(teacher.locator('.status')).toContainText('CLOSED');

  await teacher.getByRole('button', { name: 'Open 2nd bout (post)' }).click();
  // Anna changes her mind after the discussion; Bo stays right.
  await students['anna'].getByRole('button', { name: 'Smith-Waterman' }).click();
  await students['bo'].getByRole('button', { name: 'Smith-Waterman' }).click();
  await teacher.getByRole('button', { name: 'Halt submission' }).click();
  await expect(teacher.locator('.status')).toContainText('CLOSED');

  // --- the participants view ---
  await goto('Participants');
  await teacher.waitForURL('**/people');
  await expect(teacher.locator('.warning')).toContainText('do not project');

  // Names stay hidden until asked for, so opening this tab in front of the
  // class does not expose anyone.
  await expect(teacher.locator('table')).toHaveCount(0);
  await expect(teacher.locator('.empty')).toContainText('2 students have taken part');

  await teacher.getByRole('button', { name: 'Show names' }).click();
  const anna = teacher.locator('tbody tr', { hasText: 'anna' });
  const bo = teacher.locator('tbody tr', { hasText: 'bo' });
  await expect(anna).toBeVisible();
  await expect(bo).toBeVisible();

  // Anna: wrong before, right after. Bo: right both times.
  await expect(anna.locator('td.q .mark').nth(0)).toHaveText('✗');
  await expect(anna.locator('td.q .mark').nth(1)).toHaveText('✓');
  await expect(anna.locator('td.tot').nth(0)).toHaveText('0');
  await expect(anna.locator('td.tot').nth(1)).toHaveText('1');
  await expect(bo.locator('td.q .mark').nth(0)).toHaveText('✓');
  await expect(bo.locator('td.tot').nth(0)).toHaveText('1');

  await teacherCtx.close();
});
