import { Component, OnDestroy, OnInit, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';

import { ApiService } from '../api.service';
import { AuthService } from '../auth.service';
import { SessionState } from '../models';

@Component({
  selector: 'app-student-session',
  standalone: true,
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
          @if (selected()) {
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
      /* Markdown output: keep figures and tables inside the phone screen. */
      .qtext img { max-width: 100%; height: auto; border-radius: 6px; }
      .qtext pre { overflow-x: auto; background: #f5f5f5; padding: 0.5rem;
                   border-radius: 4px; }
      .qtext code { background: #f5f5f5; padding: 0.1rem 0.25rem; border-radius: 3px; }
      .qtext table { border-collapse: collapse; }
      .qtext th, .qtext td { border: 1px solid #ddd; padding: 0.2rem 0.4rem; }
      img { max-width: 100%; border-radius: 6px; }
      .choices { display: flex; flex-direction: column; gap: 0.6rem; }
      .choice { padding: 1rem; text-align: left; border: 2px solid #ccc; border-radius: 8px;
                background: #fff; font-size: 1rem; }
      .choice.selected { border-color: #2c7; background: #eafaf1; }
      .waiting { color: #777; text-align: center; margin-top: 2rem; }
      .saved { color: #2c7; margin-top: 1rem; }
      .error { color: #c0392b; }
    `,
  ],
})
export class StudentSessionComponent implements OnInit, OnDestroy {
  state = signal<SessionState | null>(null);
  selected = signal<number | null>(null);
  error = signal<string>('');
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
    // The server tells us our own current choice; trust it over local state.
    this.selected.set(s.my_choice_id);
  }

  answer(choiceId: number): void {
    this.api.submitAnswer(this.code, choiceId).subscribe({
      next: () => this.selected.set(choiceId),
      error: () => this.resync(), // round probably closed; refresh truth
    });
  }
}
