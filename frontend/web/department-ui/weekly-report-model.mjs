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
  const end = kind === 'previous' ? day - ((new Date(day + 8 * 3600000).getUTCDay() + 6) % 7 + 1) * DAY : day;
  return {start: dateInShanghai(new Date(end - 6 * DAY).toISOString()), end: dateInShanghai(new Date(end).toISOString())};
}
export const uploadDate = row => dateInShanghai(row.created_at || row.created_date);
export function periodRecords(records, period) {
  return records.filter(row => {const date = uploadDate(row); return date && date >= period.start && date <= period.end;})
    .sort((a, b) => String(b.created_at || b.created_date).localeCompare(String(a.created_at || a.created_date)) || String(a.id).localeCompare(String(b.id)));
}
const clean = value => typeof value === 'string' ? value.replace(/\r\n?/g, '\n').replace(/[\t ]+/g, ' ').trim() : '';
const sentence = value => {const text = clean(value); return text && !/[。！？.!?]$/.test(text) ? `${text}。` : text;};
export function composeReport(records, period, author) {
  if (!records.length) throw Error('请至少选择一条更新记录');
  const customers = new Set(records.map(r => r.customer_id).filter(Boolean));
  const opportunities = new Set(records.map(r => r.opportunity_id).filter(Boolean));
  const lines = ['本周概览', `根据 ${period.start} 至 ${period.end} 上传的 ${records.length} 条记录，本周涉及 ${customers.size} 个客户、${opportunities.size} 个商机。`, '', '客户与商机进展'];
  records.forEach((r, i) => lines.push(`[${i + 1}] ${clean(r.customer_name) || '未关联客户'}${r.opportunity_name ? ` · ${clean(r.opportunity_name)}` : ''}`, `${uploadDate(r)}｜${sentence(r.follow_up_record) || '沟通内容未填写，待补充。'}`, ''));
  lines.push('后续行动（按原记录整理）');
  const actions = records.flatMap((r, i) => clean(r.next_action) ? [`• ${clean(r.customer_name) || '未关联客户'}：${sentence(r.next_action)} [${i + 1}]`] : []);
  lines.push(...(actions.length ? actions : ['原记录未填写下一步行动，请补充下周计划。']), '', '待补充与协同');
  const missing = records.flatMap((r, i) => !clean(r.follow_up_record) || !clean(r.next_action) ? [`• [${i + 1}] ${clean(r.customer_name) || '未关联客户'}：请补充${[!clean(r.follow_up_record) && '沟通内容', !clean(r.next_action) && '下一步行动'].filter(Boolean).join('、')}。`] : []);
  lines.push(...(missing.length ? missing : ['请核对以上进展和行动，并补充需要团队协调的事项。']));
  return {title: `${author || '我的'}周报`, text: lines.join('\n'), sources: records.map(r => ({id: r.id, customer: r.customer_name, date: uploadDate(r)})), period};
}
