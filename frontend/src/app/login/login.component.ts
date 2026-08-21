import { Component, OnInit, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { FormsModule } from '@angular/forms';

import { API_BASE } from '../api.config';
import { ApiService } from '../api.service';
import { LoginMethods } from '../models';
import { AuthService } from '../auth.service';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [FormsModule],
  template: `
    <div class="card">
      <h1>quizbinf</h1>
      <p>In-class quiz for the bioinformatics course.</p>

      @if (methods()?.oidc) {
        <!-- Real authentication, so it goes first and reads as the way in.
             The server does the whole flow; this is a plain link because it
             is a browser redirect to KTH, not an API call. -->
        <a class="kth primary" [href]="oidcUrl()">Log in with your KTH-id</a>
        @if (loginError) {
          <p class="error">{{ loginError }}</p>
        }
      }

      @if (methods()?.roster_login) {
        <!-- Roster identification: a stop-gap until a real identity provider
             is available. Deliberately a typed address rather than a list of
             the class — a dropdown would publish the roster to anyone who
             opens this page. -->
        <div class="roster">
          <label for="email">Your KTH email address</label>
          <input
            id="email"
            type="email"
            name="email"
            autocomplete="off"
            [(ngModel)]="email"
            placeholder="username@kth.se"
            (ngModelChange)="onEmailTyped()"
            (keydown)="onKey($event)"
          />

          @if (suggestions().length) {
            <ul class="suggestions">
              @for (s of suggestions(); track s; let i = $index) {
                <li
                  [class.active]="i === highlighted()"
                  (mousedown)="choose(s)"
                  (mouseenter)="highlighted.set(i)"
                >
                  {{ s }}
                </li>
              }
            </ul>
          }

          <label for="password">Teacher password <span class="opt">— students leave blank</span></label>
          <input
            id="password"
            type="password"
            name="password"
            autocomplete="current-password"
            [(ngModel)]="password"
            (keyup.enter)="rosterLogin()"
          />

          <button (click)="rosterLogin()" [disabled]="!email.trim() || busy()">
            {{ busy() ? 'Checking…' : 'Continue' }}
          </button>
          <p class="hint">
            You must be registered on the course in Canvas. Ask the teacher if
            you have only just registered.
          </p>
          @if (error) {
            <p class="error">{{ error }}</p>
          }
        </div>
      }

      @if (methods()?.mock_login) {
        <!-- Development-only login that skips the IdP entirely. -->
        <div class="mock">
          <label>KTH username (mock login)</label>
          <input [(ngModel)]="username" placeholder="e.g. lukask" (keyup.enter)="login()" />
          <button (click)="login()" [disabled]="!username.trim()">Log in</button>
          @if (error && !methods()?.roster_login) {
            <p class="error">{{ error }}</p>
          }
        </div>
      }

      @if (methods() && !methods()!.mock_login && !methods()!.roster_login && !methods()!.oidc) {
        <p class="note">
          No login method is configured on this deployment. Ask the course
          teacher.
        </p>
      }
    </div>
  `,
  styles: [
    `
      .card { max-width: 22rem; margin: 3rem auto; padding: 1.5rem; text-align: center; }
      input { display: block; width: 100%; padding: 0.6rem; margin: 0.5rem 0; }
      button { padding: 0.6rem 1.2rem; }
      .kth { display: inline-block; margin-top: 0.5rem; }
      .kth.primary { display: block; padding: 0.7rem 1rem; border-radius: 6px;
                     background: #2c7a51; color: #fff; text-decoration: none;
                     font-weight: 600; margin-bottom: 0.5rem; }
      .note { font-size: 0.85rem; color: #666; }
      .roster { text-align: left; position: relative; }
      .suggestions { list-style: none; margin: -0.3rem 0 0; padding: 0;
                     border: 1px solid var(--border, #ccc); border-radius: 6px;
                     max-height: 12rem; overflow-y: auto; background: #fff; }
      .suggestions li { padding: 0.45rem 0.6rem; cursor: pointer; font-size: 0.9rem; }
      .suggestions li.active, .suggestions li:hover { background: #eef4ff; }
      .roster label { display: block; font-size: 0.85rem; color: #555; margin-top: 0.6rem; }
      .roster .opt { color: #888; font-weight: 400; }
      .roster button { width: 100%; margin-top: 0.8rem; }
      .hint { font-size: 0.8rem; color: #777; }
      .mock { margin-top: 1.5rem; padding-top: 1rem; border-top: 1px dashed #ddd; }
      .error { color: #c0392b; }
    `,
  ],
})
export class LoginComponent implements OnInit {
  username = '';
  email = '';
  password = '';
  error = '';
  loginError = '';
  busy = signal(false);
  methods = signal<LoginMethods | null>(null);
  suggestions = signal<string[]>([]);
  highlighted = signal(-1);
  private suggestTimer?: ReturnType<typeof setTimeout>;

