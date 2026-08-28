import { Component, OnDestroy, OnInit, signal } from '@angular/core';

import { ApiService } from '../api.service';
import { JoinQrComponent } from './join-qr.component';
import { QuestionPanelComponent } from './question-panel.component';
import { SessionFeed } from './session-feed.service';

/** How often to refresh the room count while the join screen is projected. */
const POLL_MS = 3000;

/**
 * The projected screen for everything up to the results: how to get in, how
 * full the room is, and the question the class is on.
 *
 * It has two shapes, and which one is showing is decided by the session rather
 * than by the teacher. While students are still arriving the QR code owns the
 * screen. Once the first bout opens the question takes that space and the code
 * shrinks into a corner — small, but still there, because someone always fails
 * to log in during the scramble and needs a way in mid-lecture.
 *
 * It shows the choices, never which of them is correct.
 */
@Component({
  selector: 'app-teacher-session-join',
  standalone: true,
  imports: [JoinQrComponent, QuestionPanelComponent],
  template: `
    @if (feed.started()) {
      <div class="running">
        <div class="question">
          <app-question-panel />
        </div>
        <aside class="aside">
          <app-join-qr [small]="true" />
          <p class="joined-small">{{ joined() }} joined</p>
        </aside>
      </div>
    } @else {
      <div class="wrap">
        <app-join-qr />

        <div class="how">
          <code class="url">{{ feed.joinUrl() }}</code>
          <p class="alt">
            …or open <strong>{{ host() }}</strong><br />
            and enter code <strong class="code">{{ feed.code() }}</strong>
          </p>

          <p class="joined">
            <strong>{{ joined() }}</strong>
            {{ joined() === 1 ? 'student has' : 'students have' }} joined
          </p>
          <p class="connected">{{ connected() }} connected right now</p>
        </div>
      </div>

      <div class="question">
        <app-question-panel />
      </div>
    }
  `,
  styles: [
    `
      .wrap { display: flex; gap: 2rem; align-items: center; justify-content: center;
              max-width: 46rem; margin: 2rem auto; padding: 1rem; flex-wrap: wrap; }
      .url { font-size: 0.95rem; word-break: break-all; }
      .alt { margin-top: 1rem; line-height: 1.8; }
      .code { font-size: 1.6rem; letter-spacing: 0.1em; }
      .joined { font-size: 1.6rem; margin-top: 1.5rem; }
      .connected { font-size: 0.85rem; color: #777; margin-top: -0.8rem; }
      .question { max-width: 46rem; margin: 0 auto 2rem; padding: 1.2rem 1rem 0; }

      /* Once a bout is running the question leads and the code steps aside. */
      .running { display: flex; gap: 1.5rem; align-items: flex-start;
                 max-width: 52rem; margin: 1.5rem auto; padding: 1rem; }
      .running .question { flex: 1; margin: 0; padding: 0; }
      .aside { flex-shrink: 0; }
      .joined-small { font-size: 0.85rem; color: #777; text-align: center; margin: 0.3rem 0 0; }

      @media (max-width: 40rem) {
        .running { flex-direction: column-reverse; }
      }
    `,
  ],
})
export class TeacherSessionJoinComponent implements OnInit, OnDestroy {
  joined = signal(0);
  connected = signal(0);
  private timer?: ReturnType<typeof setInterval>;

  constructor(public feed: SessionFeed, private api: ApiService) {}

  ngOnInit(): void {
    this.refresh();
    this.timer = setInterval(() => this.refresh(), POLL_MS);
  }

  ngOnDestroy(): void {
    if (this.timer) clearInterval(this.timer);
  }

  host(): string {
    const url = this.feed.joinUrl();
    if (!url) return '';
    try {
      return new URL(url).host;
    } catch {
      return '';
    }
  }

  private refresh(): void {
    this.api.participants(this.feed.code()).subscribe((p) => {
      this.joined.set(p.joined);
      this.connected.set(p.connected);
    });
  }
}
