import { Component, EventEmitter, Input, OnDestroy, OnInit, Output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../api.service';
import { MarkdownHtmlDirective } from '../md-html.directive';
import { QuestionKind } from '../models';

export interface ChoiceDraft {
  /** Present when this choice already exists; absent means a new one. */
  id?: number;
  text: string;
  is_correct: boolean;
}

export interface QuestionDraft {
  text: string;
  /** 'choice' is peer instruction; 'text' collects words for a cloud. */
  kind: QuestionKind;
  /** For a word cloud: how many entries each student may write. */
  max_words: number;
  /** Why the answer is right, Markdown; shown to the teacher after the second bout. */
  explanation: string;
  choices: ChoiceDraft[];
}

/** The most entries a cloud may ask for; the server holds the same limit. */
export const MAX_WORDS = 5;

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
  imports: [FormsModule, MarkdownHtmlDirective],
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
        <span class="md-hint">
          Markdown supported, in the choices too. Uploads are inserted as an
          image link — add <code>&#123;width=60%&#125;</code> after one to size
          it, as a percentage of the column it appears in. Formulas:
          <code>$x^2$</code> inline, <code>$$…$$</code> on a line of their own.
        </span>
      </div>
      @if (uploadError()) {
        <p class="error">{{ uploadError() }}</p>
      }

      @if (draft.text.trim()) {
        <div class="preview">
          <span class="preview-label">Preview</span>
          <div [mdHtml]="preview()"></div>
        </div>
      }

      <div class="kind">
        <label>
          <input
            type="radio"
            [name]="'kind' + formId"
            [checked]="draft.kind === 'choice'"
            (change)="setKind('choice')"
            [disabled]="lockedKind"
          />
          Multiple choice
        </label>
        <label>
          <input
            type="radio"
            [name]="'kind' + formId"
            [checked]="draft.kind === 'text'"
            (change)="setKind('text')"
            [disabled]="lockedKind"
          />
          Word cloud
        </label>
        @if (lockedKind) {
          <span class="kind-hint">
            fixed once the question exists — delete and add it again to change
          </span>
        } @else if (draft.kind === 'text') {
          <span class="kind-hint">
            asked once, no right answer, shown as a cloud after you halt it
          </span>
        }
      </div>
      @if (draft.kind === 'text') {
        <label class="words">
          Each student writes
          <select [(ngModel)]="draft.max_words" [name]="'maxWords' + formId">
            @for (n of wordCounts; track n) {
              <option [ngValue]="n">{{ n === 1 ? '1 word or phrase' : n + ' words or phrases' }}</option>
            }
          </select>
        </label>
      }

      @if (draft.kind === 'choice') {
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
          <input
            [(ngModel)]="c.text"
            [name]="'c' + formId + $index"
            placeholder="Choice text"
            (ngModelChange)="refreshPreview()"
          />
          @if (draft.choices.length > 2) {
            <button type="button" class="drop" (click)="removeChoice($index)" title="Remove choice">
              ✕
            </button>
          }
        </div>
      }

      <button type="button" (click)="addChoiceRow()">+ choice</button>

      <label class="explanation">
        <span>Explanation <span class="kind-hint">(optional; shown in the Report view
          after the second bout, and in the handout with answers, never on phones)</span></span>
        <textarea
          [(ngModel)]="draft.explanation"
          [name]="'explanation' + formId"
          rows="3"
          placeholder="Why the right answer is right. Markdown and $formulas$ work here too."
        ></textarea>
      </label>
      }
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
      .kind { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap;
              margin: 0.5rem 0; font-size: 0.9rem; }
      .kind label { display: inline-flex; align-items: center; gap: 0.3rem; cursor: pointer; }
      .kind-hint { color: #666; font-size: 0.85rem; }
      .explanation { display: flex; flex-direction: column; gap: 0.25rem; margin: 0.6rem 0; }
      .explanation textarea { width: 100%; box-sizing: border-box; font: inherit; }
      .words { display: inline-flex; align-items: center; gap: 0.4rem; margin: 0.25rem 0 0.5rem; }
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
      .preview table { border-collapse: collapse; }
      .preview th, .preview td { border: 1px solid #ddd; padding: 0.2rem 0.4rem; }
      .error { color: #c0392b; }
    `,
  ],
})
export class QuestionEditorComponent implements OnInit, OnDestroy {
  @Input({ required: true }) draft!: QuestionDraft;
  @Input() submitLabel = 'Save question';
  @Input() showCancel = false;
  @Input() error = '';
  /** Distinguishes radio groups when two editors are on the page at once. */
  @Input() formId = '';
  /**
   * Set on the edit form, which does not offer a change of kind.
   *
   * The server allows one up until the question has been asked — after that
   * answers exist in the shape the old kind collected — but the editor cannot
   * tell from a question whether it has been asked, and an offer that is
   * refused on save is worse than one that was never made.
   */
  @Input() lockedKind = false;

  @Output() save = new EventEmitter<void>();
  @Output() cancel = new EventEmitter<void>();

  uploading = signal(false);
  uploadError = signal('');
  preview = signal<string>('');
  private previewTimer?: ReturnType<typeof setTimeout>;

  constructor(private api: ApiService) {}

  ngOnInit(): void {
    // An edit opens on an existing question: show it rendered straight away,
    // not only once something has been typed.
    if (this.draft.text.trim()) this.refreshPreview();
  }

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
      // The choices are Markdown too, so they belong in the preview: a
      // formula with a typo should be caught here, not on the projector.
      const choices =
        this.draft.kind === 'choice'
          ? this.draft.choices
              .filter((c) => c.text.trim())
              .map((c, i) => `${i + 1}. ${c.text.trim()}`)
              .join('\n')
          : '';
      this.api.renderMarkdown(choices ? `${text}\n\n${choices}` : text).subscribe({
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

  /**
   * Switch between the two kinds.
   *
   * The choices are kept rather than cleared while the form is open, so a
   * teacher who tries "word cloud" and changes their mind has not lost the
   * choices they typed; `save` sends none for a word cloud, and the server
   * refuses any that slip through.
   */
  readonly wordCounts = Array.from({ length: MAX_WORDS }, (_, i) => i + 1);

  setKind(kind: QuestionKind): void {
    if (this.lockedKind) return;
    this.draft.kind = kind;
  }

  /** Why the question cannot be saved yet, in the teacher's terms. */
  whyInvalid(): string {
    if (!this.draft.text.trim()) return 'Write the question text.';
    if (this.draft.kind === 'text') return '';
    const filled = this.draft.choices.filter((c) => c.text.trim());
    if (filled.length < 2) return 'Add at least two choices.';
    if (!filled.some((c) => c.is_correct)) return 'Mark which choice is correct.';
    return '';
  }

  valid(): boolean {
    if (!this.draft.text.trim()) return false;
    if (this.draft.kind === 'text') return true;
    const filled = this.draft.choices.filter((c) => c.text.trim());
    return filled.length >= 2 && filled.filter((c) => c.is_correct).length === 1;
  }
}
