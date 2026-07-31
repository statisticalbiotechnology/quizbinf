import { Component } from '@angular/core';

import { Question } from '../models';
import { SessionFeed } from './session-feed.service';

/**
 * The projected results: how the class answered before and after discussing.
 *
 * A phase appears only once its round is halted, so this view is safe to leave
 * on the projector for a whole question.
 */
@Component({
  selector: 'app-teacher-session-report',
  standalone: true,
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
                  <div class="row" [class.correct]="ch.is_correct">
                    <span class="label">
                      {{ ch.text }}
                      @if (ch.is_correct) { <span class="tick">✓</span> }
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
      .empty { color: #777; }
    `,
  ],
})
export class TeacherSessionReportComponent {
  constructor(public feed: SessionFeed) {}

  /** Something to show only once at least one phase is closed. */
  shown(q: Question): boolean {
    const c = this.feed.comparisons()[q.id];
    if (!c) return false;
    return (
      (!!c.pre && this.feed.showPhase(q, 'pre')) ||
      (!!c.post && this.feed.showPhase(q, 'post'))
    );
  }
}
