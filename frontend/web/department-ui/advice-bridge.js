// Keep the original mini-runtime component as the sole owner of advice mutations.
export function adviceEntries(advice, enabled = true) {
  if (!enabled || !advice?.id || advice.checking || advice.needsCheck) return [];
  return (advice.suggestions || advice.rows || []).filter(row => row.id).map(suggestion => ({
    selector: 'advice-actions', analysisId: advice.id, suggestionId: suggestion.id,
    props: {analysisId: advice.id, suggestion},
  }));
}

export function resolveComponent(page, entry) {
  const parent = entry.parent ? page.selectComponent(entry.parent) : page;
  if (!parent || parent._destroyed) return null;
  if (entry.suggestionId) return (parent.selectAllComponents(entry.selector) || []).find(instance =>
    !instance._destroyed && instance.data.analysisId === entry.analysisId && instance.data.suggestion?.id === entry.suggestionId) || null;
  return parent.selectComponent(entry.selector);
}

export function currentAdvice(page) {
  const data = page.data || {};
  if (page.route === 'pages/visit-confirm/index') return data.archived && data.showAdvice ? data.advice : null;
  if (page.route === 'pages/visit-detail/index') return data.visitAdvice;
  if (page.route === 'pages/customer-assets/index') return data.canOpportunityAdvice && data.adviceExpanded?.[data.opportunityTab] ? data.opportunityAdvice?.[data.opportunityTab] : null;
  return null;
}

export function invokeAdvice(page, advice, suggestion, invokeOn, action, payload) {
  const live = currentAdvice(page);
  const row = (live?.suggestions || live?.rows || []).find(item => item.id === suggestion.id);
  if (!live || live.id !== advice.id || !row || live.checking || live.needsCheck) return;
  if (action !== 'openTask' && (live.stale || row.decision !== 'pending' || row.version_no !== suggestion.version_no)) return;
  const instance = resolveComponent(page, {selector: 'advice-actions', analysisId: advice.id, suggestionId: suggestion.id});
  if (instance && !instance.data.saving) return invokeOn(instance, action, payload);
}
