import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';

import { NameDrawComponent } from './name-draw.component';

/**
 * The draw is theatre over a decision the server has already made. What these
 * pin is that the theatre cannot change the outcome: whatever the reel shows
 * on the way, each slot comes to rest on the name the server sent, in order,
 * one at a time.
 */
describe('NameDrawComponent', () => {
  let fixture: ComponentFixture<NameDrawComponent>;
  let draw: NameDrawComponent;

  const REEL = ['Anna', 'Bo', 'Cecilia', 'David', 'Eva', 'Frida'];

  beforeEach(() => {
    TestBed.configureTestingModule({ imports: [NameDrawComponent] });
    fixture = TestBed.createComponent(NameDrawComponent);
    draw = fixture.componentInstance;
  });

  /** Set the inputs the way a binding would, so ngOnChanges fires each time. */
  function start(names: string[], reel = REEL): void {
    fixture.componentRef.setInput('reel', reel);
    fixture.componentRef.setInput('names', names);
    fixture.detectChanges();
  }

  it('settles each slot on the drawn name, one at a time', fakeAsync(() => {
    start(['Cecilia', 'Frida']);

    // Rolling: both slots are showing something, neither has landed.
    tick(300);
    expect(draw.slots().length).toBe(2);
    expect(draw.slots().every((s) => !s.fixed)).toBeTrue();

    // The first lands while the second is still going — the staggered stop is
    // the whole effect.
    tick(1600);
    expect(draw.slots()[0].fixed).toBeTrue();
    expect(draw.slots()[0].text).toBe('Cecilia');
    expect(draw.slots()[1].fixed).toBeFalse();

    tick(1500);
    expect(draw.slots()[1].fixed).toBeTrue();
    expect(draw.slots()[1].text).toBe('Frida');
    expect(draw.settled()).toBeTrue();
    expect(draw.slots().map((s) => s.text)).toEqual(['Cecilia', 'Frida']);
  }));

  it('rolls through names before it settles', fakeAsync(() => {
    start(['Cecilia', 'Frida']);

    const seen = new Set<string>();
    for (let i = 0; i < 12; i++) {
      tick(100);
      seen.add(draw.slots()[0].text);
    }
    expect(seen.size).toBeGreaterThan(1);

    tick(5000);
  }));

  it('never rolls a name that is not on the reel', fakeAsync(() => {
    // The reel is who joined the lecture; nothing else may be projected.
    start(['Cecilia', 'Frida']);
    for (let i = 0; i < 20; i++) {
      tick(80);
      for (const slot of draw.slots()) {
        if (!slot.fixed) expect(REEL).toContain(slot.text);
      }
    }
    tick(5000);
  }));

  it('shows the names outright when there is nothing to roll through', fakeAsync(() => {
    // A class where only the drawn students are on the reel: a spin between
    // two names is not suspense, it is a flicker.
    start(['Anna'], ['Anna']);

    expect(draw.slots()).toEqual([{ text: 'Anna', fixed: true }]);
    expect(draw.settled()).toBeTrue();
  }));

  it('shows nothing when nobody was drawn', fakeAsync(() => {
    start([], REEL);
    expect(draw.slots()).toEqual([]);
  }));

  it('starts over when the teacher draws again', fakeAsync(() => {
    start(['Cecilia', 'Frida']);
    tick(6000);
    expect(draw.settled()).toBeTrue();

    start(['Anna', 'Bo']);
    expect(draw.settled()).toBeFalse();
    expect(draw.slots().every((s) => !s.fixed)).toBeTrue();

    tick(6000);
    expect(draw.slots().map((s) => s.text)).toEqual(['Anna', 'Bo']);
  }));

  it('drops its timers when the view is closed mid-spin', fakeAsync(() => {
    start(['Cecilia', 'Frida']);
    tick(200);
    fixture.destroy();
    // A pending timeout here would fail fakeAsync's queue check on exit.
    tick(6000);
    expect(draw.slots()[0].fixed).toBeFalse();
  }));
});
