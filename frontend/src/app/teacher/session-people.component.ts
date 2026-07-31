import { Component, OnInit, signal } from '@angular/core';

import { ApiService } from '../api.service';
import { ParticipantRow, Question } from '../models';
import { SessionFeed } from './session-feed.service';

/**
 * Who took part, and who got it right.
 *
 * The only view in the app that shows individuals rather than aggregates, so
 * it carries a standing warning not to project it — the other three views are
 * meant for the lecture-hall screen and this one sits next to them in the nav.
 * Names are hidden until revealed, so opening the tab by accident in front of
 * the class does not expose anyone.
 */
@Component({
  selector: 'app-teacher-session-people',
  standalone: true,
  template: `
    <div class="wrap">
      <p class="warning">
        <strong>Personal data — do not project.</strong>
        This page names individual students and how they answered.
      </p>

      <div class="actions">
        <button (click)="revealed.set(!revealed())">
          {{ revealed() ? 'Hide names' : 'Show names' }}
        </button>
        <a class="csv" [href]="csvUrl" download>Download CSV</a>
      </div>

      @if (!rows().length) {
        <p class="empty">Nobody has joined this session yet.</p>
      } @else if (!revealed()) {
        <p class="empty">
          {{ rows().length }} {{ rows().length === 1 ? 'student has' : 'students have' }}
          taken part. Names are hidden — click “Show names”.
        </p>
      } @else {
        <table>
          <thead>
            <tr>
              <th class="who">Student</th>
              @for (q of questions(); track q.id) {
                <th class="q" [title]="q.text">Q{{ q.position + 1 }}</th>
              }
              <th class="tot">Correct<br />before</th>
              <th class="tot">Correct<br />after</th>
            </tr>
          </thead>
          <tbody>
            @for (row of rows(); track row.username) {
              <tr>
                <td class="who">
                  {{ row.display_name }}
                  <span class="uid">{{ row.username }}</span>
                </td>
                @for (a of row.answers; track a.question_id) {
                  <td class="q">
                    <span class="mark" [class]="cls(a.pre)">{{ glyph(a.pre) }}</span>
                    <span class="mark" [class]="cls(a.post)">{{ glyph(a.post) }}</span>
                  </td>
                }
                <td class="tot">{{ row.pre_correct }}</td>
                <td class="tot">{{ row.post_correct }}</td>
              </tr>
            }
          </tbody>
        </table>
        <p class="legend">
          Each cell shows the answer before and after the discussion:
          <span class="mark ok">✓</span> correct,
          <span class="mark no">✗</span> wrong,
          <span class="mark none">–</span> did not answer.
        </p>
      }
    </div>
  `,
  styles: [
    `
      .wrap { max-width: 52rem; margin: 1.5rem auto; padding: 1rem; }
      .warning { background: #fdf3f2; border: 1px solid #e6b8b2; border-radius: 6px;
                 padding: 0.6rem 0.8rem; color: #8a2b20; }
      .actions { display: flex; gap: 0.6rem; align-items: center; margin: 1rem 0; }
      .csv { padding: 0.5rem 0.9rem; border: 1px solid var(--border); border-radius: 6px;
             text-decoration: none; }
      table { border-collapse: collapse; width: 100%; }
      th, td { border-bottom: 1px solid #eee; padding: 0.4rem 0.5rem; text-align: center; }
      th.who, td.who { text-align: left; }
      .uid { color: #888; font-size: 0.8rem; margin-left: 0.4rem; }
      .mark { display: inline-block; width: 1.2rem; font-weight: 700; }
      .mark.ok { color: #2c7a51; }
      .mark.no { color: #c0392b; }
      .mark.none { color: #bbb; }
      .tot { font-variant-numeric: tabular-nums; }
      .legend { font-size: 0.85rem; color: #555; margin-top: 0.8rem; }
      .empty { color: #777; }
    `,
  ],
})
export class TeacherSessionPeopleComponent implements OnInit {
  rows = signal<ParticipantRow[]>([]);
  questions = signal<Question[]>([]);
  revealed = signal(false);
  csvUrl = '';

  constructor(public feed: SessionFeed, private api: ApiService) {}

  ngOnInit(): void {
    this.csvUrl = this.api.participationCsvUrl(this.feed.code());
    this.api.participation(this.feed.code()).subscribe((r) => {
      this.questions.set(r.questions);
      this.rows.set(r.rows);
    });
  }

  cls(v: boolean | null): string {
    return v === null ? 'mark none' : v ? 'mark ok' : 'mark no';
  }

  glyph(v: boolean | null): string {
    return v === null ? '–' : v ? '✓' : '✗';
  }
}
