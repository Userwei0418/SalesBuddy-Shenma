// Display only: status and counts come from the authorized server query, never this page's rows.
export const riskClosed = risk => risk?.status === 'resolved';
export const riskStatus = risk => riskClosed(risk) ? '已解除' : risk?.status === 'accepted' ? '已接受 · 未关闭' : '未关闭';
export const riskCount = value => typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null;
export const riskVersioned = risk => Number.isInteger(risk?.version_no) && risk.version_no >= 1;
export const decisionLabel = value => value === 'adopt' ? '已采纳' : value === 'defer' ? '暂不采纳' : '尚未确认';
export const riskSuggestion = risk => {
  // An explicitly cleared authoritative suggestion must not revive an old snapshot/fallback.
  if (Object.hasOwn(risk || {}, 'suggested_action')) return String(risk.suggested_action || '').trim();
  return String(risk?.next_action || '').trim();
};
const EVIDENCE_KEYS = ['label','detail','description','reason','text','summary','message','evidence','facts','items'];
export function evidenceText(value) {
  if (typeof value === 'string') {
    const text = value.trim();
    if (/^[\[{]/.test(text)) { try { return evidenceText(JSON.parse(text)); } catch (_) { return '依据格式待整理'; } }
    return text;
  }
  if (Array.isArray(value)) return value.map(evidenceText).filter(Boolean).join('；');
  if (value && typeof value === 'object') {
    for (const key of EVIDENCE_KEYS) { const text = evidenceText(value[key]); if (text) return text; }
  }
  return '';
}
export function riskDate(value) {
  const date = value && new Date(value);
  return date && Number.isFinite(date.valueOf()) ? date.toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false}) : '时间未登记';
}
export function riskEvent(event) {
  const payload = event.payload || {};
  const decision = ['adopt','defer'].includes(payload.decision);
  return {
    title: decision ? `建议${decisionLabel(payload.decision)}` : ({resolved:'解除风险',opened:'识别风险',reopened:'重新打开风险',accepted:'接受风险（仍未关闭）'})[event.event_type] || '风险记录更新',
    note: decision ? payload.note : event.event_type === 'resolved' ? payload.resolution_note : '',
    actor: event.actor_name || '系统', date: riskDate(event.occurred_at),
    suggestion: decision ? payload.suggested_action : '',
  };
}
