import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { ApiService } from '../api.service';
import { Choice, Comparison, Question, Round } from '../models';
import { TeacherSessionReportComponent } from './session-report.component';
import { SessionFeed } from './session-feed.service';

/**
 * What reaches the projector.
 *
 * These helpers decide which question the room sees and when the correct
 * choice may be marked, so they are worth pinning: getting either wrong hands
 * the class the answer before they have argued about it.
 */
function choices(...texts: string[]): Choice[] {
  return texts.map((text, i) => ({
    id: 100 + i,
    position: i,
    text,
    is_correct: i === 0,
  }));
}

function question(id: number, position: number): Question {
  return {
    id,
    position,
    text: `q${id}`,
    text_html: `<p>q${id}</p>`,
    image_url: null,
    choices: choices('right', 'wrong'),
  };
}

function openRound(questionId: number, phase: 'pre' | 'post'): Round {
  return { id: 1, question_id: questionId, phase, opened_at: '', closed_at: null };
}

function comparison(question_id: number, pre = false, post = false): Comparison {
  const counts = { 100: 3, 101: 1 };
  return { question_id, pre: pre ? counts : null, post: post ? counts : null };
}

describe('SessionFeed projection rules', () => {
  let feed: SessionFeed;
  const q1 = question(1, 0);
  const q2 = question(2, 1);

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        SessionFeed,
        ApiService,
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    feed = TestBed.inject(SessionFeed);
    feed.questions.set([q1, q2]);
  });

  it('does not count a phase as run while its own round is open', () => {
    feed.comparisons.set({ 1: comparison(1, true) });
    feed.state.set({
      code: 'abc123',
      quiz_id: 1,
      quiz_title: 'Bioinf',
      open_round: openRound(1, 'pre'),
      question: q1,
      my_choice_id: null,
    });

    expect(feed.ran(q1, 'pre')).toBeFalse();
  });

  it('counts a phase as run once its round is halted', () => {
    feed.comparisons.set({ 1: comparison(1, true) });
    expect(feed.ran(q1, 'pre')).toBeTrue();
    expect(feed.ran(q1, 'post')).toBeFalse();
  });

  it('shows the question of whichever round is open', () => {
    feed.comparisons.set({ 1: comparison(1, true, true), 2: comparison(2) });
    feed.state.set({
      code: 'abc123',
      quiz_id: 1,
      quiz_title: 'Bioinf',
      open_round: openRound(2, 'pre'),
      question: q2,
      my_choice_id: null,
    });

    expect(feed.currentQuestion()?.id).toBe(2);
    expect(feed.stage()).toBe('pre');
  });

  it('stays on the question being discussed between the two bouts', () => {
    feed.comparisons.set({ 1: comparison(1, true), 2: comparison(2) });

    expect(feed.currentQuestion()?.id).toBe(1);
    expect(feed.stage()).toBe('discuss');
  });

  it('moves to the next unasked question once both bouts are done', () => {
    feed.comparisons.set({ 1: comparison(1, true, true), 2: comparison(2) });

    expect(feed.currentQuestion()?.id).toBe(2);
    expect(feed.stage()).toBe('waiting');
  });

  it('counts the lecture as started once a bout has opened', () => {
    // Which is what moves the projected screen from "here is the QR code" to
    // "here is the question, and the code in the corner".
    expect(feed.started()).toBeFalse();

    feed.state.set({
      code: 'abc123',
      quiz_id: 1,
      quiz_title: 'Bioinf',
      open_round: openRound(1, 'pre'),
      question: q1,
      my_choice_id: null,
    });
    expect(feed.started()).toBeTrue();
  });

  it('stays started between the bouts, when no round is open', () => {
    feed.comparisons.set({ 1: comparison(1, true) });
    expect(feed.openRound()).toBeNull();
    expect(feed.started()).toBeTrue();
  });

  it('has nothing left to project once every question has run twice', () => {
    feed.comparisons.set({
      1: comparison(1, true, true),
      2: comparison(2, true, true),
    });

    expect(feed.currentQuestion()).toBeNull();
    expect(feed.stage()).toBe('done');
  });
});

describe('TeacherSessionReportComponent', () => {
  let feed: SessionFeed;
  let report: TeacherSessionReportComponent;
  const q1 = question(1, 0);

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        SessionFeed,
        ApiService,
        provideHttpClient(),
        provideHttpClientTesting(),
        TeacherSessionReportComponent,
      ],
    });
    feed = TestBed.inject(SessionFeed);
    feed.questions.set([q1]);
    report = TestBed.inject(TeacherSessionReportComponent);
  });

  it('does not mark the correct choice after only the first bout', () => {
    // This is the moment the distribution goes on the projector and the class
    // is told to argue about it. A tick here would end the argument.
    feed.comparisons.set({ 1: comparison(1, true) });
    expect(report.shown(q1)).toBeTrue();
    expect(report.revealCorrect(q1)).toBeFalse();
  });

  it('marks the correct choice once the second bout is halted', () => {
    feed.comparisons.set({ 1: comparison(1, true, true) });
    expect(report.revealCorrect(q1)).toBeTrue();
  });
});
