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

  it('shows nothing rather than failing when nobody answered', () => {
    const fixture = TestBed.createComponent(WordCloudComponent);
    fixture.componentInstance.words = [];
    expect(() => fixture.detectChanges()).not.toThrow();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('0 answers');
  });
});
