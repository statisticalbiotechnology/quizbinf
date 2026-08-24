import { Component } from '@angular/core';

import { SessionFeed } from './session-feed.service';

/**
 * The question the class is on, sized for a lecture hall.
 *
 * Projected throughout: while students are still scanning the QR code, while
 * a bout is open, and between the two bouts when they are arguing about it.
 * The choices are listed **without** any mark on the correct one — this is the
 * screen the room reads, and the whole method depends on them not being told.
 */
@Component({
  selector: 'app-question-panel',
  standalone: true,
  template: `
    @if (feed.currentQuestion(); as q) {
      <section class="panel">
        <p class="stage" [class.open]="stageIsOpen()">{{ label() }}</p>
        <div class="qtext">
          <span class="num">{{ q.position + 1 }}.</span>
          <span [innerHTML]="q.text_html"></span>
        </div>
        <ol class="choices">
          @for (c of q.choices; track c.id) {
            <li>{{ c.text }}</li>
          }
        </ol>
      </section>
    }
  `,
  styles: [
    `
      .panel { text-align: left; }
      .stage { font-size: 0.9rem; letter-spacing: 0.08em; text-transform: uppercase;
               color: #777; margin: 0 0 0.4rem; }
      .stage.open { color: #2c7a51; font-weight: 700; }
      /* Read from the back of the room, so larger than the rest of the app. */
      .qtext { font-size: 1.5rem; font-weight: 600; line-height: 1.35; }
      .qtext img { max-width: 100%; height: auto; border-radius: 6px; }
      .qtext :is(p, ul, ol) { display: inline; margin: 0; }
      .num { color: #888; margin-right: 0.3rem; }
      .choices { font-size: 1.25rem; line-height: 1.6; margin: 1rem 0 0; padding-left: 1.6rem; }
      .choices li { margin: 0.2rem 0; }
    `,
  ],
})
export class QuestionPanelComponent {
  constructor(public feed: SessionFeed) {}

  stageIsOpen(): boolean {
    const s = this.feed.stage();
    return s === 'pre' || s === 'post';
  }

  label(): string {
    switch (this.feed.stage()) {
      case 'pre':
        return 'Answering — first bout';
      case 'post':
        return 'Answering — second bout, after discussing';
      case 'discuss':
        return 'Discuss with your neighbour';
      default:
        return 'Coming up';
    }
  }
}
