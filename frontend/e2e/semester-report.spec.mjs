import { expect, test } from '@playwright/test';

/**
 * The end-of-term attendance export, driven the way a teacher reaches it.
 *
 * Runs one full question — pre, discussion, post — with one student answering
 * both bouts and another answering only the first, then downloads the CSV
 * from the dashboard and checks it says what it should.
 */
test('a teacher exports attendance across sessions', async ({ browser }) => {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();

  await page.goto('/login');
  await page.getByPlaceholder('e.g. lukask').fill('teacher');
  await page.getByRole('button', { name: 'Log in' }).click();
  await page.waitForURL('**/teacher');

  await page.fill('input[name="title"]', 'Semester report quiz');
  await page.click('form.new-quiz button');
  const quiz = page
    .locator('section.quiz')
    .filter({ has: page.getByRole('heading', { name: 'Semester report quiz', exact: true }) })
    .last();
  await quiz.locator('details summary').filter({ hasText: 'Add question' }).click();
  await quiz.locator('textarea[name="qtext"]').fill('Does the discussion help?');
  const choices = quiz.locator('.add-q input[placeholder="Choice text"]');
  await choices.nth(0).fill('Helps a lot');
  await choices.nth(1).fill('Hinders');
  await quiz.locator('.add-q input[type="radio"]').first().check();
  await quiz.getByRole('button', { name: 'Save question' }).click();
  await quiz.getByRole('button', { name: 'Run session' }).click();
  await page.waitForURL('**/teacher/session/*/join');
  const code = page.url().match(/session\/([a-z0-9]+)\//)[1];

  // Two students: one answers both bouts, one only the first.
  const students = [];
  for (const name of ['bothbouts', 'onlypre']) {
    const sctx = await browser.newContext();
    const spage = await sctx.newPage();
    await spage.goto('/login');
    await spage.getByPlaceholder('e.g. lukask').fill(name);
    await spage.getByRole('button', { name: 'Log in' }).click();
    await spage.goto(`/s/${code}`);
    students.push({ name, ctx: sctx, page: spage });
  }

  const control = page.url().replace('/join', '/control');
  await page.goto(control);

  for (const bout of ['Open 1st bout (pre)', 'Open 2nd bout (post)']) {
    await page.getByRole('button', { name: bout }).click();

    for (const s of students) {
      // "onlypre" skips the second bout — that is the whole point of the test.
      if (bout.startsWith('Open 2nd') && s.name === 'onlypre') continue;
      const choice = s.page.getByRole('button', { name: 'Helps a lot' });
      await expect(choice).toBeEnabled({ timeout: 10000 });
      await choice.click();
    }

    await page.getByRole('button', { name: 'Halt submission' }).click();
    await expect(page.getByRole('button', { name: 'Halt submission' })).toHaveCount(0);
  }

  // Download the term report from the dashboard.
  await page.goto('/teacher');
  await page.getByText('End-of-term participation').click();
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('link', { name: 'Download CSV' }).click(),
  ]);
  const csv = await (await import('node:fs/promises')).readFile(await download.path(), 'utf8');

  const rows = csv.trim().split('\n').map((line) => line.split(','));
  const header = rows[0];
  expect(header[0]).toBe('username');
  expect(header.at(-2)).toBe('sessions_attended');

  // Other specs share this backend, so find our session's column rather than
  // assuming it is the first one.
  const col = header.findIndex((h) => h.includes(code));
  expect(col, `no column for session ${code}`).toBeGreaterThan(1);

  const byUser = Object.fromEntries(rows.slice(1).map((r) => [r[0], r]));
  expect(byUser['bothbouts'], 'the student who answered twice').toBeDefined();
  expect(byUser['bothbouts'][col]).toBe('yes');
  expect(byUser['onlypre'][col]).toContain('no');
  // Attendance only: the export says nothing about right or wrong.
  expect(byUser['bothbouts'].join(',').toLowerCase()).not.toContain('correct');

  for (const s of students) await s.ctx.close();
  await ctx.close();
});
