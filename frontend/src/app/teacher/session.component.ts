import { Component, OnDestroy, OnInit, signal } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import * as QRCode from 'qrcode';

import { ApiService } from '../api.service';
import { Comparison, Phase, Question, Quiz, Round, SessionState } from '../models';

/** How often to refresh the answer counter while a round is open. */
const LIVE_POLL_MS = 2000;

@Component({
  selector: 'app-teacher-session',
  standalone: true,
  template: `
    <div class="wrap">
      <div class="join">
        <img [src]="qrDataUrl()" alt="QR code to join" width="200" height="200" />
        <div>
          <p>Students scan to join:</p>
          <code class="url">{{ joinUrl() }}</code>
          <p class="code">Session code: <strong>{{ code }}</strong></p>
        </div>
      </div>

      <!-- Submission window status, big enough to read from the lectern. -->
      <p class="status" [class.open]="anyOpen()">
        @if (openRound(); as r) {
          <strong>OPEN</strong> for answers — {{ r.phase === 'pre' ? 'first' : 'second' }} bout ·
          {{ answered() }} answer{{ answered() === 1 ? '' : 's' }} in
          <button class="halt" (click)="close(r.id)">Halt submission</button>
        } @else {
          <strong>CLOSED</strong> — no answers are being accepted.
        }
      </p>

      @for (q of questions(); track q.id) {
        <section class="q">
          <p class="qtext">{{ q.position + 1 }}. {{ q.text }}</p>
          <div class="controls">
            <button (click)="open(q, 'pre')" [disabled]="anyOpen()">
              Open 1st bout (pre)
            </button>
            <button (click)="open(q, 'post')" [disabled]="anyOpen()">
              Open 2nd bout (post)
            </button>
          </div>

          @if (cmp(q); as c) {
            <div class="hist">
              @for (ch of q.choices; track ch.id) {
                <div class="bar-row" [class.correct]="ch.is_correct">
                  <span class="label">{{ ch.text }}</span>
                  <span class="bars">
                    @if (showPhase(q, 'pre')) {
                      <span class="bar pre" [style.width.%]="pct(c.pre, ch.id, q)"></span>
                    }
                    @if (showPhase(q, 'post')) {
                      <span class="bar post" [style.width.%]="pct(c.post, ch.id, q)"></span>
                    }
                  </span>
                </div>
              }
              <p class="legend">
                <span class="sw pre"></span> before discussion &nbsp;
                <span class="sw post"></span> after discussion
              </p>
            </div>
          }
          @if (openRoundFor(q)) {
            <p class="hidden-note">
              Results stay hidden while answers are open — they appear when you halt.
            </p>
          }
        </section>
      }
    </div>
  `,
  styles: [
    `
      .wrap { max-width: 46rem; margin: 1.5rem auto; padding: 1rem; }
      .join { display: flex; gap: 1rem; align-items: center; border: 1px solid #ddd;
              border-radius: 8px; padding: 1rem; }
      .url { font-size: 0.9rem; }
      .status { font-size: 1.1rem; padding: 0.6rem 0.8rem; border-radius: 6px;
                background: #f3f3f3; margin: 1rem 0; }
      .status.open { background: #eafaf1; }
      .halt { margin-left: 0.8rem; background: #c0392b; color: #fff; border-color: #a33; }
      .q { border-top: 1px solid #eee; padding: 1rem 0; }
      .qtext { font-weight: 600; }
      .controls { display: flex; gap: 0.5rem; margin-bottom: 0.6rem; }
      .bar-row { display: flex; align-items: center; gap: 0.4rem; margin: 0.25rem 0; }
      .label { width: 14rem; }
      .bars { flex: 1; display: flex; flex-direction: column; gap: 2px; }
      .bar { height: 0.8rem; display: block; min-width: 1px; }
      .bar.pre { background: #9bd; }
      .bar.post { background: #2c7a51; }
      .bar-row.correct .label { font-weight: 700; }
      .sw { display: inline-block; width: 0.8rem; height: 0.8rem; vertical-align: middle; }
      .sw.pre { background: #9bd; }
      .sw.post { background: #2c7a51; }
      .legend { font-size: 0.85rem; color: #555; }
      .hidden-note { font-size: 0.85rem; color: #777; font-style: italic; }
    `,
  ],
})
export class TeacherSessionComponent implements OnInit, OnDestroy {
  code = '';
  state = signal<SessionState | null>(null);
  questions = signal<Question[]>([]);
  comparisons = signal<Record<number, Comparison>>({});
  answered = signal(0);
  qrDataUrl = signal<string>('');
  joinUrl = signal<string>('');
  private teardown?: () => void;
  private pollTimer?: ReturnType<typeof setInterval>;

  constructor(private api: ApiService, private route: ActivatedRoute) {}

  ngOnInit(): void {
    this.code = this.route.snapshot.paramMap.get('code') || '';
    this.api.joinUrl(this.code).subscribe(({ url }) => {
      this.joinUrl.set(url);
      QRCode.toDataURL(url, { width: 200 }).then((d) => this.qrDataUrl.set(d));
    });
    this.refreshState();
    this.teardown = this.api.streamState(this.code, (s) => {
      this.state.set(s);
      this.loadQuestions();
      this.refreshComparisons();
      this.refreshLive();
    });
    // The answer counter is teacher-only and polled; students never see it.
    this.pollTimer = setInterval(() => {
      if (this.anyOpen()) this.refreshLive();
    }, LIVE_POLL_MS);
  }

  ngOnDestroy(): void {
    this.teardown?.();
    if (this.pollTimer) clearInterval(this.pollTimer);
  }

  private refreshState(): void {
    this.api.sessionState(this.code).subscribe((s) => {
      this.state.set(s);
      this.loadQuestions();
      this.refreshLive();
    });
  }

  private refreshLive(): void {
    this.api.liveCount(this.code).subscribe((l) => this.answered.set(l.answered));
  }

  private loadQuestions(): void {
    const st = this.state();
    if (!st || this.questions().length) return;
    this.api.listQuizzes().subscribe((quizzes: Quiz[]) => {
      const quiz = quizzes.find((q) => q.title === st.quiz_title);
      if (quiz) {
        this.questions.set(quiz.questions);
        this.refreshComparisons();
      }
    });
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
   * Never reveal a phase's distribution while that phase is still open — the
   * teacher's screen is projected, and seeing the vote split before the
   * discussion would defeat the point of asking twice.
   */
  showPhase(q: Question, phase: Phase): boolean {
    const open = this.openRoundFor(q);
    return !(open && open.phase === phase);
  }

  cmp(q: Question): Comparison | null {
    return this.comparisons()[q.id] ?? null;
  }

  open(q: Question, phase: Phase): void {
    this.api.openRound(this.code, q.id, phase).subscribe({
      next: () => {
        this.answered.set(0);
        this.refreshState();
      },
      error: () => this.refreshState(),
    });
  }

  close(roundId: number): void {
    this.api.closeRound(this.code, roundId).subscribe(() => this.refreshState());
  }

  private refreshComparisons(): void {
    for (const q of this.questions()) {
      this.api.comparison(this.code, q.id).subscribe((c) => {
        this.comparisons.update((m) => ({ ...m, [q.id]: c }));
      });
    }
  }

  pct(counts: Record<number, number> | null, choiceId: number, q: Question): number {
    if (!counts) return 0;
    const total = q.choices.reduce((sum, c) => sum + (counts[c.id] || 0), 0);
    return total ? Math.round((100 * (counts[choiceId] || 0)) / total) : 0;
  }
}
