const { dateValue } = require('./visitDates');
const { activeWorkspaceId, displayText } = require('./demoDisplay');
const ADVICE_TABS={overview:'客户概览',tasks:'待办事项',visits:'跟进记录',opportunity:'商机进展'};
function businessTimeLabel(value) {
  if (!value) return '';
  const text = String(value).trim();
  // Date-only and legacy local timestamps retain their recorded precision.
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return dateValue(text);
  if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(text)) return text.replace('T', ' ').slice(0, 16);
  const timestamp = new Date(text).getTime();
  if (!Number.isFinite(timestamp)) return '';
  // Match the existing visit/date business convention, independent of device timezone.
  const china = new Date(timestamp + 8 * 3600000);
  const pad = number => String(number).padStart(2, '0');
  return `${dateValue(text)} ${pad(china.getUTCHours())}:${pad(china.getUTCMinutes())}`;
}
// Presentation only. The server owns facts, prompt versions, validation and cache identity.
function adviceResult(value) {
  if(!value || value.status!=='succeeded')throw Error(value && value.status==='superseded'?'资料已变化，请更新建议':'AI尚未完成分析，请稍后重试');
  if(!value.id || !value.summary || !Array.isArray(value.suggestions))throw Error('AI未返回有效建议');
  const workspaceId = activeWorkspaceId();
  return {status:'ready',id:value.id,summary:displayText(value.summary,workspaceId),
    rows:value.suggestions.map(item=>({...item,title:displayText(item.title,workspaceId),detail:displayText(`${item.evidence}\n建议行动：${item.action}`,workspaceId)})),
    coverage:value.coverage || {},updatedAt:businessTimeLabel(value.completed_at)};
}
module.exports={ADVICE_TABS,adviceResult,businessTimeLabel};
