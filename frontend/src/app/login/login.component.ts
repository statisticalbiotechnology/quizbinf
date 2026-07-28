import { Component } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { FormsModule } from '@angular/forms';

import { AuthService } from '../auth.service';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [FormsModule],
  template: `
    <div class="card">
      <h1>quizbinf</h1>
      <p>In-class quiz for the bioinformatics course.</p>

      <!-- Development-only mock login. In production this is replaced by the
           KTH-id (OIDC) button below. -->
      <div class="mock">
        <label>KTH username (mock login)</label>
        <input [(ngModel)]="username" placeholder="e.g. lukask" (keyup.enter)="login()" />
        <button (click)="login()" [disabled]="!username.trim()">Log in</button>
        @if (error) {
          <p class="error">{{ error }}</p>
        }
      </div>

      <a class="kth" href="/api/auth/login">Log in with KTH-id</a>
    </div>
  `,
  styles: [
    `
      .card { max-width: 22rem; margin: 3rem auto; padding: 1.5rem; text-align: center; }
      input { display: block; width: 100%; padding: 0.6rem; margin: 0.5rem 0; }
      button { padding: 0.6rem 1.2rem; }
      .kth { display: inline-block; margin-top: 1.5rem; }
      .error { color: #c0392b; }
    `,
  ],
})
export class LoginComponent {
  username = '';
  error = '';

  constructor(
    private auth: AuthService,
    private router: Router,
    private route: ActivatedRoute,
  ) {}

  login(): void {
    this.error = '';
    this.auth.mockLogin(this.username.trim()).subscribe({
      next: () => {
        const next = this.route.snapshot.queryParamMap.get('next');
        this.router.navigateByUrl(next || (this.auth.isTeacher() ? '/teacher' : '/'));
      },
      error: () => (this.error = 'Login failed (is mock login enabled on the server?)'),
    });
  }
}
