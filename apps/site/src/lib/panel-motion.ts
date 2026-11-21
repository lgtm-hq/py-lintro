/** Briefly introduce changed content; reduced motion always takes precedence. */
export function animatePanel(element: HTMLElement | null): void {
  if (!element?.animate || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  element.getAnimations().forEach((animation) => animation.cancel());
  element.animate(
    [
      { opacity: 0.3, transform: 'translateY(7px)' },
      { opacity: 1, transform: 'translateY(0)' },
    ],
    { duration: 260, easing: 'cubic-bezier(.22,1,.36,1)' }
  );
}
