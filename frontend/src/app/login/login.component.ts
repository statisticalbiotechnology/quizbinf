import { Component, OnInit, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { FormsModule } from '@angular/forms';

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
            autocomplete="email"
            [(ngModel)]="email"
            placeholder="username@kth.se"
            (keyup.enter)="rosterLogin()"
          />

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

      @if (methods() && !methods()!.mock_login && !methods()!.roster_login) {
        <a class="kth" href="/api/auth/login">Log in with KTH-id</a>
      }
    </div>
  `,
  styles: [
    `
      .card { max-width: 22rem; margin: 3rem auto; padding: 1.5rem; text-align: center; }
      input { display: block; width: 100%; padding: 0.6rem; margin: 0.5rem 0; }
      button { padding: 0.6rem 1.2rem; }
      .kth { display: inline-block; margin-top: 1.5rem; }
      .roster { text-align: left; }
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
  busy = signal(false);
  methods = signal<LoginMethods | null>(null);

  constructor(
    private auth: AuthService,
    private api: ApiService,
    private router: Router,
    private route: ActivatedRoute,
  ) {}

  ngOnInit(): void {
    // Which form to show is the server's business — a deployment may offer
    // roster identification, mock login, or neither.
    this.api.loginMethods().subscribe({
      next: (m) => this.methods.set(m),
      error: () => this.methods.set({ mock_login: false, roster_login: false, oidc: false }),
    });
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
