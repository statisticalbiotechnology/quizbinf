import { Component, OnInit } from '@angular/core';
import { Router, RouterLink } from '@angular/router';

import { AuthService } from './auth.service';

/**
 * Where someone lands who opens the app directly rather than scanning the QR.
 *
 * Without this the root path redirected to /login, so a student who signed in
 * from the front page was sent straight back to the form they had just
 * completed — it looked as though the login had failed.
 */
@Component({
  selector: 'app-home',
  standalone: true,
  imports: [RouterLink],
  template: `
    <div class="wrap">
      <h1>quizbinf</h1>
      @if (auth.user(); as u) {
        <p class="who">Signed in as <strong>{{ u.display_name }}</strong>.</p>
        <p>
          Scan the QR code shown in the lecture to join the session and answer
          the question.
        </p>
        @if (u.role === 'teacher') {
          <p><a routerLink="/teacher">Go to your quizzes →</a></p>
        }
      } @else {
        <p>An in-class quiz for the bioinformatics course.</p>
        <p><a routerLink="/login">Sign in →</a></p>
      }
    </div>
  `,
  styles: [
    `
      .wrap { max-width: 24rem; margin: 3rem auto; padding: 1.5rem; text-align: center; }
      .who { color: #2c7a51; }
    `,
  ],
})
export class HomeComponent implements OnInit {
  constructor(public auth: AuthService, private router: Router) {}

  ngOnInit(): void {
    this.auth.init();
  }
}
