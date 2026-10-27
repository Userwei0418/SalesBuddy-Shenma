import {displayItem} from './content.js';
import {materialMetricViews} from './presentation.js';
import {sourceDisplayText} from './source-display.js';
// Saved prose/metrics and editor/history/export inputs are never rewritten.
export function readingItem(item,metrics) {
  if(['manual','user_supplement'].includes(item.kind))return displayItem(item,metrics);
  const views=materialMetricViews(metrics),converted=metrics.map((metric,index)=>({...metric,
    display_value:views[index].originalAmount?`${views[index].display} 万元`:metric.display_value}));
  const amounts=new Map(metrics.filter((metric,index)=>views[index].originalAmount).map(metric=>[metric.metric_id,metric]));
  // Server metric_fragment appends the exact unit; the older basic fallback
  // wraps it in full-width parentheses. Consume only that controlled suffix.
  const text=item.text.replace(/\{\{metric:([a-z][a-z0-9_.-]{0,95})\}\}(?:[ \t]*(?:（(元|原币)）|(元|原币)(?=[\s。！？!?；;，,、:]|$)))?/g,
    (whole,id,wrapped,plain)=>amounts.has(id) && (!wrapped && !plain || amounts.get(id).unit===(wrapped || plain))?`{{metric:${id}}}`:whole);
  const shown=displayItem({...item,text},converted);
  const refs=new Set([...item.text.matchAll(/\{\{metric:([a-z][a-z0-9_.-]{0,95})\}\}/g)].map(match=>match[1]));
  return {...shown,text:sourceDisplayText(shown.text,item.source_refs || []),
    originalAmounts:views.filter(view=>refs.has(view.id) && view.originalAmount).map(view=>({label:view.label,value:view.originalAmount}))};
}
