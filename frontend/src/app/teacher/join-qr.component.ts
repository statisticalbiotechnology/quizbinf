import { Component, Input, OnInit } from '@angular/core';

import { API_BASE } from '../api.config';
import { SessionFeed } from './session-feed.service';

/**
 * The QR code that gets a student into this session.
 *
 * Its own component because it belongs on more than one projected view: large
 * on the join screen while the room fills, and small beside the question and
 * the results afterwards — someone always fails to log in during the scramble,
 * and they should not have to ask for the code to be put back up.
 */
@Component({
  selector: 'app-join-qr',
  standalone: true,
  template: `
    <div class="qr-block" [class.small]="small">
      <img class="qr" [src]="qrSrc" alt="QR code to join this session" />
      <p class="caption">
        @if (small) {
          Join late: <strong>{{ feed.code() }}</strong>
        } @else {
          Scan to join
        }
      </p>
    </div>
  `,
  styles: [
    `
      .qr-block { text-align: center; }
      /* Large: read from the back of a lecture hall. */
      .qr { width: 340px; height: 340px; display: block; }
      .caption { font-size: 1.4rem; font-weight: 600; margin: 0.4rem 0 0; }
      /* Small: still scannable from a seat, but the question owns the screen. */
      .small .qr { width: 120px; height: 120px; }
      .small .caption { font-size: 0.85rem; font-weight: 400; color: #777; }
      .small .caption strong { letter-spacing: 0.08em; }
    `,
  ],
})
export class JoinQrComponent implements OnInit {
  /** Small enough to sit beside a question rather than fill the screen. */
  @Input() small = false;

  qrSrc = '';

  constructor(public feed: SessionFeed) {}

  ngOnInit(): void {
    // Cache-buster: this URL once returned index.html via the SPA fallback,
    // and a cached copy renders as a broken image.
    this.qrSrc = `${API_BASE}/api/sessions/${this.feed.code()}/qr.svg?v=${Date.now()}`;
  }
}
