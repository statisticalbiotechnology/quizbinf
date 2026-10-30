import { Component, effect, signal } from '@angular/core';

import { ApiService } from '../api.service';
import { Draw, Question, WordCloud } from '../models';
import { MarkdownHtmlDirective } from '../md-html.directive';
import { JoinQrComponent } from './join-qr.component';
import { NameDrawComponent } from './name-draw.component';
import { WordCloudComponent } from './word-cloud.component';
import { SessionFeed } from './session-feed.service';

/**
 * The projected results: how the class answered before and after discussing.
 *
 * A phase appears only once its round is halted, so this view is safe to leave
 * on the projector for a whole question.
 *
 * Two things it holds back. Which choice is correct stays hidden until the
 * second bout has run — the pre distribution is projected *before* the
 * discussion, and a green tick beside it would settle the argument the
 * discussion is supposed to be. And the drawn discussants are shown by name
 * under the whole distribution, never beside a bar, so being called on does
 * not disclose what that student answered.
 */
@Component({
  selector: 'app-teacher-session-report',
  standalone: true,
  imports: [JoinQrComponent, MarkdownHtmlDirective, NameDrawComponent, WordCloudComponent],
  template: `
    <div class="wrap with-way-in">
      <div class="main">
      <div class="top">
        <!-- Study material: students ask for the questions afterwards, and
             where they look is Canvas. Figures are rewritten to absolute URLs
             by the export so they still load once pasted there. -->
        @if (feed.state(); as s) {
          <p class="handout">
            Hand out:
            <a [href]="exportUrl(s.quiz_id, 'html')">HTML</a> ·
            <a [href]="exportUrl(s.quiz_id, 'md')">Markdown</a>
            <label class="with-answers">
              <input type="checkbox" [checked]="withAnswers()"
                     (change)="withAnswers.set(!withAnswers())" />
              with answers
            </label>
          </p>
        }
      </div>

      @if (!feed.questions().length) {
        <p class="empty">Nothing to report yet.</p>
      }

      @for (q of feed.questions(); track q.id) {
        @if (feed.comparisons()[q.id]; as c) {
          <section class="q">
            <div class="qtext"><span class="num">{{ q.position + 1 }}.</span>
            <span [mdHtml]="q.text_html"></span></div>

            @if (q.kind === 'text') {
              @if (clouds()[q.id]; as cloud) {
                <app-word-cloud [words]="cloud.words" [answers]="cloud.answers" />
              } @else {
                <p class="pending">
                  @if (feed.openRoundFor(q)) {
                    Answers are open — the cloud appears when you halt the round.
                  } @else if (cloudError()[q.id]) {
                    {{ cloudError()[q.id] }}
                  } @else {
                    Not asked yet.
                  }
                </p>
              }
            } @else if (!shown(q)) {
              <p class="pending">
                @if (feed.openRoundFor(q)) {
                  Answers are open — results appear when you halt the round.
                } @else {
                  Not asked yet.
                }
              </p>
            } @else {
              <div class="hist">
                @for (ch of q.choices; track ch.id) {
                  <div class="row" [class.correct]="revealCorrect(q) && ch.is_correct">
                    <span class="label">
                      <span class="choice-md" [mdHtml]="ch.text_html"></span>
                      @if (revealCorrect(q) && ch.is_correct) { <span class="tick">✓</span> }
                    </span>
                    <span class="bars">
                      @if (feed.showPhase(q, 'pre') && c.pre) {
                        <span class="line">
                          <span class="bar pre" [style.width.%]="feed.pct(c.pre, ch.id, q)"></span>
                          <span class="n">{{ c.pre[ch.id] || 0 }}</span>
                        </span>
                      }
                      @if (feed.showPhase(q, 'post') && c.post) {
                        <span class="line">
                          <span class="bar post" [style.width.%]="feed.pct(c.post, ch.id, q)"></span>
                          <span class="n">{{ c.post[ch.id] || 0 }}</span>
                        </span>
                      }
                    </span>
                  </div>
                }
                <p class="legend">
                  <span class="sw pre"></span> before discussion
                  ({{ feed.total(c.pre) }})
                  &nbsp;&nbsp;
                  <span class="sw post"></span> after discussion
                  ({{ feed.total(c.post) }})
                </p>

                <div class="draw">
                  <button type="button" (click)="draw(q)" [disabled]="drawing() === q.id">
                    {{ hasDraw(q) ? 'Draw again' : 'Draw two to explain' }}
                  </button>
                  @if (drawn()[q.id]; as draw) {
                    @if (draw.names.length) {
                      <app-name-draw [names]="draw.names" [reel]="draw.reel" />
                      <p class="ask">…tell us how you reasoned.</p>
                    } @else {
                      <p class="nobody">Nobody has answered this question yet.</p>
                    }
                  }
                  @if (drawError(); as e) { <p class="error">{{ e }}</p> }
                </div>

                <!-- Only once the answer itself is shown, and folded: the
                     teacher opens it when they want it on the wall. -->
                @if (revealCorrect(q) && q.explanation_html) {
                  <details class="explanation">
                    <summary>Explanation</summary>
                    <div class="qtext" [mdHtml]="q.explanation_html"></div>
                  </details>
                }
              </div>
            }
          </section>
        }
      }
      </div>
      <!-- A way in on every projected view of a running session: whichever
           screen is up, somebody has just arrived or failed to log in. -->
      <aside class="way-in">
        <app-join-qr [small]="true" />
      </aside>
    </div>
  `,
  styles: [
    `
      .explanation { margin-top: 0.8rem; border-left: 3px solid #ddd; padding-left: 0.8rem; }
      .explanation summary { cursor: pointer; color: #555; }
      .wrap { max-width: 46rem; margin: 1.5rem auto; padding: 1rem; }
      .with-way-in { max-width: 56rem; display: grid; gap: 1.5rem;
                     grid-template-columns: minmax(0, 1fr) auto; align-items: start; }
      .way-in { position: sticky; top: 1rem; }
      @media (max-width: 40rem) {
        .with-way-in { grid-template-columns: minmax(0, 1fr); }
        .way-in { position: static; order: -1; }
      }
      .top { display: flex; justify-content: space-between; align-items: flex-start;
             gap: 1rem; }
      .handout { font-size: 0.9rem; color: #555; margin: 0; }
      .handout a { margin: 0 0.15rem; }
      .with-answers { display: block; margin-top: 0.3rem; font-size: 0.85rem; }
      .q { border-top: 1px solid #eee; padding: 1rem 0; }
      .qtext { font-weight: 600; font-size: 1.1rem; }
      .qtext :is(p, ul, ol) { display: inline; margin: 0; }
      .pending { color: #777; font-style: italic; }
      .row { display: flex; align-items: center; gap: 0.6rem; margin: 0.5rem 0; }
      .label { width: 14rem; }
      .tick { color: #2c7a51; font-weight: 700; }
      .bars { flex: 1; display: flex; flex-direction: column; gap: 3px; }
      .line { display: flex; align-items: center; gap: 0.4rem; }
      .bar { height: 1.1rem; display: block; min-width: 2px; }
      .bar.pre { background: #9bd; }
      .bar.post { background: #2c7a51; }
      .n { font-size: 0.8rem; color: #555; }
      .row.correct .label { font-weight: 700; }
      .sw { display: inline-block; width: 0.8rem; height: 0.8rem; vertical-align: middle; }
      .sw.pre { background: #9bd; }
      .sw.post { background: #2c7a51; }
      .legend { font-size: 0.85rem; color: #555; margin-top: 0.8rem; }
      /* Under the whole distribution, never beside a bar: being drawn must not
         disclose which choice that student picked. */
      .draw { margin-top: 0.8rem; }
      .ask { margin: 0.1rem 0 0; color: #555; }
      .nobody { color: #777; font-style: italic; }
      .empty { color: #777; }
      .error { color: #c0392b; }
    `,
  ],
})
export class TeacherSessionReportComponent {
  /** Clouds already fetched, by question id. */
  clouds = signal<Record<number, WordCloud>>({});
  cloudError = signal<Record<number, string>>({});
  /** The last draw per question. Empty `names` means nobody answered. */
  drawn = signal<Record<number, Draw>>({});
  drawing = signal<number | null>(null);
  drawError = signal('');
  /** Study material normally wants the answers; before a class it may not. */
  withAnswers = signal(true);

