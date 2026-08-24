import { Component, signal } from '@angular/core';

import { ApiService } from '../api.service';
import { Draw, Question } from '../models';
import { NameDrawComponent } from './name-draw.component';
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
  imports: [NameDrawComponent],
  template: `
    <div class="wrap">
      @if (!feed.questions().length) {
        <p class="empty">Nothing to report yet.</p>
      }

      @for (q of feed.questions(); track q.id) {
        @if (feed.comparisons()[q.id]; as c) {
          <section class="q">
            <div class="qtext"><span class="num">{{ q.position + 1 }}.</span>
            <span [innerHTML]="q.text_html"></span></div>

            @if (!shown(q)) {
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
                      {{ ch.text }}
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
              </div>
            }
          </section>
        }
      }
    </div>
  `,
  styles: [
    `
      .wrap { max-width: 46rem; margin: 1.5rem auto; padding: 1rem; }
      .q { border-top: 1px solid #eee; padding: 1rem 0; }
      .qtext { font-weight: 600; font-size: 1.1rem; }
      .qtext img { max-width: 22rem; height: auto; border-radius: 6px; }
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
  /** The last draw per question. Empty `names` means nobody answered. */
  drawn = signal<Record<number, Draw>>({});
  drawing = signal<number | null>(null);
  drawError = signal('');

  constructor(public feed: SessionFeed, private api: ApiService) {}

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
