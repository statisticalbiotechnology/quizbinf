import { Component, OnDestroy, OnInit, signal } from '@angular/core';

import { ApiService } from '../api.service';
import { Phase, Question } from '../models';
import { SessionFeed } from './session-feed.service';

/** How often to refresh the answer counter while a round is open. */
const POLL_MS = 2000;

/**
 * The teacher's control panel: open and halt the submission window.
 *
 * Shows how many answers are in, never how they split — that belongs to the
 * report view, after the round is halted.
 */
@Component({
  selector: 'app-teacher-session-control',
  standalone: true,
  template: `
    <div class="wrap">
      <p class="status" [class.open]="feed.anyOpen()">
        @if (feed.openRound(); as r) {
          <strong>OPEN</strong> — {{ r.phase === 'pre' ? 'first' : 'second' }} answers ·
          {{ answered() }} answer{{ answered() === 1 ? '' : 's' }} in
          <button class="halt" (click)="halt(r.id)">Halt submission</button>
        } @else {
          <strong>CLOSED</strong> — no answers are being accepted.
        }
      </p>

      @if (!feed.questions().length) {
        <p class="empty">
          This quiz has no questions yet, so there is nothing to open a round
          for. Add one from the dashboard, then reload.
        </p>
      }

      <!-- While a round is open this is the one question that matters, so the
           rest of the quiz gets out of the way: the teacher is driving from
           this screen mid-lecture and should not have to find the live one in
           a list. The others stay one click away. -->
      @for (q of shownQuestions(); track q.id) {
        <section class="q" [class.live]="feed.openRoundFor(q)">
          <div class="qtext"><span class="num">{{ q.position + 1 }}.</span>
            <span [innerHTML]="q.text_html"></span></div>

          <!-- The alternatives as the students see them, so the teacher can
               read them out without switching views. Nothing is marked until
               asked for: this screen is often visible from the room. -->
          <ol class="choices">
            @for (c of q.choices; track c.id) {
              <li [class.correct]="revealed()[q.id] && c.is_correct">{{ c.text }}</li>
            }
          </ol>
          <button type="button" class="reveal" (click)="toggleReveal(q.id)">
            {{ revealed()[q.id] ? 'Hide the answer' : 'Show which is correct' }}
          </button>

          <div class="controls">
            <button (click)="open(q, 'pre')" [disabled]="feed.anyOpen() || ran(q, 'pre')">
              {{ ran(q, 'pre') ? '✓ 1st bout done' : 'Open 1st bout (pre)' }}
            </button>
            <button (click)="open(q, 'post')" [disabled]="feed.anyOpen() || ran(q, 'post')">
              {{ ran(q, 'post') ? '✓ 2nd bout done' : 'Open 2nd bout (post)' }}
            </button>
            @if (hasRun(q)) {
              <button class="reset" (click)="reset(q)" title="Discard this question's answers and run it again">
                ↺ Reset
              </button>
            }
          </div>
          @if (feed.openRoundFor(q)) {
            <p class="note">Results stay hidden until you halt this round.</p>
          }
        </section>
      }

      @if (canNarrow()) {
        <button type="button" class="show-all" (click)="showAll.set(!showAll())">
          {{ showAll() ? 'Show only the live question' : 'Show all ' + feed.questions().length + ' questions' }}
        </button>
      }

      @if (actionError()) {
        <p class="error">{{ actionError() }}</p>
      }
    </div>
  `,
  styles: [
    `
      .wrap { max-width: 46rem; margin: 1.5rem auto; padding: 1rem; }
      .status { font-size: 1.2rem; padding: 0.7rem 0.9rem; border-radius: 6px;
                background: #f3f3f3; margin: 0 0 1rem; }
      .status.open { background: #eafaf1; }
      .halt { margin-left: 0.8rem; background: #c0392b; color: #fff; border-color: #a33; }
      .q { border-top: 1px solid #eee; padding: 0.9rem 0; }
      .q.live { border-left: 4px solid #2c7a51; padding-left: 0.8rem; }
      .qtext { font-weight: 600; margin: 0 0 0.5rem; }
      .choices { margin: 0.3rem 0 0.5rem; padding-left: 1.4rem; color: #333; }
      .choices li { margin: 0.1rem 0; }
      .choices li.correct { font-weight: 700; color: #2c7; }
      .reveal { font-size: 0.75rem; padding: 0.15rem 0.45rem; margin-bottom: 0.5rem;
                color: #777; background: none; }
      .show-all { font-size: 0.85rem; margin-top: 0.8rem; }
      .qtext :is(p, ul, ol) { display: inline; margin: 0; }
      .controls { display: flex; gap: 0.5rem; flex-wrap: wrap; }
      .reset { margin-left: auto; color: #8a2b20; border-color: #e6b8b2; }
      .note { font-size: 0.85rem; color: #777; font-style: italic; }
      .empty { color: #777; }
      .error { color: #c0392b; }
    `,
  ],
})
export class TeacherSessionControlComponent implements OnInit, OnDestroy {
  answered = signal(0);
  actionError = signal('');
  /** Override the narrowing, for picking what to ask next mid-session. */
  showAll = signal(false);
  /** Which questions have had their answer revealed, by explicit click. */
  revealed = signal<Record<number, boolean>>({});
  private timer?: ReturnType<typeof setInterval>;

