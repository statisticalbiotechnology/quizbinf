import { expect, test } from '@playwright/test';

/**
 * What the projector shows, and what it must not.
 *
 * Three things this pins, all of which only a browser can see: the question is
 * on the projected screen from the moment students start scanning; the correct
 * choice is not marked until the second bout has run; and the draw names two
 * students who actually answered.
 */

async function loginAs(page, username) {
  await page.goto('/login');
  await page.getByPlaceholder('e.g. lukask').fill(username);
  await page.getByRole('button', { name: 'Log in' }).click();
}

async function authorQuestion(page, quizTitle, questionText, choices) {
  await page.fill('input[name="title"]', quizTitle);
  await page.click('form.new-quiz button');
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

test('the projected screen shows the question but never the answer too early', async ({
  browser,
}) => {
  const teacherCtx = await browser.newContext();
  const teacher = await teacherCtx.newPage();
  await loginAs(teacher, 'teacher');
  await teacher.waitForURL('**/teacher');

  const quiz = await authorQuestion(teacher, 'Projection quiz', 'Which is local?', [
    'Smith-Waterman',
    'Needleman-Wunsch',
  ]);
  await quiz.getByRole('button', { name: 'Run session' }).click();
  await teacher.waitForURL('**/teacher/session/*/join');

  // --- the join screen, projected while students are still scanning ---
  const panel = teacher.locator('app-question-panel');
  await expect(panel.locator('.qtext')).toContainText('Which is local?');
  await expect(panel.locator('.choices li')).toHaveCount(2);
  await expect(panel.locator('.stage')).toContainText('Coming up');
  // Nothing on the projected screen may distinguish the correct choice.
  await expect(panel.locator('.correct')).toHaveCount(0);

  const joinUrl = (await teacher.locator('code.url').textContent())?.trim() ?? '';
  const sessionPath = new URL(joinUrl).pathname;

  const studentCtx = await browser.newContext();
  const student = await studentCtx.newPage();
  await loginAs(student, 'projstudent');
  await student.goto(sessionPath);
  await expect(student.locator('.waiting')).toBeVisible();

  // A second student who joins but never answers: they must appear on the
  // reel the draw spins through — it is drawn from the room, not from the
  // answerers — but must never be the one it lands on.
  const watcherCtx = await browser.newContext();
  const watcher = await watcherCtx.newPage();
  await loginAs(watcher, 'projwatcher');
  await watcher.goto(sessionPath);
  await expect(watcher.locator('.waiting')).toBeVisible();

  const goto = (name) => teacher.getByRole('link', { name, exact: true }).click();

  // --- first bout: the join screen keeps up ---
  await goto('Control');
  await teacher.getByRole('button', { name: 'Open 1st bout (pre)' }).click();
  await goto('Join');
  await expect(panel.locator('.stage')).toContainText('first bout');
  await expect(panel.locator('.correct')).toHaveCount(0);

  await student.getByRole('button', { name: 'Needleman-Wunsch' }).click();
  await goto('Control');
  await teacher.getByRole('button', { name: 'Halt submission' }).click();
  await expect(teacher.locator('.status')).toContainText('CLOSED');

  // --- between the bouts: the question stays up, labelled for discussion ---
  await goto('Join');
  await expect(panel.locator('.stage')).toContainText('Discuss');
  await expect(panel.locator('.qtext')).toContainText('Which is local?');

  // The pre distribution is projected now, and this is exactly when the class
  // must not be told which one is right.
  await goto('Report');
  await expect(teacher.locator('.bar.pre')).toHaveCount(2);
  await expect(
    teacher.locator('.tick'),
    'the correct choice was marked before the second bout',
  ).toHaveCount(0);
  await expect(teacher.locator('.row.correct')).toHaveCount(0);

  // --- drawing someone to explain their reasoning ---
  await teacher.getByRole('button', { name: 'Draw two to explain' }).click();
  const slot = teacher.locator('app-name-draw .slot');
  // It rolls first…
  await expect(slot).toHaveClass(/spinning/);
  // …and comes to rest on the one student who actually answered, never on the
  // one who only joined, however long the spin takes.
  await expect(slot).toHaveClass(/fixed/, { timeout: 15000 });
  await expect(slot).toHaveText('projstudent');
  await expect(teacher.getByRole('button', { name: 'Draw again' })).toBeVisible();

  // --- second bout, then the answer may finally be shown ---
  await goto('Control');
  await teacher.getByRole('button', { name: 'Open 2nd bout (post)' }).click();
  await expect(student.locator('.qtext')).toContainText('Which is local?');
  await student.getByRole('button', { name: 'Smith-Waterman' }).click();
  await teacher.getByRole('button', { name: 'Halt submission' }).click();
  await expect(teacher.locator('.status')).toContainText('CLOSED');

  await goto('Report');
  await expect(teacher.locator('.bar.post')).toHaveCount(2);
  await expect(teacher.locator('.tick')).toHaveCount(1);
  await expect(teacher.locator('.row.correct .label')).toContainText('Smith-Waterman');

  await teacherCtx.close();
  await studentCtx.close();
  await watcherCtx.close();
});
