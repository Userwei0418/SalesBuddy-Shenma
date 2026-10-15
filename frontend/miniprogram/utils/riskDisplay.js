const access = require('./access');
const {riskLight} = require('./statusLight');

function versionValid(item) { return Number.isInteger(item && item.version_no) && item.version_no > 0; }
function dateLabel(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return `${date.getFullYear()}年${date.getMonth()+1}月${date.getDate()}日 ${String(date.getHours()).padStart(2,'0')}:${String(date.getMinutes()).padStart(2,'0')}`;
}
function canWrite(session, item, operation) {
  return Boolean(item && item.status !== 'resolved' && versionValid(item) && item[`can_${operation}`] === true &&
    access.canObject(session, `risk.${operation}`, item, `can_${operation}`) && (operation !== 'decide' || String(item.suggested_action || '').trim()));
}
function normalizeRisk(item, session) {
  const statusGroup = item.status === 'resolved' ? 'resolved' : 'open';
  const severity = {critical:'严重',high:'高',medium:'中',low:'低'}[item.severity_code] || item.severity || '未评级';
  const evidence = Array.isArray(item.evidence) ? item.evidence.map(row => typeof row === 'string' ? row : row.label || row.detail || JSON.stringify(row)) : [];
  const action = Object.prototype.hasOwnProperty.call(item, 'suggested_action') ? item.suggested_action : item.next_action;
  const decision = item.current_decision;
  return {...item, statusGroup, signal:riskLight(item), severity, severityClass:item.severity_code || 'medium',
    statusLabel:item.status === 'accepted' ? '风险已接受，仍未关闭' : statusGroup === 'resolved' ? '已解除' : '待解除',
    customerName:item.customer_name || item.customerName || '关联客户待确认', title:item.title || '客户经营风险',
    owner:item.owner_name || item.owner || '待确认', team:item.team_name || item.team || '',
    customerId:item.customer_id || item.customerId || '', opportunityId:item.opportunity_id || item.opportunityId || '',
    opportunityName:item.opportunity_name || item.opportunityName || '',
    openedLabel:dateLabel(item.opened_at || item.openedAt), resolvedLabel:dateLabel(item.resolved_at || item.resolvedAt),
    resolvedBy:item.resolved_by_name || item.resolvedBy || '负责人', resolutionNote:item.resolution_note || item.resolutionNote || '未填写解除依据',
    evidence:evidence.length ? evidence : ['未登记风险依据'], nextAction:action || '当前未登记处理建议',
    canDecide:canWrite(session,item,'decide'), canResolve:canWrite(session,item,'resolve'),
    decisionLabel:decision ? (decision.decision === 'adopt' ? '已采纳建议' : '暂不采纳建议') : '',
    decisionStale:Boolean(decision && (decision.is_current !== true || decision.risk_version !== item.version_no)),
    decisionTime:decision ? dateLabel(decision.occurred_at) : '',
    historyRows:(item.events || []).map((event,index) => ({id:event.id || String(index),
      label:({suggestion_decided:'建议确认',resolved:'风险解除',detected:'风险识别',reanalyzed:'风险重新分析'})[event.event_type] || '风险更新',
      actor:event.actor_name || '系统', time:dateLabel(event.occurred_at),
      note:event.payload && (event.payload.note || event.payload.resolution_note) || '',
      decision:event.payload && event.payload.decision === 'adopt' ? '已采纳' : event.payload && event.payload.decision === 'defer' ? '暂不采纳' : ''})),
  };
}
module.exports = {normalizeRisk, canWrite, versionValid};
