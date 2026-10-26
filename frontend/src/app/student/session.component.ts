import { Component, OnDestroy, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';

import { ApiService } from '../api.service';
import { AuthService } from '../auth.service';
import { SessionState } from '../models';

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
  imports: [FormsModule],
  template: `
    <div class="wrap">
      @if (state(); as s) {
        <h2>{{ s.quiz_title }}</h2>

        @if (s.open_round && s.question) {
          <p class="phase">
            {{ s.open_round.phase === 'pre' ? 'First answer' : 'Answer again after discussion' }}
          </p>
          <div class="qtext" [innerHTML]="s.question.text_html"></div>
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
                  {{ c.text }}
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
