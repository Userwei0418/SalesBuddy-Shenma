/* UI shortcuts only: no navigation, saving or business writes. */
(() => {
  'use strict';
  const trigger = document.getElementById('shortcut-help-button');
  const dialog = document.getElementById('shortcut-help-dialog');
  if (!trigger || !dialog) return;
  let returnFocus;
  const visible = element => element && !element.closest('[hidden], [inert]') && element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden';
  function openHelp() {
    returnFocus = document.activeElement;
    dialog.showModal();
    dialog.querySelector('button').focus();
  }
  trigger.addEventListener('click', openHelp);
  dialog.querySelector('button').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { if (visible(returnFocus)) returnFocus.focus(); });
  window.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.isComposing || event.keyCode === 229 || event.repeat || event.altKey || event.ctrlKey || event.metaKey) return;
    const editing = event.target.closest?.('input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="combobox"], [role="textbox"]');
    if (editing || document.body.classList.contains('web-login')) return;
    // Leave modal keyboard behavior and focus traps to the existing components.
    if ([...document.querySelectorAll('dialog[open], [aria-modal="true"], .wx-modal-mask')].some(visible)) return;
    if (event.key === '?') { event.preventDefault(); openHelp(); return; }
    if (event.key !== '/') return;
    const candidates = [...document.querySelectorAll('#page-root input:not(:disabled):not([readonly])')];
    const search = candidates.find(input => visible(input) && (input.type === 'search' || /搜索/.test(input.getAttribute('aria-label') || input.placeholder || '')));
    if (search) { event.preventDefault(); search.focus(); }
  });
})();
