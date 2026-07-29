import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { AuthService } from './auth.service';

/** Requires the teacher role; sends others to login (or home if already a student). */
export const teacherGuard: CanActivateFn = (_route, state) => {
  const auth = inject(AuthService);
  const router = inject(Router);
  if (auth.isTeacher()) return true;
  if (auth.user()) return router.parseUrl('/'); // logged in but not a teacher
  return router.parseUrl(`/login?next=${encodeURIComponent(state.url)}`);
};
