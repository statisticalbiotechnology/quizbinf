import {
  AfterViewInit,
  Component,
  ElementRef,
  Input,
  OnChanges,
  ViewChild,
} from '@angular/core';

import { Word } from '../models';

/** Font size for the most common word, and for one nobody repeated. */
const MAX_FONT = 96;
const MIN_FONT = 18;
/** Enough for a lecture hall; past this the wall is unreadable anyway. */
const MAX_WORDS = 80;

/**
 * The words of a halted word-cloud round, laid out for the projector.
 *
 * Drawn here rather than with a word-cloud library on purpose: the layout is
 * a spiral and a rectangle-overlap test, which is less code than wiring a
 * dependency into the build and leaves nothing to go stale. If the packing
 * ever needs to be cleverer — rotated words, shapes — wordcloud2.js does that
 * well and this component is the single place that would change.
 *
 * Size follows the square root of the count rather than the count itself: a
 * word said twice as often is twice the *area*, which is how a reader
 * compares two words, and linear sizing makes one popular word swallow the
 * cloud.
 */
@Component({
  selector: 'app-word-cloud',
  standalone: true,
  template: `
    <figure class="cloud">
      <canvas #canvas [attr.aria-label]="summary()"></canvas>
      <figcaption>
        {{ answers }} {{ answers === 1 ? 'answer' : 'answers' }},
        {{ words.length }} different
      </figcaption>
      <!-- The same words as text: a projector shows the canvas, a screen
           reader and a copy-paste want this. -->
      <ul class="plain">
        @for (w of words; track w.text) {
          <li>{{ w.text }} <span class="n">×{{ w.count }}</span></li>
        }
      </ul>
    </figure>
  `,
  styles: [
    `
      .cloud { margin: 0.5rem 0; }
      canvas { width: 100%; height: auto; display: block; }
      figcaption { color: #666; font-size: 0.85rem; margin-top: 0.3rem; }
      .plain { list-style: none; padding: 0; margin: 0.4rem 0 0; display: flex;
               flex-wrap: wrap; gap: 0.3rem 0.8rem; font-size: 0.8rem; color: #555; }
      .plain .n { color: #999; }
    `,
  ],
})
export class WordCloudComponent implements AfterViewInit, OnChanges {
  @Input({ required: true }) words: Word[] = [];
  @Input() answers = 0;
  @ViewChild('canvas') canvasRef?: ElementRef<HTMLCanvasElement>;

  ngAfterViewInit(): void {
    this.draw();
  }

  ngOnChanges(): void {
    this.draw();
  }

  summary(): string {
    return this.words.map((w) => `${w.text} (${w.count})`).join(', ');
  }

  private draw(): void {
    const canvas = this.canvasRef?.nativeElement;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Fixed drawing surface, scaled by CSS: the projector decides the pixels,
    // the layout should not change with the window.
    const width = (canvas.width = 1200);
    const height = (canvas.height = 600);
    ctx.clearRect(0, 0, width, height);

    const words = this.words.slice(0, MAX_WORDS);
    if (!words.length) return;
    const top = words[0].count;
    const taken: { x: number; y: number; w: number; h: number }[] = [];

    words.forEach((word, i) => {
      const size = Math.max(
        MIN_FONT,
        Math.round(MAX_FONT * Math.sqrt(word.count / top)),
      );
      ctx.font = `600 ${size}px system-ui, sans-serif`;
      const w = ctx.measureText(word.text).width;
      const h = size * 1.1;
      const place = this.findSpot(w, h, width, height, taken, i);
      if (!place) return; // no room left; the long tail is in the list below
      taken.push({ x: place.x, y: place.y, w, h });
      // Alternating hues keep neighbours apart without colour carrying meaning.
      ctx.fillStyle = `hsl(${(i * 47) % 360} 45% ${i % 2 ? 42 : 30}%)`;
      ctx.textBaseline = 'top';
      ctx.fillText(word.text, place.x, place.y);
    });
  }

  /**
   * Walk outwards from the middle until the word fits where nothing else is.
   *
   * The spiral is what makes the common words land in the centre: they are
   * placed first, so they take the middle, and later words settle around them.
   */
  private findSpot(
    w: number,
    h: number,
    width: number,
    height: number,
    taken: { x: number; y: number; w: number; h: number }[],
    seed: number,
  ): { x: number; y: number } | null {
    const cx = width / 2;
    const cy = height / 2;
    const step = 6;
    for (let t = 0; t < 2000; t++) {
      const angle = 0.35 * t + seed;
      const radius = step * 0.35 * t;
      const x = cx + radius * Math.cos(angle) - w / 2;
      const y = cy + radius * Math.sin(angle) * 0.55 - h / 2;
      if (x < 0 || y < 0 || x + w > width || y + h > height) continue;
      const clash = taken.some(
        (r) => x < r.x + r.w && x + w > r.x && y < r.y + r.h && y + h > r.y,
      );
      if (!clash) return { x, y };
    }
    return null;
  }
}
