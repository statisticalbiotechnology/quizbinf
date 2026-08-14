import { Component, EventEmitter, Input, OnDestroy, Output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../api.service';

export interface ChoiceDraft {
  /** Present when this choice already exists; absent means a new one. */
  id?: number;
  text: string;
  is_correct: boolean;
}

export interface QuestionDraft {
  text: string;
  choices: ChoiceDraft[];
}

/**
 * The authoring form, used both to add a question and to edit one.
 *
 * Shared rather than duplicated so the two cannot drift: an edit form without
 * the Markdown preview or the image upload would quietly be a worse tool than
 * the one used to write the question in the first place.
 */
@Component({
  selector: 'app-question-editor',
  standalone: true,
  imports: [FormsModule],
  template: `
    <div class="add-q">
      <!-- Stable name: only the radio group below needs to differ per editor,
           and this one is what the browser tests address the form by. -->
      <textarea
        [(ngModel)]="draft.text"
        name="qtext"
        (ngModelChange)="onTextChanged()"
        placeholder="Question text — Markdown supported: **bold**, *italic*, lists, tables, images"
      ></textarea>

      <div class="md-tools">
        <label class="upload">
          <input
            type="file"
            accept="image/png,image/jpeg,image/gif,image/webp"
            (change)="uploadImage($event)"
            hidden
          />
          {{ uploading() ? 'Uploading…' : '🖼 Add image' }}
        </label>
        <span class="md-hint">Markdown supported. Uploads are inserted as an image link.</span>
      </div>
      @if (uploadError()) {
        <p class="error">{{ uploadError() }}</p>
      }

      @if (draft.text.trim()) {
        <div class="preview">
          <span class="preview-label">Preview</span>
          <div [innerHTML]="preview()"></div>
        </div>
      }

      @for (c of draft.choices; track $index) {
        <div class="choice-row">
          <label class="mark" [class.on]="c.is_correct">
            <input
              type="radio"
              [name]="'correct' + formId"
              [checked]="c.is_correct"
              (change)="setCorrect($index)"
            />
            <span>correct</span>
          </label>
          <input [(ngModel)]="c.text" [name]="'c' + formId + $index" placeholder="Choice text" />
          @if (draft.choices.length > 2) {
            <button type="button" class="drop" (click)="removeChoice($index)" title="Remove choice">
              ✕
            </button>
          }
        </div>
      }

      <button type="button" (click)="addChoiceRow()">+ choice</button>
      <button (click)="save.emit()" [disabled]="!valid()">{{ submitLabel }}</button>
      @if (showCancel) {
        <button type="button" class="cancel" (click)="cancel.emit()">Cancel</button>
      }

      @if (!valid()) {
        <p class="hint">{{ whyInvalid() }}</p>
      }
      @if (error) {
        <p class="error">{{ error }}</p>
      }
    </div>
  `,
  styles: [
    `
      .choice-row { display: flex; gap: 0.5rem; align-items: center; margin: 0.3rem 0; }
      .mark { display: inline-flex; align-items: center; gap: 0.3rem; cursor: pointer;
              border: 1px solid var(--border); border-radius: 6px; padding: 0.25rem 0.5rem;
              font-size: 0.85rem; color: #777; white-space: nowrap; }
      .mark.on { border-color: #2c7a51; background: #eafaf1; color: #2c7a51; font-weight: 600; }
      .choice-row input[type='text'], .choice-row input:not([type]) { flex: 1; }
      .drop { border: none; background: none; cursor: pointer; color: #999; font-size: 0.9rem; }
      .drop:hover { color: #c0392b; }
      .cancel { background: none; }
      .hint { font-size: 0.85rem; color: #777; margin: 0.3rem 0 0; }
      textarea { width: 100%; min-height: 5rem; font-family: inherit; }
      .md-tools { display: flex; gap: 0.6rem; align-items: center; margin: 0.4rem 0; }
      .upload { cursor: pointer; border: 1px solid var(--border); border-radius: 6px;
                padding: 0.35rem 0.7rem; font-size: 0.85rem; white-space: nowrap; }
      .md-hint { font-size: 0.8rem; color: #777; }
      .preview { border: 1px dashed #ccc; border-radius: 6px; padding: 0.6rem;
                 margin: 0.5rem 0; position: relative; }
      .preview-label { position: absolute; top: -0.6rem; left: 0.6rem; background: #fff;
                       padding: 0 0.3rem; font-size: 0.7rem; color: #888; text-transform: uppercase; }
      .preview img { max-width: 100%; height: auto; border-radius: 4px; }
      .preview table { border-collapse: collapse; }
      .preview th, .preview td { border: 1px solid #ddd; padding: 0.2rem 0.4rem; }
      .error { color: #c0392b; }
    `,
  ],
})
export class QuestionEditorComponent implements OnDestroy {
  @Input({ required: true }) draft!: QuestionDraft;
  @Input() submitLabel = 'Save question';
  @Input() showCancel = false;
  @Input() error = '';
  /** Distinguishes radio groups when two editors are on the page at once. */
  @Input() formId = '';

  @Output() save = new EventEmitter<void>();
  @Output() cancel = new EventEmitter<void>();

  uploading = signal(false);
  uploadError = signal('');
  preview = signal<string>('');
  private previewTimer?: ReturnType<typeof setTimeout>;

  constructor(private api: ApiService) {}

  ngOnDestroy(): void {
    if (this.previewTimer) clearTimeout(this.previewTimer);
  }

  /** Render on the server, so the teacher sees exactly what students get. */
  refreshPreview(): void {
    if (this.previewTimer) clearTimeout(this.previewTimer);
    this.previewTimer = setTimeout(() => {
      const text = this.draft.text.trim();
      if (!text) {
        this.preview.set('');
        return;
      }
      this.api.renderMarkdown(text).subscribe({
        next: ({ html }) => this.preview.set(html),
        error: () => this.preview.set(''),
      });
    }, 300);
  }

  onTextChanged(): void {
    this.refreshPreview();
  }

  /**
   * Upload a figure and append the Markdown that displays it. Files live on
   * the app's own volume so a question cannot break because an external host
   * is down mid-lecture.
   */
  uploadImage(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    this.uploadError.set('');
    this.uploading.set(true);
    this.api.uploadImage(file).subscribe({
      next: ({ markdown }) => {
        const sep = this.draft.text && !this.draft.text.endsWith('\n') ? '\n\n' : '';
        this.draft.text = `${this.draft.text}${sep}${markdown}`;
        this.uploading.set(false);
        input.value = '';
        this.refreshPreview();
      },
      error: (err) => {
        this.uploadError.set(err?.error?.detail ?? 'Could not upload that image.');
        this.uploading.set(false);
        input.value = '';
      },
    });
  }

  addChoiceRow(): void {
    this.draft.choices.push({ text: '', is_correct: false });
  }

  removeChoice(index: number): void {
    this.draft.choices.splice(index, 1);
  }

  setCorrect(index: number): void {
    this.draft.choices.forEach((c, i) => (c.is_correct = i === index));
  }

  /** Why the question cannot be saved yet, in the teacher's terms. */
  whyInvalid(): string {
    if (!this.draft.text.trim()) return 'Write the question text.';
    const filled = this.draft.choices.filter((c) => c.text.trim());
    if (filled.length < 2) return 'Add at least two choices.';
    if (!filled.some((c) => c.is_correct)) return 'Mark which choice is correct.';
    return '';
  }

  valid(): boolean {
    const filled = this.draft.choices.filter((c) => c.text.trim());
    return (
      this.draft.text.trim().length > 0 &&
      filled.length >= 2 &&
      filled.filter((c) => c.is_correct).length === 1
    );
  }
}
