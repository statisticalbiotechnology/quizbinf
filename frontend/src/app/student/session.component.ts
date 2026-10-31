import { Component, OnDestroy, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';

import { ApiService } from '../api.service';
import { MarkdownHtmlDirective } from '../md-html.directive';
import { AuthService } from '../auth.service';
import { MyResult, SessionState } from '../models';

/** How many times a phone re-sends an answer the server was too busy to take. */
const SEND_RETRIES = 3;
/** The first wait, doubled each attempt and jittered — see `answer()`. */
const SEND_BACKOFF_MS = 400;
/**
 * Statuses that mean the answer never reached a server with an opinion on it:
 * 0 is a dropped connection, 503 the app shedding load, and 502 and 504 the
 * proxy in front of the app failing to reach it.
 */
const RETRYABLE_STATUSES = [0, 502, 503, 504];

@Component({
  selector: 'app-student-session',
  standalone: true,
  imports: [FormsModule, MarkdownHtmlDirective],
  template: `
    <div class="wrap">
      @if (state(); as s) {
        <h2>{{ s.quiz_title }}</h2>

        @if (s.open_round && s.question) {
          <p class="phase">
            {{ s.open_round.phase === 'pre' ? 'First answer' : 'Answer again after discussion' }}
          </p>
          <div class="qtext" [mdHtml]="s.question.text_html"></div>
          @if (s.question.image_url) {
            <img [src]="s.question.image_url" alt="" />
          }
          @if (s.question.kind === 'text') {
            <div class="word" [class.several]="words.length > 1">
              @for (w of words; track $index) {
                <input
                  type="text"
                  [(ngModel)]="words[$index]"
                  [name]="'word' + $index"
                  maxlength="40"
                  [placeholder]="words.length > 1 ? 'Word ' + ($index + 1) : 'One word or a short phrase'"
                  autocomplete="off"
                  (keyup.enter)="sendWord()"
                />
              }
              <button class="send" (click)="sendWord()" [disabled]="!hasWords()">
                {{ sent() ? 'Change' : 'Send' }}
              </button>
            </div>
          } @else {
            <div class="choices">
              @for (c of s.question.choices; track c.id) {
                <button
                  class="choice"
                  [class.selected]="selected() === c.id"
                  (click)="answer(c.id)"
                >
                  <span class="choice-md" [mdHtml]="c.text_html"></span>
                </button>
              }
            </div>
          }
          @if (sending()) {
            <p class="sending">Sending…</p>
          } @else if (sendError()) {
            <p class="error">{{ sendError() }}</p>
          } @else if (selected() || sent()) {
            <p class="saved">Answer saved — you can change it while the question is open.</p>
          }
        } @else {
          <p class="waiting">Waiting for the teacher to open a question…</p>
          @if (results().length) {
            <section class="results">
              <h3>Finished questions</h3>
              @for (r of results(); track r.question.id; let first = $first) {
                <details class="result" [open]="first">
                  <summary>
                    <span class="arrow">▸</span>
                    <div class="rq" [mdHtml]="r.question.text_html"></div>
                  </summary>
                  <p class="legend">
                    <span class="key pre"></span> first answer
                    <span class="key post"></span> after discussion
                    <span class="n">{{ total(r.post) }} answered</span>
                  </p>
                  @for (c of r.question.choices; track c.id) {
                    <div class="rchoice" [class.correct]="c.is_correct">
                      <div class="rlabel">
                        @if (c.is_correct) {
                          <span class="tick" title="Correct">✓ </span>
                        }
                        <span class="choice-md" [mdHtml]="c.text_html"></span>
                        @if (r.my_pre === c.id || r.my_post === c.id) {
                          <span class="mine">{{ mine(r, c.id) }}</span>
                        }
                      </div>
                      <div class="bar pre">
                        <span class="track"><i [style.width.%]="share(r.pre, c.id)"></i></span>
                        <b>{{ share(r.pre, c.id) }}%</b>
                      </div>
                      <div class="bar post">
                        <span class="track"><i [style.width.%]="share(r.post, c.id)"></i></span>
                        <b>{{ share(r.post, c.id) }}%</b>
                      </div>
                    </div>
                  }
                  @if (r.my_pre === null && r.my_post === null) {
                    <p class="note">You did not answer this question.</p>
                  }
                </details>
              }
            </section>
          }
        }
      } @else if (error()) {
        <p class="error">{{ error() }}</p>
      } @else {
        <p>Loading…</p>
      }
    </div>
  `,
  styles: [
    `
      .wrap { max-width: 30rem; margin: 1.5rem auto; padding: 1rem; }
      .phase { font-weight: 600; color: #2c7; text-transform: uppercase; font-size: 0.8rem; }
      .qtext { font-size: 1.2rem; margin: 0.5rem 0 1rem; }
      /* Figures, <pre> and tables in rendered Markdown are sized in
         styles.scss — a rule written here would not reach them. */
      .qtext pre { background: #f5f5f5; padding: 0.5rem; border-radius: 4px; }
      .qtext code { background: #f5f5f5; padding: 0.1rem 0.25rem; border-radius: 3px; }
      .qtext th, .qtext td { border: 1px solid #ddd; padding: 0.2rem 0.4rem; }
      img { max-width: 100%; height: auto; border-radius: 6px; }
      .choices { display: flex; flex-direction: column; gap: 0.6rem; }
      .choice { padding: 1rem; text-align: left; border: 2px solid #ccc; border-radius: 8px;
                background: #fff; font-size: 1rem; }
      .choice.selected { border-color: #2c7; background: #eafaf1; }
      .word { display: flex; gap: 0.5rem; }
      .word input { flex: 1; padding: 0.9rem; font-size: 1rem; border: 2px solid #ccc;
                    border-radius: 8px; }
      .word .send { padding: 0.9rem 1.2rem; font-size: 1rem; border-radius: 8px;
                    border: 2px solid #2c7; background: #eafaf1; }
      .word.several { flex-direction: column; }
      .waiting { color: #777; text-align: center; margin-top: 2rem; }
      .saved { color: #2c7; margin-top: 1rem; }
      .sending { color: #777; margin-top: 1rem; }
      .error { color: #c0392b; }
      .results { margin-top: 2rem; }
      .results h3 { font-size: 1rem; color: #555; }
      .result { border: 1px solid #ddd; border-radius: 8px; padding: 0.6rem 0.8rem;
                margin-bottom: 0.8rem; }
      .result summary { cursor: pointer; font-weight: 600; list-style: none; display: flex;
                        gap: 0.4rem; align-items: baseline; }
      .result summary::-webkit-details-marker { display: none; }
      .arrow { display: inline-block; transition: transform 0.15s; }
      .result[open] .arrow { transform: rotate(90deg); }
      .rq { flex: 1; }
      .legend { font-size: 0.8rem; color: #555; display: flex; gap: 0.4rem;
                align-items: center; flex-wrap: wrap; }
      .legend .n { margin-left: auto; }
      .key { display: inline-block; width: 0.8rem; height: 0.8rem; border-radius: 2px; }
      .key.pre, .bar.pre i { background: #b8c4d6; }
      .key.post, .bar.post i { background: #2c7be5; }
      .rchoice { margin: 0.7rem 0; }
      .rlabel .mine { margin-left: 0.3rem; white-space: nowrap; }
      .rchoice.correct .rlabel { font-weight: 600; }
      .tick { color: #2c7; }
      .mine { font-size: 0.75rem; background: #fff3cd; border: 1px solid #e0c060;
              border-radius: 4px; padding: 0 0.3rem; font-weight: normal; }
      .bar { display: flex; align-items: center; gap: 0.4rem; margin-top: 0.2rem; }
      .track { flex: 1; height: 0.9rem; background: #f2f2f2; border-radius: 3px;
               overflow: hidden; }
      .track i { display: block; height: 100%; }
      .bar b { width: 2.6rem; text-align: right; font-size: 0.75rem; font-weight: normal;
               color: #555; }
      .note { color: #777; font-size: 0.9rem; }
    `,
  ],
})
export class StudentSessionComponent implements OnInit, OnDestroy {
  state = signal<SessionState | null>(null);
  selected = signal<number | null>(null);
  error = signal<string>('');
  sending = signal(false);
  /** Whether this student's word is in — the cloud's version of `selected`. */
  sent = signal(false);
  /** What they are writing, for a word-cloud question. */
  words: string[] = [''];
  /** The question `words` was laid out for, so a new one starts empty. */
  private wordsFor: number | null = null;
  sendError = signal<string>('');
  /** Questions whose second bout is halted, newest first. */
  results = signal<MyResult[]>([]);
  /** The open round when state last arrived, to notice one being halted. */
  private lastRound: number | null | undefined = undefined;
  private resultsTimer?: ReturnType<typeof setTimeout>;
  private code = '';
  private teardown?: () => void;

  constructor(
    private api: ApiService,
    private auth: AuthService,
    private route: ActivatedRoute,
    private router: Router,
  ) {}

  ngOnInit(): void {
    this.code = this.route.snapshot.paramMap.get('code') || '';
    // Do not decide on auth from auth.user() here: this page is loaded fresh
    // when a student scans the QR code, and the session lookup that populates
    // it has not resolved yet, so an already-logged-in student would be
    // bounced to the login page every time. Ask the server instead and treat
    // 401 as "needs login".
    this.resync();
    // Follow live state changes; also resync on connect since events sent
    // while disconnected are missed.
    this.teardown = this.api.streamState(this.code, (s) => this.applyState(s));
  }

  ngOnDestroy(): void {
    this.teardown?.();
    clearTimeout(this.resultsTimer);
  }

  /**
   * Fetch the finished questions, once a round has been halted.
   *
   * Every phone in the room learns of the halt from the same SSE event, so
   * the fetch is spread over a couple of seconds rather than arriving as one
   * burst; nobody is reading results to the millisecond.
   */
  private loadResults(spread: boolean): void {
    clearTimeout(this.resultsTimer);
    const fetch = () =>
      this.api.myResults(this.code).subscribe({
        next: (r) => this.results.set(r),
        error: () => {}, // the next halt tries again
      });
    if (!spread) {
      fetch();
      return;
    }
    this.resultsTimer = setTimeout(fetch, Math.random() * 2500);
  }

  share(counts: Record<number, number>, choiceId: number): number {
    const total = this.total(counts);
    return total ? Math.round((100 * (counts[choiceId] ?? 0)) / total) : 0;
  }

  total(counts: Record<number, number>): number {
    return Object.values(counts).reduce((a, b) => a + b, 0);
  }

  mine(r: MyResult, choiceId: number): string {
    if (r.my_pre === choiceId && r.my_post === choiceId) return 'you, both times';
    return r.my_pre === choiceId ? 'you, first' : 'you, after discussion';
  }

  private resync(): void {
    this.api.sessionState(this.code).subscribe({
      next: (s) => this.applyState(s),
      error: (err) => {
        if (err?.status === 401) {
          // Send them to log in, then straight back to this session.
          this.router.navigate(['/login'], {
            queryParams: { next: `/s/${this.code}` },
          });
          return;
        }
        this.error.set('Session not found.');
      },
    });
  }

  private applyState(s: SessionState): void {
    const round = s.open_round?.id ?? null;
    if (round === null && this.lastRound !== null) {
      // Arrived, or a round was just halted: the latter may finish a question.
      this.loadResults(this.lastRound !== undefined);
    }
    this.lastRound = round;
    this.state.set(s);
    // The server tells us our own current answer; trust it over local state.
    this.selected.set(s.my_choice_id);
    this.sent.set(!!s.my_text);
    const q = s.question;
    if (q?.kind !== 'text') return;
    const mine = s.my_words ?? (s.my_text ? [s.my_text] : []);
    const slots = Math.max(1, q.max_words ?? 1);
    if (q.id !== this.wordsFor) {
      this.wordsFor = q.id;
      this.words = Array.from({ length: slots }, (_, i) => mine[i] ?? '');
    } else if (!this.hasWords() && mine.length) {
      // Only adopt the server's words when the boxes are untouched, so a
      // resync mid-typing does not overwrite what is being written.
      this.words = Array.from({ length: slots }, (_, i) => mine[i] ?? '');
    }
  }

  hasWords(): boolean {
    return this.words.some((w) => w.trim());
  }

  /**
   * Send a word for the cloud.
   *
   * The same submission as a choice, with the same retry policy behind it:
   * the whole room writes at once, and a shed or lost request is a student
   * who looks like they were not there.
   */
  sendWord(attempt = 0): void {
    const words = this.words.map((w) => w.trim()).filter((w) => w);
    if (!words.length) return;
    this.sending.set(true);
    this.sendError.set('');
    this.api.submitWords(this.code, words).subscribe({
      next: () => {
        this.sent.set(true);
        this.sending.set(false);
      },
      error: (err) => {
        const busy = RETRYABLE_STATUSES.includes(err?.status);
        if (busy && attempt < SEND_RETRIES) {
          const wait = SEND_BACKOFF_MS * Math.pow(2, attempt) * (0.5 + Math.random());
          setTimeout(() => this.sendWord(attempt + 1), wait);
          return;
        }
        this.sending.set(false);
        if (busy) {
          this.sendError.set('Too many at once — send it again.');
          return;
        }
        this.resync(); // round probably closed; refresh truth
      },
    });
  }

  /**
   * Submit an answer, retrying if the server is merely busy.
   *
   * A whole class taps at once, so the server may turn a submission away
   * (503) to keep from starving itself. That is a "wait a moment", not a
   * refusal — but the student cannot tell the difference from a spinner, and
   * the submission window is short, so the phone retries rather than asking
   * them to notice and tap again. The waits are staggered by a random amount
   * because the reason for the 503 was everyone acting in unison; retrying in
   * unison would recreate it.
   *
   * Only a busy server and a dropped connection are retried. Every other
   * error means the server has an opinion — most often that the round has
   * closed — and the answer to that is to go and get the truth, not to insist.
   *
   * A 502 or 504 counts as a dropped connection. It is written by Serve's
   * proxy, not by this app, and says the proxy lost its way to the app: a
   * load test at the deployment got 502s under a burst and saw the proxy cut
   * every open connection at once. Re-sending is safe even if the first try
   * was recorded, because an answer is one row per student per round and a
   * repeat overwrites it with the same choice.
   */
  answer(choiceId: number, attempt = 0): void {
    this.sending.set(true);
    this.sendError.set('');
    this.api.submitAnswer(this.code, choiceId).subscribe({
      next: () => {
        this.selected.set(choiceId);
        this.sending.set(false);
      },
      error: (err) => {
        const busy = RETRYABLE_STATUSES.includes(err?.status);
        if (busy && attempt < SEND_RETRIES) {
          const wait = SEND_BACKOFF_MS * Math.pow(2, attempt) * (0.5 + Math.random());
          setTimeout(() => this.answer(choiceId, attempt + 1), wait);
          return;
        }
        this.sending.set(false);
        if (busy) {
          // Say so plainly and leave the choices tappable: the answer was not
          // recorded, and the student is the only one who can send it again.
          this.sendError.set('Too many at once — tap your answer again.');
          return;
        }
        this.resync(); // round probably closed; refresh truth
      },
    });
  }
}
