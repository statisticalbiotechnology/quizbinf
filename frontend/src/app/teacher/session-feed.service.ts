import { Injectable, OnDestroy, signal } from '@angular/core';

import { ApiService } from '../api.service';
import { Comparison, Phase, Question, Round, SessionState } from '../models';

/**
 * Shared session data for the three teacher views.
 *
 * Provided by the session shell rather than at root, so each session gets its
 * own instance and a single SSE stream is shared by whichever view is open.
 */
@Injectable()
export class SessionFeed implements OnDestroy {
  readonly code = signal('');
  readonly state = signal<SessionState | null>(null);
  readonly questions = signal<Question[]>([]);
  readonly comparisons = signal<Record<number, Comparison>>({});
  readonly joinUrl = signal('');
  readonly error = signal('');

  private teardown?: () => void;

  constructor(private api: ApiService) {}

  init(code: string): void {
    if (this.code() === code) return;
    this.code.set(code);
    this.api.joinUrl(code).subscribe({
      next: ({ url }) => this.joinUrl.set(url),
      error: () => this.error.set('Session not found.'),
    });
    this.refreshState();
    this.teardown = this.api.streamState(code, (s) => {
      this.state.set(s);
      this.loadQuestions();
      this.refreshComparisons();
    });
  }

  ngOnDestroy(): void {
    this.teardown?.();
  }

  refreshState(): void {
    this.api.sessionState(this.code()).subscribe({
      next: (s) => {
        this.state.set(s);
        this.loadQuestions();
      },
      error: () => this.error.set('Session not found.'),
    });
  }

  private loadQuestions(): void {
    const st = this.state();
    if (!st || this.questions().length) return;
    // By id, not by title: two quizzes sharing a name used to leave the
    // teacher with no questions and no way to open a round.
    this.api.getQuiz(st.quiz_id).subscribe({
      next: (quiz) => {
        this.questions.set(quiz.questions);
        this.refreshComparisons();
      },
      error: () => this.error.set('Could not load this session’s questions.'),
    });
  }

  refreshComparisons(): void {
    for (const q of this.questions()) {
      this.api.comparison(this.code(), q.id).subscribe((c) => {
        this.comparisons.update((m) => ({ ...m, [q.id]: c }));
      });
    }
  }

  openRound(): Round | null {
    return this.state()?.open_round ?? null;
  }

  anyOpen(): boolean {
    return !!this.openRound();
  }

  openRoundFor(q: Question): Round | null {
    const r = this.openRound();
    return r && r.question_id === q.id ? r : null;
  }

  /**
   * Whether a phase's distribution may be shown for this question.
   *
   * Never while that phase's own round is open: the report view is the
   * projected one, and revealing the split before the discussion defeats the
   * point of asking twice.
   */
  showPhase(q: Question, phase: Phase): boolean {
    const open = this.openRoundFor(q);
    return !(open && open.phase === phase);
  }

  pct(counts: Record<number, number> | null, choiceId: number, q: Question): number {
    if (!counts) return 0;
    const total = q.choices.reduce((sum, c) => sum + (counts[c.id] || 0), 0);
    return total ? Math.round((100 * (counts[choiceId] || 0)) / total) : 0;
  }

  total(counts: Record<number, number> | null): number {
    if (!counts) return 0;
    return Object.values(counts).reduce((a, b) => a + b, 0);
  }
}
