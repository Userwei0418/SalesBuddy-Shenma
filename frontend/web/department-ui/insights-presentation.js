// Presentation only: amounts, membership and periods are calculated by the shared page.
export function insightContext(data) {
  return JSON.stringify([data.viewMode, data.selectedMemberId, data.memberSelected,
    data.selectedTeamGroups, data.teamPickerSelected, data.selectedQuarterKeys,
    data.selectedQuarter, data.sourceDate]);
}

export function insightCell(value) {
  if (value === null || value === undefined || value === '') return '未登记';
  if (typeof value === 'number' && !Number.isFinite(value)) return '未登记';
  // Shared financialDisplay supplies the final unit, rounding and missing-state text.
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '—';
}

export function insightDefinitions(data) {
  return (data.kpis || []).map(item => ({
    key: item.key || item.label, title: item.label,
    definition: data.metricDetails?.[item.key]?.definition || item.definition || item.sub || '统计口径待补充',
    periodLabel: data.metricDetails?.[item.key]?.periodLabel || item.periodLabel || data.selectedQuarter?.label,
  }));
}
// Unit conversion for display only. Six decimal places in 万元 preserve cents in 元.
export const insightWan = value => value == null ? null : Number((Number(value) / 10000).toFixed(6));
export function insightAmount(value) {
  if (!/^[¥￥]-?[\d,]+(?:\.\d+)?$/.test(String(value || ''))) return null;
  const amount = Number(String(value).slice(1).replaceAll(',', ''));
  if (!Number.isFinite(amount)) return null;
  return Math.abs(amount) < 10000 ? {value: amount, unit:'元'} : {value:insightWan(amount), unit:'万元'};
}

// Keep actionable business feedback; never render arbitrary backend diagnostics.
export function insightRankingError(message) {
  const messages = {
    '此团队目录用途不支持指定权限': '当前团队排名暂时无法加载，请稍后重试。',
    '当前账号未获查看排名授权': '当前账号没有查看排名的权限，请联系运营。',
    '当前账号未获此操作或数据范围的授权': '当前账号没有查看该范围排名的权限，请联系运营。',
    '请选择有效的团队分组': '所选团队已失效，请重新选择团队。',
    '所选成员不在可查看范围内或已停用': '所选成员不在可查看范围内或已停用，请重新选择。',
    '排名范围与当前选择不一致': '排名范围与当前选择不一致，请重试。',
    '排名成员与当前选择不一致': '排名成员与当前选择不一致，请重试。',
    '排名期间与当前选择不一致': '排名期间与当前选择不一致，请重试。',
    '排名团队与当前选择不一致': '排名团队与当前选择不一致，请重试。',
    '当前后端尚未提供人均跟进排名，请在配套版本上线后重试': '人均跟进排名暂不可用，请稍后重试。',
    'FDE暂不提供销售排名': '售前 / FDE 暂不提供销售排名。',
  };
  return Object.hasOwn(messages, message) ? messages[message] : '排名暂时无法加载，请稍后重试。';
}
