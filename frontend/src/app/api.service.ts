import { HttpClient } from '@angular/common/http';
import { Injectable, NgZone } from '@angular/core';
import { Observable } from 'rxjs';

import { API_BASE } from './api.config';
import {
  Comparison,
  Histogram,
  LiveCount,
  Phase,
  Question,
  QuestionInput,
  Quiz,
  QuizSession,
  Round,
  SessionState,
  User,
} from './models';

@Injectable({ providedIn: 'root' })
export class ApiService {
  // withCredentials so the session cookie rides along in dev (cross-origin).
  private opts = { withCredentials: true } as const;

  constructor(private http: HttpClient, private zone: NgZone) {}

  // --- auth ---
  me(): Observable<User> {
    return this.http.get<User>(`${API_BASE}/api/auth/me`, this.opts);
  }
  mockLogin(username: string, displayName = ''): Observable<User> {
    return this.http.post<User>(
      `${API_BASE}/api/auth/mock-login`,
      { username, display_name: displayName },
      this.opts,
    );
  }
  logout(): Observable<unknown> {
    return this.http.post(`${API_BASE}/api/auth/logout`, {}, this.opts);
  }

  // --- teacher: quizzes ---
  listQuizzes(): Observable<Quiz[]> {
    return this.http.get<Quiz[]>(`${API_BASE}/api/quizzes`, this.opts);
  }
  createQuiz(title: string): Observable<Quiz> {
    return this.http.post<Quiz>(`${API_BASE}/api/quizzes`, { title }, this.opts);
  }
  getQuiz(id: number): Observable<Quiz> {
    return this.http.get<Quiz>(`${API_BASE}/api/quizzes/${id}`, this.opts);
  }
  addQuestion(quizId: number, q: QuestionInput): Observable<Question> {
    return this.http.post<Question>(`${API_BASE}/api/quizzes/${quizId}/questions`, q, this.opts);
  }

  // --- teacher: sessions ---
  createSession(quizId: number): Observable<QuizSession> {
    return this.http.post<QuizSession>(
      `${API_BASE}/api/sessions?quiz_id=${quizId}`,
      {},
      this.opts,
    );
  }
  joinUrl(code: string): Observable<{ url: string }> {
    return this.http.get<{ url: string }>(`${API_BASE}/api/sessions/${code}/join-url`, this.opts);
  }
  openRound(code: string, questionId: number, phase: Phase): Observable<Round> {
    return this.http.post<Round>(
      `${API_BASE}/api/sessions/${code}/rounds`,
      { question_id: questionId, phase },
      this.opts,
    );
  }
  closeRound(code: string, roundId: number): Observable<Round> {
    return this.http.post<Round>(
      `${API_BASE}/api/sessions/${code}/rounds/${roundId}/close`,
      {},
      this.opts,
    );
  }
  histogram(code: string, roundId: number): Observable<Histogram> {
    return this.http.get<Histogram>(
      `${API_BASE}/api/sessions/${code}/rounds/${roundId}/histogram`,
      this.opts,
    );
  }
  /** Answer count for the open round — count only, safe to project. */
  liveCount(code: string): Observable<LiveCount> {
    return this.http.get<LiveCount>(`${API_BASE}/api/sessions/${code}/live`, this.opts);
  }
  comparison(code: string, questionId: number): Observable<Comparison> {
    return this.http.get<Comparison>(
      `${API_BASE}/api/sessions/${code}/questions/${questionId}/comparison`,
      this.opts,
    );
  }

  // --- student ---
  sessionState(code: string): Observable<SessionState> {
    return this.http.get<SessionState>(`${API_BASE}/api/sessions/${code}/state`, this.opts);
  }
  submitAnswer(code: string, choiceId: number): Observable<unknown> {
    return this.http.post(
      `${API_BASE}/api/sessions/${code}/answers`,
      { choice_id: choiceId },
      this.opts,
    );
  }

  /**
   * Subscribe to session-state changes over SSE. The server is the source of
   * truth: every event is a full state snapshot. Callers should also fetch
   * sessionState() on (re)connect since events sent while disconnected are lost.
   * Returns a teardown function.
   */
  streamState(code: string, onState: (s: SessionState) => void): () => void {
    const es = new EventSource(`${API_BASE}/api/sessions/${code}/events`, {
      withCredentials: true,
    });
    es.addEventListener('state', (ev: MessageEvent) => {
      const state = JSON.parse(ev.data) as SessionState;
      // EventSource callbacks fire outside Angular's zone.
      this.zone.run(() => onState(state));
    });
    return () => es.close();
  }
}
