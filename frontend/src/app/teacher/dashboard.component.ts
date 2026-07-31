import { Component, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { ApiService } from '../api.service';
import { Quiz } from '../models';

interface ChoiceDraft {
  text: string;
  is_correct: boolean;
}

@Component({
  selector: 'app-teacher-dashboard',
  standalone: true,
  imports: [FormsModule],
  template: `
    <div class="wrap">
      <h1>Your quizzes</h1>

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
                {{ q.text }}
                <ul>
                  @for (c of q.choices; track c.id) {
                    <li [class.correct]="c.is_correct">{{ c.text }}</li>
                  }
                </ul>
              </li>
            }
          </ol>

          <details>
            <summary>Add question</summary>
            <div class="add-q">
              <textarea [(ngModel)]="draft.text" name="qtext" placeholder="Question text"></textarea>
              @for (c of draft.choices; track $index) {
                <div class="choice-row">
                  <label class="mark" [class.on]="c.is_correct">
                    <input type="radio" name="correct" [checked]="c.is_correct"
                           (change)="setCorrect($index)" />
                    <span>correct</span>
                  </label>
                  <input [(ngModel)]="c.text" [name]="'c' + $index" placeholder="Choice text" />
                </div>
              }
              <button type="button" (click)="addChoiceRow()">+ choice</button>
              <button (click)="addQuestion(quiz)" [disabled]="!questionValid()">Save question</button>
              @if (!questionValid()) {
                <p class="hint">{{ whyInvalid() }}</p>
              }
              @if (formError) {
                <p class="error">{{ formError }}</p>
              }
            </div>
          </details>
        </section>
      }
    </div>
  `,
  styles: [
    `
      .wrap { max-width: 44rem; margin: 1.5rem auto; padding: 1rem; }
      .quiz { border: 1px solid #ddd; border-radius: 8px; padding: 1rem; margin: 1rem 0; }
      header { display: flex; justify-content: space-between; align-items: center; }
      li.correct { font-weight: 600; color: #2c7; }
      .choice-row { display: flex; gap: 0.5rem; align-items: center; margin: 0.3rem 0; }
      .mark { display: inline-flex; align-items: center; gap: 0.3rem; cursor: pointer;
              border: 1px solid var(--border); border-radius: 6px; padding: 0.25rem 0.5rem;
              font-size: 0.85rem; color: #777; white-space: nowrap; }
      .mark.on { border-color: #2c7a51; background: #eafaf1; color: #2c7a51; font-weight: 600; }
      .hint { font-size: 0.85rem; color: #777; margin: 0.3rem 0 0; }
      textarea { width: 100%; min-height: 3rem; }
      .error { color: #c0392b; }
    `,
  ],
})
export class TeacherDashboardComponent implements OnInit {
  quizzes = signal<Quiz[]>([]);
  newTitle = '';
  formError = '';
  draft: { text: string; choices: ChoiceDraft[] } = this.blankDraft();

  constructor(private api: ApiService, private router: Router) {}

  ngOnInit(): void {
    this.reload();
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

  createQuiz(): void {
    if (!this.newTitle.trim()) return;
    this.api.createQuiz(this.newTitle.trim()).subscribe(() => {
      this.newTitle = '';
      this.reload();
    });
  }

  addChoiceRow(): void {
    this.draft.choices.push({ text: '', is_correct: false });
  }

  setCorrect(index: number): void {
    this.draft.choices.forEach((c, i) => (c.is_correct = i === index));
  }

  /** Why the question cannot be saved yet, in the teacher's terms. */
  whyInvalid(): string {
    if (!this.draft.text.trim()) return 'Write the question text.';
    const filled = this.draft.choices.filter((c) => c.text.trim());
    if (filled.length < 2) return 'Add at least two choices.';
    if (!filled.some((c) => c.is_correct)) {
      return 'Mark which choice is correct.';
    }
    return '';
  }

  questionValid(): boolean {
    const filled = this.draft.choices.filter((c) => c.text.trim());
    return (
      this.draft.text.trim().length > 0 &&
      filled.length >= 2 &&
      filled.filter((c) => c.is_correct).length === 1
    );
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
