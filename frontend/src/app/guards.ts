import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { map } from 'rxjs';

import { AuthService } from './auth.service';

/**
 * Requires the teacher role; sends others to login (or home if a student).
 *
 * Waits for the current user to be resolved first. Deciding from `user()`
 * synchronously bounced a logged-in teacher to the login page on every cold
 * load — a reload, or opening a session URL directly.
 */
export const teacherGuard: CanActivateFn = (_route, state) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  const decide = () => {
    if (auth.isTeacher()) return true;
    if (auth.user()) return router.parseUrl('/'); // logged in but not a teacher
    return router.parseUrl(`/login?next=${encodeURIComponent(state.url)}`);
  };

  return auth.loaded() ? decide() : auth.ensureLoaded().pipe(map(decide));
};
