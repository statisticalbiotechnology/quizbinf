import { Directive, ElementRef, Input, OnChanges, SecurityContext, inject } from '@angular/core';
import { DomSanitizer } from '@angular/platform-browser';

/**
 * Show server-rendered Markdown, formulas included.
 *
 * Replaces `[innerHTML]` for question and choice text. It keeps what that
 * binding gave: the HTML is already sanitised on the server, and Angular's
 * sanitiser runs over it again here, because the result is shown to every
 * student in the room. Then it draws the formulas the server marked
 * (`span.math.inline`, `div.math.block`, holding TeX as text) with KaTeX.
 *
 * KaTeX is loaded only once a formula is on the page, so a lecture without
 * one costs nothing. It is given the TeX as text and builds its own markup,
 * with its defaults: `trust: false`, so no `\href` or `\includegraphics`.
 */
@Directive({ selector: '[mdHtml]', standalone: true })
export class MarkdownHtmlDirective implements OnChanges {
  @Input('mdHtml') html: string | null | undefined = '';

  private host = inject<ElementRef<HTMLElement>>(ElementRef);
  private sanitizer = inject(DomSanitizer);

  ngOnChanges(): void {
    const el = this.host.nativeElement;
    el.innerHTML = this.sanitizer.sanitize(SecurityContext.HTML, this.html ?? '') ?? '';
    const formulas = Array.from(el.querySelectorAll<HTMLElement>('.math'));
    if (formulas.length) void drawFormulas(formulas);
  }
}

let katexModule: Promise<typeof import('katex')> | null = null;

async function drawFormulas(nodes: HTMLElement[]): Promise<void> {
  katexModule ??= import('katex');
  const katex = (await katexModule).default;
  for (const node of nodes) {
    const tex = node.textContent ?? '';
    // A typo in a formula shows the error in red in place of the formula,
    // rather than leaving the teacher to guess why it is missing.
    katex.render(tex, node, {
      displayMode: node.classList.contains('block'),
      throwOnError: false,
    });
  }
}
