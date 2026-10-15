/* DOM geometry only: keep shared column headings below the current sticky tools.
   No business data, page handlers, navigation or query parameters are modified. */
(() => {
  'use strict';
  const root = document.querySelector('#page-root');
  if (!root) return;
  let currentTable = null, currentTools = null, observer = null, scheduled = false;
  function scrollHost(element) {
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      if (/^(auto|scroll)$/.test(getComputedStyle(parent).overflowY)) return parent;
    }
    return null;
  }
  function measure() {
    if (!currentTable?.isConnected) return;
    const host = scrollHost(currentTable), style = currentTools && getComputedStyle(currentTools);
    const height = host?.contains(currentTools) && style.position === 'sticky'
      ? Math.max(0, currentTools.getBoundingClientRect().height + (parseFloat(style.top) || 0)) : 0;
    const value = `${Math.ceil(height)}px`;
    if (currentTable.style.getPropertyValue('--web-review-tools-height') !== value) currentTable.style.setProperty('--web-review-tools-height', value);
  }
  function reconcile() {
    scheduled = false;
    const table = root.querySelector('.web-review-opportunity-table');
    const workspace = table?.closest('.web-opportunity-results,.web-review-opportunities-page');
    const tools = workspace?.querySelector('.workbench-opportunity-tools,.filter-card') || null;
    if (table !== currentTable || tools !== currentTools) {
      observer?.disconnect(); observer = null;
      currentTable?.style.removeProperty('--web-review-tools-height');
      currentTable = table; currentTools = tools;
      if (table) {
        observer = new ResizeObserver(measure);
        observer.observe(table);
        if (tools) observer.observe(tools);
        if (workspace) observer.observe(workspace);
      }
    }
    measure();
  }
  function schedule() {if (!scheduled) {scheduled = true; requestAnimationFrame(reconcile);}}
  // The runtime emits navigation before asynchronous list reads finish. Observe
  // child-list changes to attach when results arrive, and drop the old observer
  // when an empty/error state or another route replaces the table.
  new MutationObserver(schedule).observe(root, {childList: true, subtree: true});
  window.addEventListener('sales:navigation', schedule);
  window.addEventListener('resize', schedule);
  schedule();
})();
