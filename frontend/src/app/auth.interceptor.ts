import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';

import { AuthService } from './auth.service';

/**
 * Send the user back to the login page when the server stops accepting their
 * cookie.
 *
 * Without this a rejected cookie surfaces as whatever each view makes of a
 * failed request — "Session not found.", a broken QR image — while the page
 * still looks logged in, which is impossible to act on. A cookie can be
 * rejected because it expired, or because the server's session secret changed
 * (which happens on every restart when no writable volume is mounted).
 */
export const authErrorInterceptor: HttpInterceptorFn = (req, next) => {
  const router = inject(Router);
  const auth = inject(AuthService);

  return next(req).pipe(
    catchError((err: unknown) => {
      const is401 = err instanceof HttpErrorResponse && err.status === 401;
      // /api/auth/me answers 401 for a logged-out visitor by design; that is
      // the question being asked, not a session that went stale.
      const isAuthProbe = req.url.includes('/api/auth/');
      if (is401 && !isAuthProbe) {
        auth.forgetUser();
        const here = router.url;
        if (!here.startsWith('/login')) {
          router.navigate(['/login'], { queryParams: { next: here } });
        }
      }
      return throwError(() => err);
    }),
  );
};
