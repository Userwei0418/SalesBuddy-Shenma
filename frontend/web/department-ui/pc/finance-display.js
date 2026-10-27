// Display only. Amounts and the chosen source come from the effective API view.
export function actualPeriodLabel(row) {
  if (row.period_precision === 'quarter' || !row.occurred_on) {
    return Number.isInteger(row.year) && [1, 2, 3, 4].includes(row.quarter) ? `${row.year} Q${row.quarter}` : '期间未标注';
  }
  return row.occurred_on;
}

export function actualSourceLabel(row) {
  const source = row.sourceLabel || {operations: '实绩登记', entries: '逐笔登记', historical: '历史导入'}[row.source] || '来源未标注';
  const state = row.statusLabel || {withdrawn: '已撤回', partial: '部分登记', missing: '未登记', conflict: '待核对'}[row.status] || (row.tax_unknown ? '税口径待核对' : row.complete === false ? '待核对' : '');
  return [source, state].filter(Boolean).join(' · ');
}
