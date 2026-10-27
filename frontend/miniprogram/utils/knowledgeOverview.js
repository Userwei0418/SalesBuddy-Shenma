// /knowledge/stats is the complete current-authorized SalesBuddy material
// count. The native library route remains the separate customer-facing
// catalogue; its pagination total must not replace this home metric.
const INTERNAL_SOURCE = 'salesbuddy.internal_materials';
function normalizeKnowledgeOverview(stats) {
  const metric = stats && stats.metrics && stats.metrics.total;
  const available = stats && stats.source === INTERNAL_SOURCE && metric && metric.source === INTERNAL_SOURCE
    && metric.status === 'available' && Number.isSafeInteger(metric.count) && metric.count >= 0;
  if (available) return {status:'available',count:metric.count,value:String(metric.count),source:INTERNAL_SOURCE,
    sourceLabel:'系统资料',observedAt:metric.observed_at||stats.observed_at||'',error:''};
  return {status:metric && metric.status === 'unavailable'?'unavailable':'unconfigured',count:null,value:'—',source:'',
    sourceLabel:'系统资料',observedAt:'',error:''};
}
function knowledgeOverviewError(error='') {return {...normalizeKnowledgeOverview(null),status:'unavailable',error:String(error||'')};}
module.exports={normalizeKnowledgeOverview,knowledgeOverviewError};
