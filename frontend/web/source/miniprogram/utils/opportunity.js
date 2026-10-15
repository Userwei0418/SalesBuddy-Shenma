const {stages:STAGES,grades:OPPORTUNITY_GRADES} = require('./businessOptions');
const materials = require('./opportunityMaterials');
const extraFields = require('./opportunityExtraFields');
const productLines = require('./opportunityProductLines');
function gradeOfAmount(value) {
  if (value === null || value === undefined || String(value).trim() === '') return null;
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) return null;
  return OPPORTUNITY_GRADES.find(item => number >= item.min && number < item.max) || null;
}
function stageOf(row = {}) {
  return STAGES.find(s => (row.status === 'lost' || row.status === 'won') ? s.status === row.status : s.probability === Number(row.probability)) || STAGES.find(s => s.code === row.stage_code) || {code:row.stage_code||'',label:'阶段待确认',text:'阶段待确认',probability:null,status:row.status||'open'};
}
function quarterNow() { const d = new Date(Date.now() + 8 * 3600000); return { year: d.getUTCFullYear(), quarter: Math.floor(d.getUTCMonth() / 3) + 1 }; }
function wan(value) {
  if (value === null || value === undefined) return '';
  const match = String(value).trim().match(/^(\d+)(?:\.(\d+))?$/);
  if (!match) return '';
  const whole = match[1].replace(/^0+(?=\d)/, '').padStart(5, '0');
  return `${whole.slice(0, -4)}.${whole.slice(-4)}${match[2] || ''}`.replace(/\.?0+$/, '');
}
function amount(value, required = false) {
  if (value === null || value === undefined || String(value).trim() === '') { if (required) throw new Error('请填写 ACV'); return null; }
  if (!/^\d+(\.\d{1,6})?$/.test(String(value).trim())) throw new Error('金额请输入非负数字，万元最多六位小数（精确到分）');
  const [whole, fraction = ''] = String(value).trim().split('.');
  const cents = (whole + fraction.padEnd(6, '0')).replace(/^0+(?=\d)/, '').padStart(3, '0');
  const exact = `${cents.slice(0, -2)}.${cents.slice(-2)}`.replace(/\.?0+$/, '');
  const n = Number(exact);
  if (n > Number.MAX_SAFE_INTEGER || String(n) !== exact) throw new Error('金额数值过大，无法精确保存，请核对金额单位或联系管理员');
  if (!Number.isFinite(n) || n >= 1e16 || (required && n <= 0)) throw new Error('请输入有效金额');
  return n;
}
function formFor(row) {
  const stage = row ? stageOf(row) : null;
  return { ...materials.formFields(row), ...productLines.formFields(row), ...extraFields.formFields(row), fde_members: (row && row.fde_members || []).map(p=>({...p})), fde_member_ids: !row || Array.isArray(row.fde_members) ? (row && row.fde_members || []).map(p=>p.id) : undefined, name: row ? row.name : '', amount: row ? wan(row.amount) : '',
    expected_close_date: row ? row.expected_close_date || '' : '', stageIndex: stage ? STAGES.indexOf(stage) : -1,
    partner_mode: row ? row.sales_channel || (row.partner_name === '直销' ? 'direct' : row.partner_name ? 'partner' : 'unknown') : 'direct',
    partner_id: row ? row.partner_id || null : null,
    partner_name: row ? row.partner_name || '' : '直销', product_line: row ? row.product_line || '' : '',
    quarters: (row && row.quarterly_forecasts || []).map(q => ({ ...q, recognized: wan(q.recognized_amount), collection: wan(q.collection_amount) })) };
}
function formForVisit(row, savedDraft) {
  const form = JSON.parse(JSON.stringify(savedDraft || formFor(row)));
  // The opportunity roster is not evidence of attendance at this visit.
  delete form.fde_members;
  delete form.fde_member_ids;
  form.visit_fde_members = [...new Map((form.visit_fde_members || []).filter(m => m && m.id).map(m => [m.id, m])).values()];
  form.visit_fde_member_ids = form.visit_fde_members.map(m => m.id);
  return form;
}
function forecastRequired(stageIndex) {
  const stage = STAGES[stageIndex];
  return !!stage && stage.probability >= 30;
}
// Sales forecast is the latest entered amount, independent of stage and operations actuals.
function forecastAmount(value) {
  try { const yuan=amount(value);return yuan===null?'—':wan(yuan); } catch (_) { return '—'; }
}
function validateForecasts(form) {
  if (!forecastRequired(form.stageIndex)) return;
  const filled = value => value !== null && value !== undefined && String(value).trim() !== '';
  const started = form.quarters.filter(q => filled(q.recognized) || filled(q.collection));
  const incomplete = started.find(q => !filled(q.recognized) || !filled(q.collection));
  if (!started.length || incomplete) {
    const error = new Error(incomplete
      ? `${incomplete.year} Q${incomplete.quarter}：请填写${!filled(incomplete.recognized) ? '确收' : '回款'}，金额为零请填 0`
      : '30%及以上阶段须至少填写一个季度的回款和确收，金额为零请填 0');
    error.code = 'FORECAST_REQUIRED';
    error.forecastQuarter = incomplete || null;
    throw error;
  }
}
function payload(form, row) {
  if (!form.name.trim()) throw new Error('请填写商机名称');
  const s = STAGES[form.stageIndex]; if (!s) throw new Error('请选择商机阶段');
  if (!form.expected_close_date) throw new Error('请选择预计关单日期');
  validateForecasts(form);
  if (!['direct','partner'].includes(form.partner_mode)) throw new Error('请确认销售渠道：直销或合作伙伴');
  if (form.partner_mode === 'partner' && !form.partner_id) throw new Error('请选择已有合作伙伴，目录中没有时请联系运营');
  return { action: row ? 'update' : 'create', opportunity_id: row ? row.id : null,
    ...(!row&&form.owner_team_id?{owner_team_id:form.owner_team_id}:{}),
    ...(Array.isArray(form.fde_member_ids)&&(row?(!Array.isArray(row.fde_members)||JSON.stringify([...new Set(form.fde_member_ids)].sort())!==JSON.stringify(row.fde_members.map(p=>p.id).sort())):form.fde_member_ids.length>0)?{fde_member_ids:[...new Set(form.fde_member_ids)]}:{}),
    name: form.name.trim(), amount: amount(form.amount, true), expected_close_date: form.expected_close_date,
    probability: s.probability, status: s.status, version_no: row ? row.version_no : undefined,
    sales_channel: form.partner_mode, partner_id: form.partner_mode === 'partner' ? form.partner_id : null,
    ...productLines.payload(form,row), ...materials.payload(form), ...extraFields.payload(form),
    quarterly_forecasts: form.quarters.map(q => ({ year: q.year, quarter: q.quarter,
      recognized_amount: amount(q.recognized), collection_amount: amount(q.collection), collection_confidence:['high','low'].includes(q.collection_confidence)?q.collection_confidence:null })) };
}
function needsConfirmation(p, row) { return p.status !== 'open' && (!row || row.status !== p.status) ? 'close' : row && row.status !== 'open' && p.status === 'open' ? 'reopen' : ''; }
function metricNumber(value) {
  if (value === null || value === undefined || String(value).trim() === '') return null;
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
}
function metricMoney(value) {
  const amountValue = metricNumber(value);
  return amountValue === null ? '未登记' : `¥${amountValue.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`;
}
function performanceMetric(maturity, kind, actual, targetOverride) {
  const source = maturity || {};
  const targets = source.targets || source.sales_targets || {};
  const fallbackTarget = [source[`${kind}_target_amount`], source[`${kind}_target`], targets[`${kind}_amount`], targets[kind]]
    .map(metricNumber).find(value => value !== null);
  const customTarget = metricNumber(targetOverride);
  const target = customTarget !== null ? customTarget : fallbackTarget === undefined ? null : fallbackTarget;
  const completed = metricNumber(actual);
  const completionRate = target && completed !== null ? completed / target * 100 : null;
  const targetToActualRatio = target !== null && completed > 0 ? target / completed : null;
  return { kind, name: kind === 'collection' ? '回款' : '确收',
    target,
    completed,
    targetText: target === null ? '未设置' : metricMoney(target),
    completedText: completed === null ? '未登记' : metricMoney(completed),
    rateText: targetToActualRatio === null ? '目标/完成 待计算' : `目标/完成 ${targetToActualRatio.toFixed(2)}`,
    progress: completionRate === null ? 0 : Math.max(0, Math.min(100, Math.round(completionRate))) };
}
function customerRetention(rows, now = new Date()) {
  const year = now.getFullYear(); const lastYear = year - 1; const byCustomer = new Map();
  (rows || []).forEach(row => {
    const customerId = String(row.customer_id || '');
    const rowYear = Number(row.year || String(row.occurred_on || row.occurredAt || '').slice(0, 4));
    const value = metricNumber(row.recognized_amount !== undefined ? row.recognized_amount : row.amount);
    if (!customerId || ![year, lastYear].includes(rowYear) || value === null) return;
    if (!byCustomer.has(customerId)) byCustomer.set(customerId, { current: 0, previous: 0 });
    byCustomer.get(customerId)[rowYear === year ? 'current' : 'previous'] += value;
  });
  const cohort = [...byCustomer.values()].filter(item => item.previous > 0);
  const previous = cohort.reduce((sum, item) => sum + item.previous, 0);
  const current = cohort.reduce((sum, item) => sum + item.current, 0);
  return { rate: previous > 0 ? current / previous * 100 : null, current, previous, customerCount: cohort.length };
}
function averageVisitScore(visits) {
  const scores = (visits || []).map(item => metricNumber(item.score !== undefined ? item.score : item.follow_up_score)).filter(value => value !== null);
  return scores.length ? scores.reduce((sum, value) => sum + value, 0) / scores.length : null;
}
function averageCustomerScore(customers) {
  const scores = (customers || []).map(item => {
    const potential = metricNumber(item.potential_score !== undefined ? item.potential_score : item.potential);
    const relationship = metricNumber(item.relationship_score !== undefined ? item.relationship_score : item.relationship);
    return potential === null || relationship === null ? null : (potential + relationship) / 2;
  }).filter(value => value !== null);
  return scores.length ? scores.reduce((sum, value) => sum + value, 0) / scores.length : null;
}
function opportunityABShare(opportunities) {
  const rows = opportunities || [];
  if (!rows.length) return null;
  return rows.filter(item => { const grade = gradeOfAmount(item.amount); return grade && ['A', 'B'].includes(grade.code); }).length / rows.length * 100;
}
module.exports = { STAGES, OPPORTUNITY_GRADES, gradeOfAmount, stageOf, quarterNow, wan, amount, formFor, formForVisit, forecastRequired, forecastAmount, payload, needsConfirmation,
  averageCustomerScore, averageVisitScore, customerRetention, money: metricMoney, opportunityABShare, performanceMetric };
