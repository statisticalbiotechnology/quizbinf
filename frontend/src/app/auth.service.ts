import { Injectable, signal } from '@angular/core';
import { Observable, tap } from 'rxjs';

import { ApiService } from './api.service';
import { User } from './models';

@Injectable({ providedIn: 'root' })
export class AuthService {
  readonly user = signal<User | null>(null);
  readonly loaded = signal(false);

  constructor(private api: ApiService) {}

  /** Called once at startup to hydrate the current user (if the cookie is valid). */
  init(): void {
    this.api.me().subscribe({
      next: (u) => {
        this.user.set(u);
        this.loaded.set(true);
      },
      error: () => {
        this.user.set(null);
        this.loaded.set(true);
      },
    });
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
