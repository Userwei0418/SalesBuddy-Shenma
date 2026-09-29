// A factual, deterministic demo composer; no generated promises, inferred risks or AI claims.
const DAY = 86400000;
export const dateInShanghai = value => {
  if (!value) return '';
  const stamp = Date.parse(value);
  return Number.isFinite(stamp) ? new Date(stamp + 8 * 3600000).toISOString().slice(0, 10) : '';
};
export function reportPeriod(kind = 'recent', now = Date.now()) {
  const today = dateInShanghai(new Date(now).toISOString());
  const day = Date.parse(`${today}T00:00:00+08:00`);
  const monday = day - ((new Date(day + 8 * 3600000).getUTCDay() + 6) % 7) * DAY;
  const start = monday - (kind === 'previous' ? 7 * DAY : 0), end = start + 6 * DAY;
  const cutoff = kind === 'previous' ? end + DAY - 1 : now;
  const inputEnd = kind === 'previous' ? end : day;
  const iso = value => dateInShanghai(new Date(value).toISOString());
  return {start:iso(start),end:iso(end),inputStart:iso(inputEnd-13*DAY),inputEnd:iso(inputEnd),cutoffAt:new Date(cutoff).toISOString()};
}
export const uploadDate = row => dateInShanghai(row.created_at || row.created_date);
export function periodRecords(records, period) {
  return records.filter(row => {const date = uploadDate(row); const stamp=Date.parse(row.created_at || row.created_date); return date && date >= (period.inputStart || period.start) && date <= (period.inputEnd || period.end) && (!period.cutoffAt || stamp <= Date.parse(period.cutoffAt));})
    .sort((a, b) => String(b.created_at || b.created_date).localeCompare(String(a.created_at || a.created_date)) || String(a.id).localeCompare(String(b.id)));
}
const clean = value => typeof value === 'string' ? value.replace(/\r\n?/g, '\n').replace(/[\t ]+/g, ' ').trim() : '';
const sentence = value => {const text = clean(value); return text && !/[。！？.!?]$/.test(text) ? `${text}。` : text;};
export function composeReport(records, period, author) {
  if (!records.length) throw Error('请至少选择一条更新记录');
  const customers = new Set(records.map(r => r.customer_id).filter(Boolean));
  const opportunities = new Set(records.map(r => r.opportunity_id).filter(Boolean));
  const currentCount = records.filter(r => uploadDate(r) >= period.start && uploadDate(r) <= period.end).length;
  const lines = ['本周概览', `报告周期：${period.start} 至 ${period.end}。`, `素材范围：${period.inputStart || period.start} 至 ${period.inputEnd || period.end}，共 ${records.length} 条记录，涉及 ${customers.size} 个客户、${opportunities.size} 个商机。其中本周上传 ${currentCount} 条，其余为前期背景。`, '', '客户与商机进展（按上传时间区分）'];
  records.forEach((r, i) => lines.push(`[${i + 1}] ${clean(r.customer_name) || '未关联客户'}${r.opportunity_name ? ` · ${clean(r.opportunity_name)}` : ''}`, `${uploadDate(r) >= period.start ? '本周上传' : '前期背景'} · ${uploadDate(r)}｜${sentence(r.follow_up_record) || '沟通内容未填写，待补充。'}`, ''));
  lines.push('后续行动（按原记录整理）');
  const actions = records.flatMap((r, i) => clean(r.next_action) ? [`• ${clean(r.customer_name) || '未关联客户'}：${sentence(r.next_action)} [${i + 1}]`] : []);
  lines.push(...(actions.length ? actions : ['原记录未填写下一步行动，请补充下周计划。']), '', '待补充与协同');
  const missing = records.flatMap((r, i) => !clean(r.follow_up_record) || !clean(r.next_action) ? [`• [${i + 1}] ${clean(r.customer_name) || '未关联客户'}：请补充${[!clean(r.follow_up_record) && '沟通内容', !clean(r.next_action) && '下一步行动'].filter(Boolean).join('、')}。`] : []);
  lines.push(...(missing.length ? missing : ['请核对以上进展和行动，并补充需要团队协调的事项。']));
  return {title: `${author || '我的'}周报`, text: lines.join('\n'), sources: records.map(r => ({id: r.id, customer: r.customer_name, date: uploadDate(r)})), period};
}

// Publication is one explicit action per report; recovery is an operator action.
export function feishuPublicationState(receipt) {
  const status = receipt?.status || (receipt?.event_id ? 'pending' : 'not_published');
  const states = {
    not_published: {type:'info',label:'尚未推送飞书',description:'核对正文后点击确认，即可写入周报表并推送到大群。'},
    pending: {type:'info',label:'飞书推送处理中',description:'已保存本次确认的正文，正在写入多维表格并推送群聊。可以离开页面，稍后查看结果。'},
    sent: {type:'success',label:'周报已推送至飞书群聊',description:'多维表格记录本次确认的正文。后续编辑不会改动已推送内容。'},
    unknown: {type:'warning',label:'飞书推送结果待核对',description:'请联系管理员先核对群消息，再在运维后台恢复。系统不会自动重复发送；处理后可刷新推送状态。'},
    failed: {type:'error',label:'飞书推送未完成',description:'请联系管理员在运维后台核对并恢复本次推送；处理后可刷新推送状态，无需再次提交周报。'},
  };
  return {...(states[status] || states.pending),status,
    canPublish:status==='not_published',needsPolling:!states[status]||status==='pending'};
}
