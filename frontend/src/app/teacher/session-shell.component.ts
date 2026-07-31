import { Component, OnInit } from '@angular/core';
import { ActivatedRoute, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { SessionFeed } from './session-feed.service';

/**
 * Frame around the three teacher views of a running session.
 *
 * Each view is its own URL so it can be opened in a separate window — project
 * Join or Report on the lecture hall screen while driving Control from the
 * laptop.
 */
@Component({
  selector: 'app-teacher-session-shell',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  providers: [SessionFeed],
  template: `
    <nav class="views">
      <a routerLink="join" routerLinkActive="active">Join</a>
      <a routerLink="control" routerLinkActive="active">Control</a>
      <a routerLink="report" routerLinkActive="active">Report</a>
      <span class="hint">each view has its own URL — open one in a second window to project it</span>
    </nav>

    @if (feed.error(); as e) {
      <p class="error">{{ e }}</p>
    }

    <router-outlet />
  `,
  styles: [
    `
      .views { display: flex; gap: 0.5rem; align-items: center; max-width: 46rem;
               margin: 1rem auto 0; padding: 0 1rem; }
      .views a { padding: 0.4rem 0.9rem; border: 1px solid #ddd; border-radius: 6px;
                 text-decoration: none; color: inherit; }
      .views a.active { background: #2c7a51; color: #fff; border-color: #2c7a51; }
      .hint { font-size: 0.8rem; color: #888; margin-left: auto; }
      .error { max-width: 46rem; margin: 1rem auto; padding: 0 1rem; color: #c0392b; }
    `,
  ],
})
export class TeacherSessionShellComponent implements OnInit {
  constructor(public feed: SessionFeed, private route: ActivatedRoute) {}

  ngOnInit(): void {
    this.feed.init(this.route.snapshot.paramMap.get('code') || '');
  }
}
