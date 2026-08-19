import { HttpClient } from '@angular/common/http';
import { Injectable, NgZone } from '@angular/core';
import { Observable } from 'rxjs';

import { API_BASE } from './api.config';
import {
  CanvasCourse,
  Comparison,
  Histogram,
  LoginMethods,
  LiveCount,
  Participants,
  ParticipationReport,
  Phase,
  Question,
  QuestionEditInput,
  QuestionInput,
  Quiz,
  QuizSession,
  RosterStatus,
  Round,
  SessionState,
  SyncSummary,
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

  /**
   * Roster addresses beginning with what has been typed.
   *
   * The server returns nothing for a short prefix and caps the matches, so
   * this narrows a class rather than downloading one.
   */
  rosterSuggest(prefix: string): Observable<{ matches: string[] }> {
    return this.http.get<{ matches: string[] }>(
      `${API_BASE}/api/auth/roster-suggest?q=${encodeURIComponent(prefix)}`,
      this.opts,
    );
  }

  /** Which login forms this deployment offers. Carries no secret. */
  loginMethods(): Observable<LoginMethods> {
    return this.http.get<LoginMethods>(`${API_BASE}/api/auth/methods`, this.opts);
  }

  /**
   * Identify a student against the synced course roster, or a teacher with
   * the shared password. A stop-gap until a real identity provider exists.
   */
  rosterLogin(email: string, password: string): Observable<User> {
    return this.http.post<User>(
      `${API_BASE}/api/auth/roster-login`,
      { email, password },
      this.opts,
    );
  }

  /** Liveness, and whether data written here survives a restart. */
  health(): Observable<{ status: string; storage: 'persistent' | 'ephemeral' }> {
    return this.http.get<{ status: string; storage: 'persistent' | 'ephemeral' }>(
      `${API_BASE}/api/health`,
      this.opts,
    );
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
  /**
   * Render Markdown for the authoring preview.
   *
   * Server-side so the preview uses the same renderer and sanitiser as the
   * question students receive.
   */
  renderMarkdown(text: string): Observable<{ html: string }> {
    return this.http.post<{ html: string }>(
      `${API_BASE}/api/markdown/preview`,
      { text },
      this.opts,
    );
  }

  /** Upload a figure; returns the Markdown to paste into a question. */
  uploadImage(file: File): Observable<{ url: string; markdown: string }> {
    const form = new FormData();
    form.append('file', file);
    return this.http.post<{ url: string; markdown: string }>(
      `${API_BASE}/api/images`,
      form,
      this.opts,
    );
  }

  addQuestion(quizId: number, q: QuestionInput): Observable<Question> {
    return this.http.post<Question>(`${API_BASE}/api/quizzes/${quizId}/questions`, q, this.opts);
  }

  /**
   * Edit a question in place. Send back the id of every choice being kept —
   * one that is left out is removed, which the server refuses if students
   * have already answered it.
   */
  editQuestion(quizId: number, questionId: number, q: QuestionEditInput): Observable<Question> {
    return this.http.put<Question>(
      `${API_BASE}/api/quizzes/${quizId}/questions/${questionId}`,
      q,
      this.opts,
    );
  }

  /** Refused (409) once the question has been asked, to protect its answers. */
  deleteQuestion(quizId: number, questionId: number): Observable<void> {
    return this.http.delete<void>(
      `${API_BASE}/api/quizzes/${quizId}/questions/${questionId}`,
      this.opts,
    );
  }

  // --- teacher: Canvas roster ---
  /**
   * Whether Canvas is configured, and which courses have been synced.
   * The Canvas access token stays on the server; this never carries it.
   */
  rosterStatus(): Observable<RosterStatus> {
    return this.http.get<RosterStatus>(`${API_BASE}/api/roster/status`, this.opts);
  }

  /** Canvas courses the configured token's owner teaches. */
  canvasCourses(): Observable<CanvasCourse[]> {
    return this.http.get<CanvasCourse[]>(`${API_BASE}/api/roster/courses`, this.opts);
  }

  /** Mirror a course's student list into the local roster. */
  syncRoster(courseId: number): Observable<SyncSummary> {
    return this.http.post<SyncSummary>(
      `${API_BASE}/api/roster/sync?course_id=${courseId}`,
      {},
      this.opts,
    );
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
  /**
   * Discard a question's rounds so it can be run again.
   *
   * Destructive: the answers students gave for it go with them.
   */
  resetQuestion(code: string, questionId: number): Observable<{ removed_rounds: number }> {
    return this.http.delete<{ removed_rounds: number }>(
      `${API_BASE}/api/sessions/${code}/questions/${questionId}/rounds`,
      this.opts,
    );
  }

  histogram(code: string, roundId: number): Observable<Histogram> {
    return this.http.get<Histogram>(
      `${API_BASE}/api/sessions/${code}/rounds/${roundId}/histogram`,
      this.opts,
    );
  }
  /** How many students have joined the session — counts only, never names. */
  participants(code: string): Observable<Participants> {
    return this.http.get<Participants>(
      `${API_BASE}/api/sessions/${code}/participants`,
      this.opts,
    );
  }

  /**
   * Per-student participation. Personal data: teacher-only, never projected.
   */
  participation(code: string): Observable<ParticipationReport> {
    return this.http.get<ParticipationReport>(
      `${API_BASE}/api/sessions/${code}/participation`,
      this.opts,
    );
  }

  participationCsvUrl(code: string): string {
    return `${API_BASE}/api/sessions/${code}/participation.csv`;
  }

  /**
   * Attendance across every session in a date range — the end-of-term record.
   * Personal data: teacher-only, and not something to project.
   */
  semesterParticipationCsvUrl(from: string, to: string): string {
    const range = new URLSearchParams();
    if (from) range.set('from', from);
    if (to) range.set('to', to);
    const query = range.toString();
    return `${API_BASE}/api/reports/participation.csv${query ? '?' + query : ''}`;
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
