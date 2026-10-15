// Presentation only: retain the score and rule recorded in each saved review.
export function historyScore(value) {
  if (!['number', 'string'].includes(typeof value) || (typeof value === 'string' && !value.trim())) return null;
  const score = Number(value);
  return Number.isFinite(score) && score >= 0 && score <= 100 ? score : null;
}

function calendarDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const time = Date.parse(value + 'T00:00:00Z');
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value ? time : null;
}

export const fdeUnit = code => code === 'record_quality' ? '分' : ['plan_coverage','project_coverage','customer_coverage','task_completion','task_timeliness'].includes(code) ? '%' : '';
export function basisLabel(basis) {
  if (basis?.snapshot_status !== 'frozen') return '历史口径未知';
  if (basis.framework_version == null) return '事实指标规则已保存';
  const policy = basis.score_policy;
  return `能力框架 V${basis.framework_version}${policy ? ` · ${policy.source === 'baseline' ? '默认评分规则' : `公司评分 V${policy.version}`}` : ''}`;
}
export function historyView(history, code = 'overall', factRatios = false) {
  const rows = history.map((item, index) => {
    const rawDate = item.review_date || item.date || '';
    const date = typeof rawDate === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(rawDate) && Number.isFinite(Date.parse(rawDate))
      ? new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(rawDate)) : rawDate;
    const basis = item.score_basis;
    const rule = basis?.snapshot_status === 'frozen' && typeof basis.basis_id === 'string' && basis.basis_id ? basis.basis_id : null;
    const overall = factRatios ? item.overall_score : item.score_summary ? item.score_summary.value : basis?.snapshot_status === 'frozen' ? null : item.overall_score;
    const score = historyScore(code === 'overall' ? overall : item.dimension_scores?.[code]?.score);
    return {key: item.id || item.run_id || String(index), reviewId:item.id || item.run_id || null,
      date, reviewedAt:item.reviewed_at || '', generation:item.generation_no, time: calendarDate(date), score,
      rule, ruleLabel: basisLabel(basis)};
  });
  const dated = rows.filter(row => row.time !== null).sort((a, b) => a.time - b.time);
  const min = dated[0]?.time ?? 0, max = dated.at(-1)?.time ?? min;
  const points = dated.map(row => ({...row, x: max === min ? 0.5 : (row.time - min) / (max - min)}));
  // A missing assessment or changed/unknown rule cannot establish a continuous trend.
  const segments = [];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i];
    if (a.score !== null && b.score !== null && a.time < b.time && a.rule !== null && a.rule === b.rule) segments.push([a, b]);
  }
  return {rows, points: points.filter(row => row.score !== null), segments,
    firstDate: dated[0]?.date || '', lastDate: dated.at(-1)?.date || '',
    unknownRules: rows.some(row => row.rule === null),
    multipleRules: new Set(rows.map(row => row.rule).filter(rule => rule !== null)).size > 1};
}
