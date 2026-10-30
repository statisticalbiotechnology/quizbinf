import { expect, test } from '@playwright/test';

/**
 * Questions from a Markdown file: checked first, then imported, then editable.
 * A question that breaks the format is refused with its reason, and the rest
 * still come in.
 */

const QUIZ = `# Imported quiz

## 1. Local alignment

Which algorithm finds the optimal **local** alignment?

- [ ] Needleman-Wunsch
- [x] Smith-Waterman

> It lets the score restart at zero.

## 2. Broken

Two answers marked.

- [x] one
- [x] two
`;

test('a Markdown file is checked, imported and then editable', async ({ page }) => {
  await page.goto('/login');
  await page.getByPlaceholder('e.g. lukask').fill('teacher');
  await page.getByRole('button', { name: 'Log in' }).click();
  await page.waitForURL('**/teacher');

  await page.fill('input[name="title"]', 'Import quiz');
  await page.click('form.new-quiz button');
  const quiz = page
    .locator('section.quiz')
    .filter({ has: page.getByRole('heading', { name: 'Import quiz', exact: true }) })
    .last();
  await quiz.locator('details summary').first().click();

  // The format is one click away from the picker.
  await quiz.getByRole('button', { name: 'What should the file look like?' }).click();
  const help = page.locator('dialog.format-help');
  await expect(help).toBeVisible();
  await expect(help).toContainText('exactly one');
  await help.screenshot({ path: 'test-results/format-help.png' });
  await help.getByRole('button', { name: 'Close' }).click();
  await expect(help).toBeHidden();

  await quiz.locator('.import input[type=file]').setInputFiles({
    name: 'quiz.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from(QUIZ),
  });

  // The check, before anything is stored.
  const report = quiz.locator('.import-report');
  await expect(report.locator('li')).toHaveCount(2);
  await expect(report.locator('li').nth(0)).toContainText('Local alignment');
  await expect(report.locator('li.refused')).toContainText('Exactly one choice must be marked');
  await expect(quiz.locator('ol > li .q-row')).toHaveCount(0);

  await quiz.getByRole('button', { name: 'Import 1 question' }).click();
  await expect(quiz.getByText(/Imported 1 question/)).toBeVisible();
  await expect(quiz.locator('ol > li .q-row')).toHaveCount(1);

  // Editable like any other question, explanation included.
  await quiz.locator('ol > li').first().getByRole('button', { name: 'Edit' }).click();
  const editor = quiz.locator('ol > li').first();
  await expect(editor.locator('textarea').nth(1)).toHaveValue('It lets the score restart at zero.');
  await expect(editor.locator('.preview')).toContainText('Local alignment');
});
