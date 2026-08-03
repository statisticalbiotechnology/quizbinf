import { Injectable, signal } from '@angular/core';
import { Observable, catchError, map, of, shareReplay, tap } from 'rxjs';

import { ApiService } from './api.service';
import { User } from './models';

@Injectable({ providedIn: 'root' })
export class AuthService {
  readonly user = signal<User | null>(null);
  readonly loaded = signal(false);

  constructor(private api: ApiService) {}

  private inFlight?: Observable<User | null>;

  /** Called once at startup to hydrate the current user (if the cookie is valid). */
  init(): void {
    this.ensureLoaded().subscribe();
  }

  /**
   * Resolve who is logged in, fetching it at most once.
   *
   * Route guards must await this rather than reading `user()` directly: on a
   * cold load — a reload, a bookmark, or the QR link — the lookup has not
   * finished when the guard runs, so `user()` is null and a logged-in teacher
   * would be bounced to the login page.
   */
  ensureLoaded(): Observable<User | null> {
    if (this.loaded()) return of(this.user());
    if (!this.inFlight) {
      this.inFlight = this.api.me().pipe(
        catchError(() => of(null)),
        tap((u) => {
          this.user.set(u);
          this.loaded.set(true);
        }),
        shareReplay(1),
      );
    }
    return this.inFlight;
  }

  /** Drop the cached identity after the server rejects our cookie. */
  forgetUser(): void {
    this.user.set(null);
    this.loaded.set(true);
    this.inFlight = undefined;
  }

  mockLogin(username: string, displayName = ''): Observable<User> {
    return this.api.mockLogin(username, displayName).pipe(tap((u) => this.user.set(u)));
  }

  logout(): void {
    this.api.logout().subscribe(() => this.user.set(null));
  }

  isTeacher(): boolean {
    return this.user()?.role === 'teacher';
  }
}
