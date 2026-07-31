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

      @for (q of feed.questions(); track q.id) {
        <section class="q">
          <p class="qtext">{{ q.position + 1 }}. {{ q.text }}</p>
          <div class="controls">
            <button (click)="open(q, 'pre')" [disabled]="feed.anyOpen() || ran(q, 'pre')">
              {{ ran(q, 'pre') ? '✓ 1st bout done' : 'Open 1st bout (pre)' }}
            </button>
            <button (click)="open(q, 'post')" [disabled]="feed.anyOpen() || ran(q, 'post')">
              {{ ran(q, 'post') ? '✓ 2nd bout done' : 'Open 2nd bout (post)' }}
            </button>
          </div>
          @if (feed.openRoundFor(q)) {
            <p class="note">Results stay hidden until you halt this round.</p>
          }
        </section>
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
      .qtext { font-weight: 600; margin: 0 0 0.5rem; }
      .controls { display: flex; gap: 0.5rem; }
      .note { font-size: 0.85rem; color: #777; font-style: italic; }
      .empty { color: #777; }
      .error { color: #c0392b; }
    `,
  ],
})
export class TeacherSessionControlComponent implements OnInit, OnDestroy {
  answered = signal(0);
  actionError = signal('');
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

  /** A phase that has already been run cannot be opened again. */
  ran(q: Question, phase: Phase): boolean {
    const counts = this.feed.comparisons()[q.id];
    const open = this.feed.openRoundFor(q);
    if (open && open.phase === phase) return false;
    return !!counts && counts[phase] !== null;
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