  constructor(public feed: SessionFeed, private api: ApiService) {
    // Fetch each cloud once its round has been halted. The feed re-emits on
    // every state change, which is exactly when a halt becomes visible.
    effect(() => {
      const code = this.feed.code();
      if (!code) return;
      for (const q of this.feed.questions()) {
        if (q.kind !== 'text') continue;
        if (this.clouds()[q.id] || this.feed.openRoundFor(q)) continue;
        if (!this.feed.ran(q, 'pre')) continue;
        this.api.wordCloud(code, q.id).subscribe({
          next: (cloud) => this.clouds.update((m) => ({ ...m, [q.id]: cloud })),
          error: (err) =>
            this.cloudError.update((m) => ({
              ...m,
              [q.id]: err?.error?.detail ?? 'Could not load the cloud.',
            })),
        });
      }
    });
  }

  exportUrl(quizId: number, format: 'md' | 'html'): string {
    return this.api.quizExportUrl(quizId, format, this.withAnswers());
  }

  /** Something to show only once at least one phase is closed. */
  shown(q: Question): boolean {
    const c = this.feed.comparisons()[q.id];
    if (!c) return false;
    return (
      (!!c.pre && this.feed.showPhase(q, 'pre')) ||
      (!!c.post && this.feed.showPhase(q, 'post'))
    );
  }

  /**
   * Whether the correct choice may be marked yet.
   *
   * Only once the second bout has been halted. Before that this view is
   * projected between the two bouts, and the point of asking twice is that the
   * class argues it out rather than reading the answer off the screen.
   */
  revealCorrect(q: Question): boolean {
    return this.feed.ran(q, 'post');
  }

  /** Whether a draw has already produced names for this question. */
  hasDraw(q: Question): boolean {
    return (this.drawn()[q.id]?.names ?? []).length > 0;
  }

  draw(q: Question): void {
    this.drawError.set('');
    this.drawing.set(q.id);
    this.api.discussants(this.feed.code(), q.id).subscribe({
      next: (result) => {
        // A new object every time, so drawing again restarts the animation
        // even when the same two people come up.
        this.drawn.update((d) => ({ ...d, [q.id]: { ...result } }));
        this.drawing.set(null);
      },
      error: () => {
        this.drawError.set('Could not draw anyone just now.');
        this.drawing.set(null);
      },
    });
  }
}
