import { Component, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { ApiService } from '../api.service';
import { CanvasCourse, Question, Quiz, RosterStatus, SyncSummary } from '../models';
import { QuestionDraft, QuestionEditorComponent } from './question-editor.component';

@Component({
  selector: 'app-teacher-dashboard',
  standalone: true,
  imports: [FormsModule, QuestionEditorComponent],
  template: `
    <div class="wrap">
      @if (ephemeralStorage()) {
        <p class="storage-warning">
          <strong>No persistent storage.</strong>
          Quizzes, answers and logins are lost whenever the app restarts, and
          everyone is signed out. Attach a writable volume at the configured
          data directory to fix it.
        </p>
      }

      <h1>Your quizzes</h1>

      <details class="term-report">
        <summary>End-of-term participation</summary>
        <p class="note">
          Who took part in both bouts, across every session you have run.
          Attendance only — no correct/incorrect. Contains student names:
          <strong>do not project</strong>.
        </p>
        <div class="range">
          <label>From <input type="date" [(ngModel)]="reportFrom" name="from" /></label>
          <label>To <input type="date" [(ngModel)]="reportTo" name="to" /></label>
          <a class="download" [href]="semesterCsvUrl()" download>Download CSV</a>
        </div>
        <p class="note">Leave the dates empty for everything.</p>
      </details>

      <details class="term-report roster" (toggle)="onRosterOpened($event)">
        <summary>Course roster (Canvas)</summary>
        @if (rosterStatus(); as status) {
          @if (!status.canvas_configured) {
            <p class="note">
              Not configured. Generate a personal access token at
              <code>{{ status.canvas_base_url }}/profile/settings</code>
              (Approved Integrations → New Access Token) and set
              <code>CANVAS_TOKEN</code> in the app's configuration file. The
              token needs no administrator, and stays on the server.
            </p>
          } @else {
            <p class="note">
              Syncing mirrors the current enrolment: students who have dropped
              the course are removed. Only names and KTH ids are stored, never
              email. Student names — <strong>do not project</strong>.
            </p>
            <div class="range">
              <label>
                Course
                <select [(ngModel)]="selectedCourse" name="course">
                  <option [ngValue]="null">
                    {{ coursesLoading() ? 'Loading…' : 'Choose a course' }}
                  </option>
                  @for (c of canvasCourses(); track c.id) {
                    <option [ngValue]="c.id">{{ c.name }}</option>
                  }
                </select>
              </label>
              <button (click)="syncRoster()" [disabled]="!selectedCourse || syncing()">
                {{ syncing() ? 'Syncing…' : 'Sync roster' }}
              </button>
            </div>

            @if (syncSummary(); as s) {
              <p class="note">
                Synced course {{ s.course_id }}: {{ s.total }} students
                ({{ s.added }} added, {{ s.updated }} updated, {{ s.removed }} removed).
              </p>
            }

            @if (status.courses.length) {
              <table class="synced">
                <tr><th>Course</th><th>Students</th><th>Last synced</th></tr>
                @for (c of status.courses; track c.course_id) {
                  <tr>
                    <td>{{ c.course_id }}</td>
                    <td>{{ c.students }}</td>
                    <td>{{ c.synced_at }}</td>
                  </tr>
                }
              </table>
            }
          }
          @if (rosterError()) {
            <p class="error">{{ rosterError() }}</p>
          }
        }
      </details>

      <form class="new-quiz" (ngSubmit)="createQuiz()">
        <input [(ngModel)]="newTitle" name="title" placeholder="New quiz title" />
        <button [disabled]="!newTitle.trim()">Create</button>
      </form>

      @for (quiz of quizzes(); track quiz.id) {
        <section class="quiz">
          <header>
            <h2>{{ quiz.title }}</h2>
            <button (click)="startSession(quiz)">Run session ▶</button>
          </header>

          <ol>
            @for (q of quiz.questions; track q.id) {
              <li>
                @if (editingId() === q.id) {
                  <app-question-editor
                    [draft]="editDraft!"
                    [formId]="'edit' + q.id"
                    submitLabel="Save changes"
                    [showCancel]="true"
                    [error]="editError()"
                    (save)="saveEdit(quiz)"
                    (cancel)="cancelEdit()"
                  />
                } @else {
                  <div class="q-row">
                    <span [innerHTML]="q.text_html"></span>
                    <span class="q-actions">
                      <button type="button" (click)="startEdit(q)">Edit</button>
                      <button type="button" class="danger" (click)="removeQuestion(quiz, q)">
                        Delete
                      </button>
                    </span>
                  </div>
                  <ul>
                    @for (c of q.choices; track c.id) {
                      <li [class.correct]="c.is_correct">{{ c.text }}</li>
                    }
                  </ul>
                  @if (deleteError()[q.id]; as msg) {
                    <p class="error">{{ msg }}</p>
                  }
                }
              </li>
            }
          </ol>

          <details>
            <summary>Add question</summary>
            <app-question-editor
              [draft]="draft"
              [formId]="'new' + quiz.id"
              [error]="formError"
              (save)="addQuestion(quiz)"
            />
          </details>
        </section>
      }
    </div>
  `,
  styles: [
    `
      .wrap { max-width: 44rem; margin: 1.5rem auto; padding: 1rem; }
      .storage-warning { background: #fdf3f2; border: 1px solid #e6b8b2; border-radius: 6px;
                         padding: 0.6rem 0.8rem; color: #8a2b20; }
      .term-report { border: 1px solid #ddd; border-radius: 8px; padding: 0.6rem 1rem;
                     margin: 1rem 0; }
      .term-report summary { cursor: pointer; font-weight: 600; }
      .term-report .note { font-size: 0.85rem; color: #666; margin: 0.5rem 0; }
      .range { display: flex; gap: 0.8rem; align-items: center; flex-wrap: wrap; }
      .range label { font-size: 0.9rem; }
      .download { border: 1px solid var(--border); border-radius: 6px;
                  padding: 0.35rem 0.7rem; text-decoration: none; color: inherit; }
      .quiz { border: 1px solid #ddd; border-radius: 8px; padding: 1rem; margin: 1rem 0; }
      header { display: flex; justify-content: space-between; align-items: center; }
      li.correct { font-weight: 600; color: #2c7; }
      .q-row { display: flex; justify-content: space-between; align-items: flex-start;
               gap: 0.75rem; }
      .q-actions { display: flex; gap: 0.3rem; flex-shrink: 0; }
      .q-actions button { font-size: 0.8rem; padding: 0.2rem 0.5rem; }
      .q-actions .danger { color: #c0392b; }
      .roster code { background: #f4f4f4; padding: 0 0.25rem; border-radius: 3px;
                     font-size: 0.85em; word-break: break-all; }
      .synced { border-collapse: collapse; margin: 0.5rem 0; font-size: 0.85rem; }
      .synced th, .synced td { border: 1px solid #ddd; padding: 0.2rem 0.5rem;
                               text-align: left; }
      .error { color: #c0392b; }
    `,
  ],
})
export class TeacherDashboardComponent implements OnInit {
  quizzes = signal<Quiz[]>([]);
  newTitle = '';
  formError = '';
  reportFrom = '';
  reportTo = '';
  ephemeralStorage = signal(false);
  draft: QuestionDraft = this.blankDraft();

  /** Which question is open in the inline editor, if any. */
  editingId = signal<number | null>(null);
  editDraft: QuestionDraft | null = null;
  editError = signal('');
  /** Refusals keyed by question id, so each row explains its own failure. */
  deleteError = signal<Record<number, string>>({});

  rosterStatus = signal<RosterStatus | null>(null);
  canvasCourses = signal<CanvasCourse[]>([]);
  coursesLoading = signal(false);
  selectedCourse: number | null = null;
  syncing = signal(false);
  syncSummary = signal<SyncSummary | null>(null);
  rosterError = signal('');

  constructor(private api: ApiService, private router: Router) {}

  ngOnInit(): void {
    this.reload();
    // Non-persistent storage does not announce itself: the app works until it
    // restarts, then everything is gone and everyone is signed out.
    this.api.health().subscribe((h) => this.ephemeralStorage.set(h.storage === 'ephemeral'));
  }

  private reload(): void {
    this.api.listQuizzes().subscribe((qs) => this.quizzes.set(qs));
  }

  private blankDraft() {
    return {
      text: '',
      choices: [
        { text: '', is_correct: false },
        { text: '', is_correct: false },
      ],
    };
  }

  /**
   * Load the roster panel when it is first opened, not on every dashboard
   * visit: listing Canvas courses is a call out to Canvas.
   */
  onRosterOpened(event: Event): void {
    if (!(event.target as HTMLDetailsElement).open) return;
    if (this.rosterStatus()) return;
    this.refreshRosterStatus(true);
  }

  private refreshRosterStatus(loadCourses = false): void {
    this.api.rosterStatus().subscribe({
      next: (status) => {
        this.rosterStatus.set(status);
        if (loadCourses && status.canvas_configured) this.loadCanvasCourses();
      },
      error: () => this.rosterError.set('Could not read the roster status.'),
    });
  }

  private loadCanvasCourses(): void {
    this.coursesLoading.set(true);
    this.api.canvasCourses().subscribe({
      next: (courses) => {
        this.canvasCourses.set(courses);
        this.coursesLoading.set(false);
      },
      error: (err) => {
        this.rosterError.set(err?.error?.detail ?? 'Could not list Canvas courses.');
        this.coursesLoading.set(false);
      },
    });
  }

  syncRoster(): void {
    if (!this.selectedCourse) return;
    this.rosterError.set('');
    this.syncSummary.set(null);
    this.syncing.set(true);
    this.api.syncRoster(this.selectedCourse).subscribe({
      next: (summary) => {
        this.syncSummary.set(summary);
        this.syncing.set(false);
        this.refreshRosterStatus();
      },
      error: (err) => {
        this.rosterError.set(err?.error?.detail ?? 'Could not sync that course.');
        this.syncing.set(false);
      },
    });
  }

  semesterCsvUrl(): string {
    return this.api.semesterParticipationCsvUrl(this.reportFrom, this.reportTo);
  }

  createQuiz(): void {
    if (!this.newTitle.trim()) return;
    this.api.createQuiz(this.newTitle.trim()).subscribe(() => {
      this.newTitle = '';
      this.reload();
    });
  }

  /**
   * Open a question for editing, working on a copy.
   *
   * Choice ids come along: the server needs them to tell a reworded choice
   * from a new one, because answers point at choice ids.
   */
  startEdit(q: Question): void {
    this.editError.set('');
    this.editDraft = {
      text: q.text,
      choices: q.choices.map((c) => ({
        id: c.id,
        text: c.text,
        is_correct: !!c.is_correct,
      })),
    };
    this.editingId.set(q.id);
  }

  cancelEdit(): void {
    this.editingId.set(null);
    this.editDraft = null;
    this.editError.set('');
  }

  saveEdit(quiz: Quiz): void {
    const id = this.editingId();
    if (id === null || !this.editDraft) return;
    this.editError.set('');
    this.api
      .editQuestion(quiz.id, id, {
        text: this.editDraft.text.trim(),
        image_url: null,
        choices: this.editDraft.choices.filter((c) => c.text.trim()),
      })
      .subscribe({
        next: () => {
          this.cancelEdit();
          this.reload();
        },
        error: (err) =>
          this.editError.set(err?.error?.detail ?? 'Could not save those changes.'),
      });
  }

  /**
   * Delete a question, after confirming.
   *
   * The server refuses if it has already been asked — the answers would be
   * stranded — so the refusal is shown against the question rather than
   * swallowed.
   */
  removeQuestion(quiz: Quiz, q: Question): void {
    if (!confirm('Delete this question? This cannot be undone.')) return;
    this.deleteError.update((errors) => {
      const { [q.id]: _dropped, ...rest } = errors;
      return rest;
    });
    this.api.deleteQuestion(quiz.id, q.id).subscribe({
      next: () => this.reload(),
      error: (err) =>
        this.deleteError.update((errors) => ({
          ...errors,
          [q.id]: err?.error?.detail ?? 'Could not delete that question.',
        })),
    });
  }

  addQuestion(quiz: Quiz): void {
    this.formError = '';
    const choices = this.draft.choices.filter((c) => c.text.trim());
    this.api
      .addQuestion(quiz.id, { text: this.draft.text.trim(), image_url: null, choices })
      .subscribe({
        next: () => {
          this.draft = this.blankDraft();
          this.reload();
        },
        error: () => (this.formError = 'Could not save (need exactly one correct choice).'),
      });
  }

  startSession(quiz: Quiz): void {
    this.api.createSession(quiz.id).subscribe((s) => {
      this.router.navigate(['/teacher/session', s.code]);
    });
  }
}
