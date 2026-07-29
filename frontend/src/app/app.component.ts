import { Component, OnInit } from '@angular/core';
import { RouterLink, RouterOutlet } from '@angular/router';

import { AuthService } from './auth.service';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink],
  template: `
    <header class="topbar">
      <a routerLink="/" class="brand">quizbinf</a>
      @if (auth.user(); as u) {
        <span class="who">
          {{ u.display_name }}
          @if (u.role === 'teacher') {
            · <a routerLink="/teacher">dashboard</a>
          }
          · <a href="#" (click)="logout($event)">log out</a>
        </span>
      }
    </header>
    <main>
      <router-outlet />
    </main>
  `,
  styles: [
    `
      .topbar { display: flex; justify-content: space-between; align-items: center;
                padding: 0.6rem 1rem; border-bottom: 1px solid #eee; }
      .brand { font-weight: 700; text-decoration: none; color: inherit; }
      .who { font-size: 0.9rem; color: #555; }
    `,
  ],
})
export class AppComponent implements OnInit {
  constructor(public auth: AuthService) {}

  ngOnInit(): void {
    this.auth.init();
  }

  logout(ev: Event): void {
    ev.preventDefault();
    this.auth.logout();
  }
}