  constructor(
    private auth: AuthService,
    private api: ApiService,
    private router: Router,
    private route: ActivatedRoute,
  ) {}

  /**
   * The server-side flow, carrying where to return to. A full page navigation
   * rather than an HTTP call: the browser has to follow redirects to KTH and
   * back for the provider's own session cookie to be involved.
   */
  oidcUrl(): string {
    const next = this.route.snapshot.queryParamMap.get('next') || '/';
    return `${API_BASE}/api/auth/login?next=${encodeURIComponent(next)}`;
  }

  ngOnInit(): void {
    // The callback sends a declined or failed KTH login back here, so say so
    // rather than silently showing the form again as if nothing happened.
    if (this.route.snapshot.queryParamMap.get('error') === 'oidc') {
      this.loginError = 'KTH login did not complete. Please try again.';
    }

    // Which form to show is the server's business — a deployment may offer
    // roster identification, mock login, or neither.
    this.api.loginMethods().subscribe({
      next: (m) => this.methods.set(m),
      error: () => this.methods.set({ mock_login: false, roster_login: false, oidc: false }),
    });
  }

  /**
   * Narrow the roster as the student types.
   *
   * Debounced, and the server returns nothing until enough has been typed —
   * the list is the class, so it is fetched a few matches at a time rather
   * than handed over whole when the page loads.
   */
  onEmailTyped(): void {
    if (this.suggestTimer) clearTimeout(this.suggestTimer);
    this.highlighted.set(-1);
    const typed = this.email.trim();
    if (typed.length < 3) {
      this.suggestions.set([]);
      return;
    }
    this.suggestTimer = setTimeout(() => {
      this.api.rosterSuggest(typed).subscribe({
        next: ({ matches }) => {
          // A single exact match is not a suggestion, it is what they typed.
          this.suggestions.set(matches.length === 1 && matches[0] === typed ? [] : matches);
        },
        error: () => this.suggestions.set([]),
      });
    }, 200);
  }

  /** Arrow keys and Enter drive the list, as a picker should. */
  onKey(event: KeyboardEvent): void {
    const list = this.suggestions();
    if (event.key === 'Enter') {
      const chosen = list[this.highlighted()];
      if (chosen) {
        event.preventDefault();
        this.choose(chosen);
      } else {
        this.rosterLogin();
      }
      return;
    }
    if (!list.length) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      this.highlighted.set((this.highlighted() + 1) % list.length);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      this.highlighted.set((this.highlighted() - 1 + list.length) % list.length);
    } else if (event.key === 'Escape') {
      this.suggestions.set([]);
    }
  }

  choose(address: string): void {
    this.email = address;
    this.suggestions.set([]);
    this.highlighted.set(-1);
  }

  /** Back to whatever sent us here — the scanned session, usually. */
  private goOnward(): void {
    const next = this.route.snapshot.queryParamMap.get('next');
    this.router.navigateByUrl(next || (this.auth.isTeacher() ? '/teacher' : '/'));
  }

  rosterLogin(): void {
    if (!this.email.trim()) return;
    this.error = '';
    this.busy.set(true);
    this.auth.rosterLogin(this.email.trim(), this.password).subscribe({
      next: () => {
        this.busy.set(false);
        this.goOnward();
      },
      error: (err) => {
        this.busy.set(false);
        this.error = err?.error?.detail ?? 'Could not sign you in.';
      },
    });
  }

  login(): void {
    this.error = '';
    this.auth.mockLogin(this.username.trim()).subscribe({
      next: () => this.goOnward(),
      error: () => (this.error = 'Login failed (is mock login enabled on the server?)'),
    });
  }
}
