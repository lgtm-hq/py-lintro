/** Copy raw text or select it and retain manual recovery instructions. */
export async function copyWithRecovery(
  source: HTMLElement,
  status: HTMLElement | null
): Promise<void> {
  try {
    if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
    await navigator.clipboard.writeText(source.textContent ?? '');
    if (status) status.textContent = 'Copied to clipboard.';
  } catch {
    const range = document.createRange();
    range.selectNodeContents(source);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
    if (!source.hasAttribute('tabindex')) source.tabIndex = -1;
    source.focus({ preventScroll: true });
    if (status)
      status.textContent =
        'Clipboard unavailable. Text selected; copy with your keyboard or device menu.';
  }
}
