import { DatePipe } from '@angular/common';
import { Component, OnInit, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { ApiService } from '../api.service';
import { CanvasCourse, ImportReport, Question, Quiz, RosterStatus, SyncSummary } from '../models';
import { MarkdownHtmlDirective } from '../md-html.directive';
import { QuestionDraft, QuestionEditorComponent } from './question-editor.component';

@Component({
  selector: 'app-teacher-dashboard',
  standalone: true,
  imports: [DatePipe, FormsModule, MarkdownHtmlDirective, QuestionEditorComponent],
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
          <span class="note">a spreadsheet to read — not the Canvas file</span>
        </div>
        <p class="note">
          Leave the dates empty for everything. Lectures from archived quizzes
          are left out of both files unless
          <label class="inline">
            <input type="checkbox" [(ngModel)]="reportArchived" name="archived" />
            included
          </label>.
        </p>

        <p class="note">
          <strong>For the Canvas gradebook:</strong> the second file below is
          Canvas's own import format. One point per lecture in which the
          student answered at least the share of bouts set below — four
          questions asked twice is eight chances, and 75% of them is six.
          Answering is the bar rather than logging in, because a login only
          proves someone has the session code. Keyed on each student's ids
          from the synced roster. In Canvas go to Grades → Import, upload it,
          and when it asks about the unknown column let it create the
          assignment.
        </p>
        <div class="range">
          <label>
            Assignment
            <input
              type="text"
              [(ngModel)]="canvasAssignment"
              name="assignment"
              placeholder="Quiz participation"
            />
          </label>
          <label>
            Answered at least
            <input
              type="number"
              class="pct"
              min="0"
              max="100"
              step="5"
              [(ngModel)]="canvasThreshold"
              name="threshold"
            />%
          </label>
          <a class="download" [href]="canvasCsvUrl()" download>Download for Canvas</a>
        </div>
        <p class="note">
          A student who is not on the synced roster is still listed, with no id
          — Canvas will skip that row, and the blank is there so you can see who
          it happened to rather than wonder later.
        </p>
      </details>

      <details class="term-report">
        <summary>Backup</summary>
        <p class="note">
          Everything on the server that cannot be recreated: the whole
          database, the uploaded figures, and the configuration. The database
          is a single file with no replication and no snapshots, so this
          download is the only copy that exists anywhere else.
        </p>
        <p class="note">
          <strong>Contains personal data</strong> — every student's name and
          every answer. Keep it somewhere only you can read.
          <strong>Secrets are removed</strong>: the Canvas token, the OIDC
          client secret and the teacher password come out as placeholders, so
          the file is not a credential if it goes astray. The keys stay, so it
          still tells you what to set up on a new host.
        </p>
        <div class="range">
          <a class="download" [href]="backupUrl()" download>Download backup</a>
        </div>
        <p class="note">
          It is a copy taken now: anything answered afterwards is not in it.
          Worth taking after each lecture, alongside the participation CSV.
        </p>
      </details>

      <details class="term-report roster" (toggle)="onRosterOpened($event)">
        <summary>Course roster (Canvas)</summary>
        @if (rosterStatus(); as status) {
          @if (!status.canvas_configured) {
            <p class="note">
              <strong>No Canvas token set</strong>, so a new sync cannot be run
              right now. Any roster already synced keeps working — it lives in
              the database, not in Canvas — so students can still sign in.
              Removing the token after syncing is the recommended state.
            </p>
            <p class="note">
              To sync again, generate a personal access token at
              <code>{{ status.canvas_base_url }}/profile/settings</code>
              (Approved Integrations → New Access Token), set
              <code>CANVAS_TOKEN</code> in the app's configuration file and
              restart. The token needs no administrator, stays on the server,
              and can be removed again afterwards.
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
                    <!-- Unpublished and finished courses are listed too — a
                         course being prepared is exactly when its roster is
                         wanted — but labelled, since picking the wrong one
                         syncs the wrong class with no other clue. -->
                    <option [ngValue]="c.id">
                      {{ c.name }}{{ c.state && c.state !== 'available' ? ' (' + c.state + ')' : '' }}
                    </option>
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

          }

          <!-- Outside the branch above on purpose: a roster that has already
               been synced keeps working after the token is removed, and
               removing it is the recommended thing to do. Hiding this made a
               correctly configured deployment look broken. -->
          @if (status.current_course_id) {
            <p class="note">
              The Canvas files use course <strong>{{ status.current_course_id }}</strong>,
              {{ status.course_pinned ? 'as set by CANVAS_COURSE_ID' : 'the one synced most recently' }}.
              For a new course, sync it here and the files follow.
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
          } @else {
            <p class="note">No roster has been synced yet, so nobody can sign in.</p>
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

      @for (quiz of activeQuizzes(); track quiz.id) {
        <section class="quiz">
          <header>
            <h2>{{ quiz.title }}</h2>
            <span class="quiz-actions">
              <button
                type="button"
                class="archive"
                (click)="archiveQuiz(quiz)"
                title="Hide it from this list and from the term reports. Deletes nothing."
              >
                Archive
              </button>
              <button (click)="startSession(quiz)">Run session ▶</button>
            </span>
          </header>

          @if (reorderError(); as msg) {
            <p class="error">{{ msg }}</p>
          }

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
                    [lockedKind]="true"
                    (save)="saveEdit(quiz)"
                    (cancel)="cancelEdit()"
                  />
                } @else {
                  <div class="q-row">
                    <!-- The qtext class is what the global rules for rendered
                         question Markdown key on; without it a figure here
                         draws at its natural pixel size. -->
                    <span class="qtext" [mdHtml]="q.text_html"></span>
                    <span class="q-actions">
                      <button
                        type="button"
                        class="move"
                        title="Ask this one earlier"
                        [disabled]="$first || reordering()"
                        (click)="move(quiz, $index, -1)"
                      >↑</button>
                      <button
                        type="button"
                        class="move"
                        title="Ask this one later"
                        [disabled]="$last || reordering()"
                        (click)="move(quiz, $index, 1)"
                      >↓</button>
                      <button type="button" (click)="startEdit(q)">Edit</button>
                      <button type="button" class="danger" (click)="removeQuestion(quiz, q)">
                        Delete
                      </button>
                    </span>
                  </div>
                  <!-- No green marking here. This list is on screen while
                       the projector is being set up, and a highlighted
                       correct answer is exactly what must not be read from
                       the back of the room. Reveal it behind a click. -->
                  @if (q.kind === 'text') {
                    <p class="kind-note">
                      Word cloud — students write
                      {{ q.max_words > 1 ? 'up to ' + q.max_words + ' words' : 'a word' }}; asked once.
                    </p>
                  }
                  <ul>
                    @for (c of q.choices; track c.id) {
                      <li
                        [class.correct]="revealed()[q.id] && c.is_correct"
                        [mdHtml]="c.text_html"
                      ></li>
                    }
                  </ul>
                  @if (q.kind !== 'text') {
                    <button type="button" class="reveal" (click)="toggleReveal(q.id)">
                      {{ revealed()[q.id] ? 'Hide the answer' : 'Show which is correct' }}
                    </button>
                  }
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

            <!-- Many questions at once, in the format the Markdown handout
                 writes. Checked before anything is stored, so the teacher sees
                 what will come in and why anything is refused. -->
            <div class="import">
              <!-- The help link sits outside the label: a click inside a file
                   input's label also opens the file chooser. -->
              <div class="pick-head">
                <span id="import-label-{{ quiz.id }}">Or import questions from a Markdown file</span>
                <button type="button" class="link" (click)="formatHelp.showModal()">
                  What should the file look like?
                </button>
              </div>
              <label class="pick">
                <input
                  [attr.aria-labelledby]="'import-label-' + quiz.id"
                  type="file"
                  multiple
                  accept=".md,.markdown,.txt,image/png,image/jpeg,image/gif,image/webp"
                  (change)="checkImport(quiz, $event)"
                />
              </label>
              <span class="import-hint">
                One <code>.md</code> file: <code>## 1.</code> per question, the text, then
                <code>- [ ]</code>/<code>- [x]</code> choices with exactly one
                <code>[x]</code>, then an optional <code>&gt;</code> explanation. Figures it
                names may be chosen with it; any left out can be added when editing.
              </span>
              @if (importFor() === quiz.id) {
                @if (importError(); as msg) {
                  <p class="error">{{ msg }}</p>
                }
                @if (importReport(); as r) {
                  <div class="import-report">
                    @for (note of r.notes; track note) {
                      <p class="error">{{ note }}</p>
                    }
                    <ol>
                      @for (q of r.questions; track $index) {
                        <li [class.refused]="!q.ok">
                          <span class="verdict">{{ q.ok ? '✓' : '✗' }}</span>
                          <span class="q-head">{{ q.heading || 'Question ' + (q.number ?? $index + 1) }}</span>
                          @if (q.ok) {
                            <span class="meta">
                              {{ q.choices.length }} choices{{ q.has_explanation ? ', explanation' : '' }}
                            </span>
                          }
                          @for (e of q.errors; track e) {
                            <div class="why">{{ e }}</div>
                          }
                          @for (w of q.warnings; track w) {
                            <div class="warn">{{ w }}</div>
                          }
                        </li>
                      }
                    </ol>
                    @if (r.dry_run) {
                      <p>
                        {{ r.accepted }} to import{{ r.refused ? ', ' + r.refused + ' refused' : '' }}.
                        @if (r.refused) { Fix the refused ones in the file and import them again later. }
                      </p>
                      <button (click)="runImport(quiz)" [disabled]="!r.accepted || importing()">
                        Import {{ r.accepted }} question{{ r.accepted === 1 ? '' : 's' }}
                      </button>
                      <button type="button" class="cancel" (click)="clearImport()">Cancel</button>
                    } @else {
                      <p>
                        Imported {{ r.imported }} question{{ r.imported === 1 ? '' : 's' }}{{ r.refused ? '; ' + r.refused + ' refused, listed above' : '' }}.
                      </p>
                      <button type="button" (click)="clearImport()">Done</button>
                    }
                  </div>
                }
              }
            </div>
          </details>
        </section>
      }

      <!-- The import format, one click from the file picker. -->
      <dialog #formatHelp class="format-help" (click)="$event.target === formatHelp && formatHelp.close()">
        <h3>Importing questions from Markdown</h3>
        <p>One <code>.md</code> file, saved as UTF-8. Each question looks like this:</p>
        <pre>## 1. Local alignment

Which algorithm finds the optimal **local** alignment?
Markdown works: $S = \\log_2 p/q$, &#96;code&#96;, lists, tables.

- [ ] Needleman-Wunsch
- [x] Smith-Waterman
- [ ] BLAST

&gt; Why the answer is right (optional).
&gt; Shown after the second bout, never on phones.</pre>
        <ul>
          <li>Every question starts with a <code>##</code> heading. The number is optional;
            a title after it becomes the question's bold first line.</li>
          <li>The question text comes first and must not be empty.</li>
          <li>Then at least two choices, each <code>- [ ]</code> or <code>- [x]</code>,
            with <strong>exactly one</strong> <code>[x]</code>.</li>
          <li>After the choices, only an optional explanation as <code>&gt;</code> lines.</li>
          <li>A <code>#</code> title line at the top is allowed and ignored.</li>
          <li>Figures: <code>![](name.png)&#123;width=60%&#125;</code>. Choose the image files
            together with the <code>.md</code>; any not chosen are left out, and can be
            added when editing.</li>
        </ul>
        <p>
          A question that breaks a rule is refused with the reason, and the others are still
          imported. Nothing is stored until you confirm. The Markdown handout downloaded
          <em>with answers</em> is in this format, so a quiz can be exported, edited and
          imported again.
        </p>
        <button type="button" (click)="formatHelp.close()">Close</button>
      </dialog>

      @if (archivedQuizzes().length) {
        <details class="archived">
          <summary>Archived quizzes ({{ archivedQuizzes().length }})</summary>
          <p class="note">
            Put away with their questions, lectures and answers intact, and left
            out of the term reports. Copy one to reuse its questions in a new
            course: the copy starts with no lectures.
          </p>
          @if (archiveError(); as msg) {
            <p class="error">{{ msg }}</p>
          }
          <ul>
            @for (quiz of archivedQuizzes(); track quiz.id) {
              <li>
                <span class="title">{{ quiz.title }}</span>
                <span class="meta">
                  {{ quiz.questions.length }}
                  {{ quiz.questions.length === 1 ? 'question' : 'questions' }} · archived
                  {{ quiz.archived_at | date: 'mediumDate' }}
                </span>
                <span class="quiz-actions">
                  <button type="button" (click)="copyQuiz(quiz)">Copy into new quiz</button>
                  <button type="button" (click)="restoreQuiz(quiz)">Restore</button>
                </span>
              </li>
            }
          </ul>
        </details>
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
      .range .pct { width: 4rem; }
      .download { border: 1px solid var(--border); border-radius: 6px;
                  padding: 0.35rem 0.7rem; text-decoration: none; color: inherit; }
      .quiz { border: 1px solid #ddd; border-radius: 8px; padding: 1rem; margin: 1rem 0; }
      header { display: flex; justify-content: space-between; align-items: center; }
      .quiz-actions { display: inline-flex; gap: 0.4rem; align-items: center; }
      .import { margin-top: 1rem; padding-top: 0.8rem; border-top: 1px dashed #ddd; }
      .import .pick-head { font-weight: 600; margin-bottom: 0.3rem; }
      .import .pick { display: block; }
      .import .import-hint { display: block; font-size: 0.8rem; color: #666; margin-top: 0.3rem; }
      .import-report ol { padding-left: 2rem; }
      .link { background: none; border: none; padding: 0; margin-left: 0.4rem; color: #2c7a51;
              text-decoration: underline; cursor: pointer; font: inherit; font-weight: 400;
              font-size: 0.85rem; }
      .format-help { max-width: 40rem; border: 1px solid #ccc; border-radius: 8px; padding: 1.2rem; }
      .format-help::backdrop { background: rgba(0, 0, 0, 0.35); }
      .format-help h3 { margin-top: 0; }
      .format-help pre { background: #f6f6f6; padding: 0.7rem; border-radius: 6px; overflow-x: auto;
                         font-size: 0.85rem; }
      .format-help li { margin: 0.3rem 0; font-size: 0.92rem; }
      .import-report li { margin: 0.3rem 0; }
      .import-report .verdict { color: #2c7a51; font-weight: 700; margin-right: 0.3rem; }
      .import-report li.refused .verdict { color: #c0392b; }
      .import-report .meta { color: #666; font-size: 0.85rem; margin-left: 0.4rem; }
      .import-report .why { color: #c0392b; font-size: 0.85rem; }
      .import-report .warn { color: #8a6d1d; font-size: 0.85rem; }
      .quiz-actions .archive { color: #555; }
      .inline { display: inline-flex; align-items: center; gap: 0.2rem; }
      .archived { border: 1px solid #ddd; border-radius: 8px; padding: 0.6rem 1rem; margin: 1.5rem 0; }
      .archived summary { cursor: pointer; font-weight: 600; }
      .archived .note { font-size: 0.85rem; color: #666; }
      .archived ul { list-style: none; padding: 0; margin: 0; }
      .archived li { display: flex; flex-wrap: wrap; gap: 0.3rem 0.8rem; align-items: center;
                     padding: 0.5rem 0; border-top: 1px solid #eee; }
      .archived .title { font-weight: 600; flex: 1 1 12rem; }
      .archived .meta { color: #666; font-size: 0.85rem; }
      li.correct { font-weight: 600; color: #2c7; }
      .reveal { font-size: 0.75rem; padding: 0.15rem 0.45rem; margin-top: 0.2rem;
                color: #777; background: none; }
      .q-row { display: flex; justify-content: space-between; align-items: flex-start;
               gap: 0.75rem; }
      .q-row .qtext { min-width: 0; }
      .q-actions { display: flex; gap: 0.3rem; flex-shrink: 0; }
      .q-actions button { font-size: 0.8rem; padding: 0.2rem 0.5rem; }
      .q-actions .move { padding: 0.2rem 0.4rem; line-height: 1; }
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
  /** The quizzes in use; archived ones are listed apart, collapsed. */
  activeQuizzes = computed(() => this.quizzes().filter((q) => !q.archived_at));
  archivedQuizzes = computed(() =>
    this.quizzes()
      .filter((q) => !!q.archived_at)
      .sort((a, b) => (b.archived_at ?? '').localeCompare(a.archived_at ?? '')),
  );
  archiveError = signal('');
  /** The quiz whose import panel is in use, and what the server made of the file. */
  importFor = signal<number | null>(null);
  importReport = signal<ImportReport | null>(null);
  importError = signal('');
  importing = signal(false);
  private importFiles: File[] = [];
  /** Whether the term reports should count lectures from archived quizzes. */
  reportArchived = false;
  newTitle = '';
  formError = '';
  reportFrom = '';
  reportTo = '';
  /** The Canvas assignment column name; blank lets the server default apply. */
  canvasAssignment = '';
  /** Share of a lecture's bouts a student must answer to be counted present. */
  canvasThreshold = 75;
  ephemeralStorage = signal(false);
  draft: QuestionDraft = this.blankDraft();

  /** Which question is open in the inline editor, if any. */
  editingId = signal<number | null>(null);
  editDraft: QuestionDraft | null = null;
  editError = signal('');
  /** Refusals keyed by question id, so each row explains its own failure. */
  deleteError = signal<Record<number, string>>({});
  reordering = signal(false);
  reorderError = signal('');

  /** Which questions have had their answer revealed, by explicit click. */
  revealed = signal<Record<number, boolean>>({});

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

  private blankDraft(): QuestionDraft {
    return {
      text: '',
      kind: 'choice' as const,
      max_words: 1,
      explanation: '',
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

  toggleReveal(questionId: number): void {
    this.revealed.update((r) => ({ ...r, [questionId]: !r[questionId] }));
  }

  semesterCsvUrl(): string {
    return this.api.semesterParticipationCsvUrl(
      this.reportFrom,
      this.reportTo,
      this.reportArchived,
    );
  }

  backupUrl(): string {
    return this.api.backupUrl();
  }

  canvasCsvUrl(): string {
    return this.api.canvasParticipationCsvUrl(
      this.reportFrom,
      this.reportTo,
      this.canvasAssignment.trim(),
      this.canvasThreshold,
      this.reportArchived,
    );
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
      kind: q.kind,
      max_words: q.max_words ?? 1,
      explanation: q.explanation ?? '',
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
        kind: this.editDraft.kind,
        max_words: this.editDraft.max_words,
        explanation: this.editDraft.kind === 'text' ? '' : this.editDraft.explanation.trim(),
        choices:
          this.editDraft.kind === 'text'
            ? []
            : this.editDraft.choices.filter((c) => c.text.trim()),
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
  /**
   * Move a question one place earlier or later in the running order.
   *
   * The whole order goes to the server, not the move, so two edits cannot
   * interleave into a shuffle. The list is re-read from the response rather
   * than assumed, so a refusal leaves the view showing what is really stored.
   */
  move(quiz: Quiz, index: number, delta: number): void {
    const ids = quiz.questions.map((q) => q.id);
    const target = index + delta;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];

    this.reordering.set(true);
    this.reorderError.set('');
    this.api.reorderQuestions(quiz.id, ids).subscribe({
      next: (questions) => {
        quiz.questions = questions;
        this.reordering.set(false);
      },
      error: () => {
        this.reorderError.set('Could not reorder the questions.');
        this.reordering.set(false);
        this.reload();
      },
    });
  }

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
      .addQuestion(quiz.id, {
        text: this.draft.text.trim(),
        image_url: null,
        kind: this.draft.kind,
        max_words: this.draft.max_words,
        explanation: this.draft.kind === 'text' ? '' : this.draft.explanation.trim(),
        choices: this.draft.kind === 'text' ? [] : choices,
      })
      .subscribe({
        next: () => {
          this.draft = this.blankDraft();
          this.reload();
        },
        error: () => (this.formError = 'Could not save (need exactly one correct choice).'),
      });
  }

  /** Check a Markdown file before importing anything from it. */
  checkImport(quiz: Quiz, event: Event): void {
    const input = event.target as HTMLInputElement;
    this.importFiles = Array.from(input.files ?? []);
    input.value = ''; // so choosing the same, corrected file again re-checks it
    this.importFor.set(quiz.id);
    this.importReport.set(null);
    this.importError.set('');
    if (!this.importFiles.length) return;
    this.api.importQuestions(quiz.id, this.importFiles, true).subscribe({
      next: (r) => this.importReport.set(r),
      error: (err) => this.importError.set(err?.error?.detail ?? 'Could not read that file.'),
    });
  }

  runImport(quiz: Quiz): void {
    this.importing.set(true);
    this.api.importQuestions(quiz.id, this.importFiles, false).subscribe({
      next: (r) => {
        this.importReport.set(r);
        this.importing.set(false);
        this.reload();
      },
      error: (err) => {
        this.importError.set(err?.error?.detail ?? 'The import failed; nothing was added.');
        this.importing.set(false);
      },
    });
  }

  clearImport(): void {
    this.importFor.set(null);
    this.importReport.set(null);
    this.importError.set('');
    this.importFiles = [];
  }

  /**
   * Put a quiz away at the end of a course. Reversible and destroys nothing,
   * so it asks no confirmation: Restore is one click away in the list below.
   */
  archiveQuiz(quiz: Quiz): void {
    this.archiveError.set('');
    this.api.archiveQuiz(quiz.id).subscribe({
      next: () => this.reload(),
      error: () => this.archiveError.set('Could not archive that quiz.'),
    });
  }

  restoreQuiz(quiz: Quiz): void {
    this.archiveError.set('');
    this.api.restoreQuiz(quiz.id).subscribe({
      next: () => this.reload(),
      error: () => this.archiveError.set('Could not restore that quiz.'),
    });
  }

  /** Reuse an archived quiz's questions in a new course, without its lectures. */
  copyQuiz(quiz: Quiz): void {
    const title = window.prompt('Title of the new quiz', quiz.title);
    if (title === null) return;
    this.archiveError.set('');
    this.api.copyQuiz(quiz.id, title.trim()).subscribe({
      next: () => this.reload(),
      error: () => this.archiveError.set('Could not copy that quiz.'),
    });
  }

  startSession(quiz: Quiz): void {
    this.api.createSession(quiz.id).subscribe((s) => {
      this.router.navigate(['/teacher/session', s.code]);
    });
  }
}
