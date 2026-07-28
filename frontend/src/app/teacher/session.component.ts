import { Component, OnDestroy, OnInit, signal } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import * as QRCode from 'qrcode';

import { ApiService } from '../api.service';
import { Comparison, Question, Quiz, SessionState } from '../models';

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

      @if (state(); as s) {
        <p class="status">
          @if (s.open_round) {
            Open: question #{{ s.open_round.question_id }} ({{ s.open_round.phase }} round)
          } @else {
            No round open.
          }
        </p>
      }

      @for (q of questions(); track q.id) {
        <section class="q">
          <p class="qtext">{{ q.position + 1 }}. {{ q.text }}</p>
          <div class="controls">
            <button (click)="open(q, 'pre')" [disabled]="anyOpen()">Open pre</button>
            <button (click)="open(q, 'post')" [disabled]="anyOpen()">Open post</button>
            @if (openRoundFor(q); as r) {
              <button class="close" (click)="close(r.id)">Close current</button>
            }
          </div>

          @if (comparisons()[q.id]; as cmp) {
            <div class="hist">
              @for (c of q.choices; track c.id) {
                <div class="bar-row" [class.correct]="c.is_correct">
                  <span class="label">{{ c.text }}</span>
                  <span class="bar pre" [style.width.%]="pct(cmp.pre, c.id, q)"></span>
                  <span class="bar post" [style.width.%]="pct(cmp.post, c.id, q)"></span>
                </div>
              }
              <p class="legend"><span class="sw pre"></span> pre &nbsp; <span class="sw post"></span> post</p>
            </div>
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
      .status { font-weight: 600; margin: 1rem 0; }
      .q { border-top: 1px solid #eee; padding: 1rem 0; }
      .controls { display: flex; gap: 0.5rem; }
      .close { background: #c0392b; color: #fff; }
      .bar-row { display: flex; align-items: center; gap: 0.4rem; margin: 0.2rem 0; }
      .label { width: 12rem; }
      .bar { height: 0.9rem; display: inline-block; }
      .bar.pre { background: #9bd; }
      .bar.post { background: #2c7; }
      .bar-row.correct .label { font-weight: 700; }
      .sw { display: inline-block; width: 0.8rem; height: 0.8rem; }
      .sw.pre { background: #9bd; }
      .sw.post { background: #2c7; }
    `,
  ],
})
export class TeacherSessionComponent implements OnInit, OnDestroy {
  code = '';
  state = signal<SessionState | null>(null);
  questions = signal<Question[]>([]);
  comparisons = signal<Record<number, Comparison>>({});
  qrDataUrl = signal<string>('');
  joinUrl = signal<string>('');
  private teardown?: () => void;

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
    });
  }

  ngOnDestroy(): void {
    this.teardown?.();
  }

  private refreshState(): void {
    this.api.sessionState(this.code).subscribe((s) => {
      this.state.set(s);
      this.loadQuestions();
    });
  }

  private loadQuestions(): void {
    const st = this.state();
    if (!st || this.questions().length) return;
    // Fetch the quiz behind this session to list its questions (teacher view
    // includes the correct-answer flags).
    this.api.listQuizzes().subscribe((quizzes: Quiz[]) => {
      const quiz = quizzes.find((q) => q.title === st.quiz_title);
      if (quiz) {
        this.questions.set(quiz.questions);
        this.refreshComparisons();
      }
    });
  }

  anyOpen(): boolean {
    return !!this.state()?.open_round;
  }

  openRoundFor(q: Question) {
    const r = this.state()?.open_round;
    return r && r.question_id === q.id ? r : null;
  }

  open(q: Question, phase: 'pre' | 'post'): void {
    this.api.openRound(this.code, q.id, phase).subscribe({ error: () => this.refreshState() });
  }

  close(roundId: number): void {
    this.api.closeRound(this.code, roundId).subscribe();
  }

  private refreshComparisons(): void {
    for (const q of this.questions()) {
      this.api.comparison(this.code, q.id).subscribe((cmp) => {
        this.comparisons.update((m) => ({ ...m, [q.id]: cmp }));
      });
    }
  }

  pct(counts: Record<number, number> | null, choiceId: number, q: Question): number {
    if (!counts) return 0;
    const total = q.choices.reduce((sum, c) => sum + (counts[c.id] || 0), 0);
    return total ? Math.round((100 * (counts[choiceId] || 0)) / total) : 0;
  }
}
