import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { ApiService } from './api.service';
import { SessionState } from './models';

describe('ApiService', () => {
  let api: ApiService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [ApiService, provideHttpClient(), provideHttpClientTesting()],
    });
    api = TestBed.inject(ApiService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('submits an answer with credentials to the session answers endpoint', () => {
    api.submitAnswer('abc123', 42).subscribe();
    const req = http.expectOne((r) => r.url.endsWith('/api/sessions/abc123/answers'));
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ choice_id: 42 });
    expect(req.request.withCredentials).toBeTrue();
    req.flush({ ok: true });
  });

  it('fetches session state for follow/resync', () => {
    const state: SessionState = {
      code: 'abc123',
      quiz_id: 1,
      quiz_title: 'Bioinf',
      open_round: null,
      question: null,
      my_choice_id: null,
    };
    let received: SessionState | undefined;
    api.sessionState('abc123').subscribe((s) => (received = s));
    http.expectOne((r) => r.url.endsWith('/api/sessions/abc123/state')).flush(state);
    expect(received).toEqual(state);
  });

  it('opens a round with the requested phase', () => {
    api.openRound('abc123', 7, 'post').subscribe();
    const req = http.expectOne((r) => r.url.endsWith('/api/sessions/abc123/rounds'));
    expect(req.request.body).toEqual({ question_id: 7, phase: 'post' });
    req.flush({ id: 1, question_id: 7, phase: 'post', opened_at: '', closed_at: null });
  });
});
