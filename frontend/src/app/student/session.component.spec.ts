import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed, fakeAsync, tick } from '@angular/core/testing';
import { ActivatedRoute, Router } from '@angular/router';

import { ApiService } from '../api.service';
import { AuthService } from '../auth.service';
import { StudentSessionComponent } from './session.component';

/**
 * What a phone does when a whole class taps at the same second.
 *
 * The server sheds a request rather than starving itself, and a shed answer
 * is not a recorded answer — so the difference between retrying and not is
 * whether a student who pressed the button inside the window is counted as
 * having been there. Attendance is scored off exactly this.
 */
describe('StudentSessionComponent answering under load', () => {
  let component: StudentSessionComponent;
  let http: HttpTestingController;

  // Matched by endpoint rather than by full URL: the subject here is the
  // retry policy, and how the URL is built is already pinned in
  // api.service.spec.ts.
  const answers = () => http.match((r) => r.url.endsWith('/answers'));

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        ApiService,
        AuthService,
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: Router, useValue: { navigate: () => {} } },
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { paramMap: { get: () => 'abc123' } } },
        },
      ],
    });
    component = TestBed.runInInjectionContext(
      () =>
        new StudentSessionComponent(
          TestBed.inject(ApiService),
          TestBed.inject(AuthService),
          TestBed.inject(ActivatedRoute),
          TestBed.inject(Router),
        ),
    );
    http = TestBed.inject(HttpTestingController);
  });

  it('re-sends an answer the server was too busy to take', fakeAsync(() => {
    component.answer(42);

    // Turned away because 150 phones arrived together — not because the
    // round closed, so the answer is still wanted.
    answers()[0].flush(
      { detail: 'The server is busy. Please try again.' },
      { status: 503, statusText: 'Service Unavailable' },
    );
    expect(component.selected()).toBeNull();

    tick(2000); // past the jittered backoff
    const retry = answers();
    expect(retry.length).toBe(1);
    retry[0].flush({ ok: true, choice_id: 42 });

    expect(component.selected()).toBe(42);
    expect(component.sending()).toBeFalse();
    expect(component.sendError()).toBe('');
  }));

  it('gives up in the end and says so, rather than looking like it worked', fakeAsync(() => {
    component.answer(42);
    for (let i = 0; i < 5; i++) {
      for (const req of answers()) {
        req.flush({}, { status: 503, statusText: 'Service Unavailable' });
      }
      tick(10000);
    }

    // The choices stay tappable and the student is told to tap again: nothing
    // may imply the answer was recorded when it was not.
    expect(component.selected()).toBeNull();
    expect(component.sending()).toBeFalse();
    expect(component.sendError()).toContain('again');
  }));

  it('does not retry a closed round — it goes and gets the truth', fakeAsync(() => {
    component.answer(42);
    answers()[0].flush(
      { detail: 'No round is open' },
      { status: 409, statusText: 'Conflict' },
    );
    tick(5000);

    expect(answers().length).toBe(0);
    // A 409 means the server has an opinion; the client resyncs instead of
    // insisting, which is what stops a closed round accepting late answers.
    http.expectOne((r) => r.url.endsWith('/state')).flush({
      code: 'abc123',
      quiz_id: 1,
      quiz_title: 'Bioinf',
      open_round: null,
      question: null,
      my_choice_id: null,
    });
    expect(component.selected()).toBeNull();
  }));

  afterEach(() => http.verify());
});
