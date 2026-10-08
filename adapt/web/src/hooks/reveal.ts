import { useEffect, type RefObject } from 'react';

// Blocks that rise into view as the page scrolls. Nested matches inherit their ancestor's motion.
const BLOCKS = [
  '.page-heading',
  '.panel',
  '.metrics',
  '.brief',
  '.command-brief',
  '.command-report-context',
  '.command-loop',
  '.workbench-stats',
  '.report-tabs',
  '.section-nav',
  '.proposal-context',
  '.lab-intro',
].join(',');
const STAGGER_MS = 70;
const MAX_DELAY_MS = 350;

/**
 * Scroll reveal: each block starts 16px low and transparent, then settles once it enters the
 * viewport. State lives in `data-reveal` so React re-renders never reset it. Skipped entirely when
 * the visitor prefers reduced motion or the browser lacks IntersectionObserver.
 */
export function useReveal(root: RefObject<HTMLElement | null>, key: string) {
  useEffect(() => {
    const container = root.current;
    if (
      !container ||
      typeof IntersectionObserver === 'undefined' ||
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    )
      return;
    const observer = new IntersectionObserver(
      (entries) => {
        const entering = entries.filter((e) => e.isIntersecting).map((e) => e.target);
        entering.forEach((el, i) => {
          const node = el as HTMLElement;
          node.style.transitionDelay = `${Math.min(i * STAGGER_MS, MAX_DELAY_MS)}ms`;
          node.dataset.reveal = 'in';
          observer.unobserve(node);
          node.addEventListener(
            'transitionend',
            () => {
              node.style.transitionDelay = '';
            },
            { once: true },
          );
        });
      },
      { rootMargin: '0px 0px -6% 0px', threshold: 0.04 },
    );
    const scan = () => {
      container.querySelectorAll<HTMLElement>(BLOCKS).forEach((el) => {
        if (el.dataset.reveal) return;
        if (el.parentElement?.closest('[data-reveal], dialog, [popover]')) return;
        el.dataset.reveal = 'pending';
        observer.observe(el);
      });
    };
    scan();
    const mutations = new MutationObserver(scan);
    mutations.observe(container, { childList: true, subtree: true });
    return () => {
      mutations.disconnect();
      observer.disconnect();
    };
  }, [root, key]);
}
