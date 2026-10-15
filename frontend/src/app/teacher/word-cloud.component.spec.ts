import { TestBed } from '@angular/core/testing';

import { WordCloudComponent } from './word-cloud.component';

/**
 * The cloud is drawn on a canvas, which a test cannot read back meaningfully.
 * What it can pin is the part a projector does not show: that the words are
 * also available as text, and that drawing a realistic cloud does not throw —
 * the layout walks a spiral and gives up when the canvas is full, and "gives
 * up" must mean a shorter cloud rather than a broken lecture screen.
 */
describe('WordCloudComponent', () => {
  beforeEach(() => TestBed.configureTestingModule({ imports: [WordCloudComponent] }));

  it('lists every word with its count for anything that is not the canvas', () => {
    const fixture = TestBed.createComponent(WordCloudComponent);
    fixture.componentInstance.words = [
      { text: 'DNA', count: 3 },
      { text: 'alignment', count: 1 },
    ];
    fixture.componentInstance.answers = 4;
    fixture.detectChanges();

    expect(fixture.componentInstance.summary()).toBe('DNA (3), alignment (1)');
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('DNA');
    expect(text).toContain('alignment');
    expect(text).toContain('4 answers');
  });

  it('draws a hall-sized cloud without failing', () => {
    const fixture = TestBed.createComponent(WordCloudComponent);
    // More words than can fit, which is the case that has to degrade quietly.
    fixture.componentInstance.words = Array.from({ length: 120 }, (_, i) => ({
      text: `word${i}`,
      count: 120 - i,
    }));
    fixture.componentInstance.answers = 120;
    expect(() => fixture.detectChanges()).not.toThrow();
  });

  it('turns some words and leaves the biggest level', () => {
    // A cloud of only horizontal words reads as a list. The largest few stay
    // level, because they are what the room reads from the back.
    const calls: string[] = [];
    const fake = {
      clearRect: () => {},
      measureText: (t: string) => ({ width: t.length * 10 }),
      fillText: (t: string) => calls.push(`text:${t}`),
      save: () => calls.push('save'),
      restore: () => calls.push('restore'),
      translate: () => {},
      rotate: (a: number) => calls.push(`rotate:${a.toFixed(2)}`),
      set font(_: string) {},
      set fillStyle(_: string) {},
      set textBaseline(_: string) {},
    };
    spyOn(HTMLCanvasElement.prototype, 'getContext').and.returnValue(
      fake as unknown as CanvasRenderingContext2D,
    );

    const fixture = TestBed.createComponent(WordCloudComponent);
    fixture.componentInstance.words = Array.from({ length: 10 }, (_, i) => ({
      text: `word${i}`,
      count: 10 - i,
    }));
    fixture.detectChanges();

    const rotations = calls.filter((c) => c.startsWith('rotate'));
    expect(rotations.length).toBeGreaterThan(0);
    // A quarter turn, and nothing else.
    expect(new Set(rotations).size).toBe(1);
    // The first word drawn is the most common one, and it is not rotated.
    expect(calls[0]).toBe('text:word0');
  });

  it('shows nothing rather than failing when nobody answered', () => {
    const fixture = TestBed.createComponent(WordCloudComponent);
    fixture.componentInstance.words = [];
    expect(() => fixture.detectChanges()).not.toThrow();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('0 answers');
  });
});
