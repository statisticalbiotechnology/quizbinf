import {
  Component,
  Input,
  OnChanges,
  OnDestroy,
  SimpleChanges,
  signal,
} from '@angular/core';

/** How long the first slot spins before it settles. */
const FIRST_SETTLE_MS = 1700;
/** How much longer each following slot spins, so they land one at a time. */
const STAGGER_MS = 1300;
/** Fastest and slowest gap between names as a slot decelerates. */
const FAST_MS = 55;
const SLOW_MS = 320;

/** A slot on the reel: the name showing now, and whether it has stopped. */
interface Slot {
  text: string;
  fixed: boolean;
}

/**
 * The draw, played out rather than just printed.
 *
 * Names roll past, then come to rest one slot at a time — the point is the
 * pause before each name lands, which is what makes the room look up. The
 * result is decided by the server before the first frame; the spin is
 * decoration over an answer that is already fixed, so a slow browser or a
 * cancelled animation still shows the same two people.
 *
 * The names it spins through are everyone who joined the session, never the
 * subset who answered this question — see `service.reel_names`.
 */
@Component({
  selector: 'app-name-draw',
  standalone: true,
  template: `
    <p class="reel" [class.settled]="settled()">
      @for (s of slots(); track $index) {
        <span class="slot" [class.spinning]="!s.fixed" [class.fixed]="s.fixed">
          {{ s.text }}
        </span>
      }
    </p>
  `,
  styles: [
    `
      .reel { margin: 0.6rem 0 0; display: flex; flex-wrap: wrap; gap: 1.2rem; }
      .slot { font-size: 1.6rem; font-weight: 700; line-height: 1.4;
              /* Held wide so a landed name does not shunt the next slot
                 sideways while it is still rolling. */
              min-width: 9rem; }
      .slot.spinning { color: #999; filter: blur(0.6px); }
      .slot.fixed { color: #2c7a51; animation: land 320ms ease-out; }
      @keyframes land {
        0% { transform: scale(1.35); opacity: 0.4; }
        60% { transform: scale(0.97); opacity: 1; }
        100% { transform: scale(1); }
      }
      /* Respect a viewer who has asked for stillness: the names simply
         appear, which is the same information without the motion. */
      @media (prefers-reduced-motion: reduce) {
        .slot.fixed { animation: none; }
      }
    `,
  ],
})
export class NameDrawComponent implements OnChanges, OnDestroy {
  /** Who was drawn. Decided by the server; the spin only delays showing it. */
  @Input({ required: true }) names: string[] = [];
  /** Names to roll through on the way there. */
  @Input() reel: string[] = [];

  slots = signal<Slot[]>([]);
  settled = signal(false);

  private timers: ReturnType<typeof setTimeout>[] = [];

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['names']) this.start();
  }

  ngOnDestroy(): void {
    this.stop();
  }

  private stop(): void {
    for (const t of this.timers) clearTimeout(t);
    this.timers = [];
  }

  private start(): void {
    this.stop();
    this.settled.set(false);
    if (!this.names.length) {
      this.slots.set([]);
      return;
    }

    // Nothing to roll through, or a viewer who asked for no motion: show the
    // result outright rather than faking a spin.
    const pool = this.reel.filter((n) => n);
    if (pool.length < 2 || this.prefersReducedMotion()) {
      this.slots.set(this.names.map((text) => ({ text, fixed: true })));
      this.settled.set(true);
      return;
    }

    this.slots.set(this.names.map((_, i) => ({ text: pool[i % pool.length], fixed: false })));
    this.names.forEach((_, i) => this.spin(i, pool, FIRST_SETTLE_MS + i * STAGGER_MS, 0));
  }

  private prefersReducedMotion(): boolean {
    return (
      typeof matchMedia === 'function' &&
      matchMedia('(prefers-reduced-motion: reduce)').matches
    );
  }

  /**
   * Roll one slot, slowing as it approaches its stop.
   *
   * A recursive timeout rather than an interval, because the gap between names
   * grows: the deceleration is what reads as suspense.
   */
  private spin(index: number, pool: string[], duration: number, elapsed: number): void {
    if (elapsed >= duration) {
      this.set(index, this.names[index], true);
      if (this.slots().every((s) => s.fixed)) this.settled.set(true);
      return;
    }

    const progress = elapsed / duration;
    const gap = FAST_MS + (SLOW_MS - FAST_MS) * progress ** 3;
    this.timers.push(
      setTimeout(() => {
        this.set(index, this.nextName(index, pool), false);
        this.spin(index, pool, duration, elapsed + gap);
      }, gap),
    );
  }

  /** A name from the reel other than the one this slot is showing. */
  private nextName(index: number, pool: string[]): string {
    const showing = this.slots()[index]?.text;
    const options = pool.filter((n) => n !== showing);
    return options[Math.floor(Math.random() * options.length)] ?? pool[0];
  }

  private set(index: number, text: string, fixed: boolean): void {
    this.slots.update((slots) =>
      slots.map((s, i) => (i === index ? { text, fixed } : s)),
    );
  }
}