  constructor(public feed: SessionFeed, private api: ApiService) {}

  ngOnInit(): void {
    this.refresh();
    this.timer = setInterval(() => {
      if (this.feed.anyOpen()) this.refresh();
    }, POLL_MS);
  }

  ngOnDestroy(): void {
    if (this.timer) clearInterval(this.timer);
  }

  /**
   * The questions to show: just the live one while a round is open.
   *
   * Only while a round is *open* — between bouts the teacher is choosing what
   * to do next, and a single question with no way past it would be a worse
   * tool than the list.
   */
  shownQuestions(): Question[] {
    const open = this.feed.openRound();
    if (!open || this.showAll()) return this.feed.questions();
    return this.feed.questions().filter((q) => q.id === open.question_id);
  }

  /**
   * Whether the narrowing applies at all, and so whether to offer the toggle.
   *
   * Not "how many are hidden": with the list expanded nothing is hidden, and
   * the way back to the live question would disappear with the button.
   */
  canNarrow(): boolean {
    return !!this.feed.openRound() && this.feed.questions().length > 1;
  }

  toggleReveal(questionId: number): void {
    this.revealed.update((r) => ({ ...r, [questionId]: !r[questionId] }));
  }

  /** Whether anything has been run for this question, so a reset makes sense. */
  hasRun(q: Question): boolean {
    return this.ran(q, 'pre') || this.ran(q, 'post') || !!this.feed.openRoundFor(q);
  }

  /**
   * Throw away this question's rounds so both bouts can be run again.
   *
   * Deletes the answers with them, so it asks first — useful when rehearsing,
   * destructive during a real lecture.
   */
  reset(q: Question): void {
    const ok = confirm(
      'Reset this question?\n\nThis deletes the answers students have already ' +
        'given for it in this session, and lets you run both bouts again.',
    );
    if (!ok) return;
    this.actionError.set('');
    this.api.resetQuestion(this.feed.code(), q.id).subscribe({
      next: () => {
        this.answered.set(0);
        this.feed.refreshState();
        this.feed.refreshComparisons();
      },
      error: () => this.actionError.set('Could not reset that question.'),
    });
  }

  /** A phase that has already been run cannot be opened again. */
  ran(q: Question, phase: Phase): boolean {
    return this.feed.ran(q, phase);
  }

  open(q: Question, phase: Phase): void {
    this.actionError.set('');
    this.api.openRound(this.feed.code(), q.id, phase).subscribe({
      next: () => {
        this.answered.set(0);
        this.feed.refreshState();
      },
      error: (err) => {
        this.actionError.set(err?.error?.detail ?? 'Could not open that round.');
        this.feed.refreshState();
      },
    });
  }

  halt(roundId: number): void {
    this.api.closeRound(this.feed.code(), roundId).subscribe({
      next: () => {
        this.feed.refreshState();
        this.feed.refreshComparisons();
      },
      error: () => this.feed.refreshState(),
    });
  }

  private refresh(): void {
    this.api.liveCount(this.feed.code()).subscribe((l) => this.answered.set(l.answered));
  }
}
