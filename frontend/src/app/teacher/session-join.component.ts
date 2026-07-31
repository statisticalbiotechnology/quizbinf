import { Component, OnDestroy, OnInit, signal } from '@angular/core';

import { API_BASE } from '../api.config';
import { ApiService } from '../api.service';
import { SessionFeed } from './session-feed.service';

/** How often to refresh the room count while the join screen is projected. */
const POLL_MS = 3000;

/**
 * The projected join screen: how to get in, and how full the room is.
 *
 * Deliberately shows nothing about the quiz itself — this is on the projector
 * while students are still arriving.
 */
@Component({
  selector: 'app-teacher-session-join',
  standalone: true,
  template: `
    <div class="wrap">
      <img class="qr" [src]="qrSrc" alt="QR code to join this session" />

      <div class="how">
        <p class="lead">Scan to join</p>
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
  `,
  styles: [
    `
      .wrap { display: flex; gap: 2rem; align-items: center; justify-content: center;
              max-width: 46rem; margin: 2rem auto; padding: 1rem; flex-wrap: wrap; }
      /* Large: this is read from the back of a lecture hall. */
      .qr { width: 340px; height: 340px; display: block; }
      .lead { font-size: 1.4rem; font-weight: 600; margin: 0 0 0.5rem; }
      .url { font-size: 0.95rem; word-break: break-all; }
      .alt { margin-top: 1rem; line-height: 1.8; }
      .code { font-size: 1.6rem; letter-spacing: 0.1em; }
      .joined { font-size: 1.6rem; margin-top: 1.5rem; }
      .connected { font-size: 0.85rem; color: #777; margin-top: -0.8rem; }
    `,
  ],
})
export class TeacherSessionJoinComponent implements OnInit, OnDestroy {
  joined = signal(0);
  connected = signal(0);
  qrSrc = '';
  private timer?: ReturnType<typeof setInterval>;

  constructor(public feed: SessionFeed, private api: ApiService) {}

  ngOnInit(): void {
    // Cache-buster: this URL once returned index.html via the SPA fallback,
    // and a cached copy renders as a broken image.
    this.qrSrc = `${API_BASE}/api/sessions/${this.feed.code()}/qr.svg?v=${Date.now()}`;
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
