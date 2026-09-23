// Quarter boundaries follow the business timezone, Asia/Shanghai (UTC+8).
const OPPORTUNITY_OVERVIEW_HELP = "全部商机：当前权限范围内所有未删除商机，包含在推、已成单和已丢单；季度筛选不改变当前存量。已成单商机：当前已赢单，按实际成单日期归季。活跃商机：所选期间有正式跟进的商机去重，包含仍在跟进的已赢单，排除已丢单。新增商机：按原始建单日期归季，历史导入使用来源日期；缺失时不按导入时间补算。\n全部时间表示跨年历史，不受旁边年份限制；选择年份后查看该年全年，可继续多选季度。缺少实际日期的记录不计入对应季度指标，但仍计入全部商机。下方列表独立按预计关单时间、阶段等条件筛选。";

function beijingDateParts(value) {
  if (value === null || value === undefined || value === '') return null;
  let date;
  if (typeof value === 'string') {
    const input = value.trim();
    const match = /^(\d{4})-(\d{2})-(\d{2})(?:$|[T ])/.exec(input);
    if (!match) return null;
    const year = Number(match[1]), month = Number(match[2]), day = Number(match[3]);
    const calendar = new Date(Date.UTC(year, month - 1, day));
    if (calendar.getUTCFullYear() !== year || calendar.getUTCMonth() + 1 !== month || calendar.getUTCDate() !== day) return null;
    if (input.length === 10) return { year, month, quarter: Math.floor((month - 1) / 3) + 1 };
    const normalized = input.replace(' ', 'T');
    date = new Date(/[zZ]$|[+-]\d{2}:?\d{2}$/.test(normalized) ? normalized : `${normalized}+08:00`);
  } else {
    date = new Date(value);
  }
  if (Number.isNaN(date.getTime())) return null;
  const china = new Date(date.getTime() + 8 * 3600000);
  const month = china.getUTCMonth() + 1;
  return { year: china.getUTCFullYear(), month, quarter: Math.floor((month - 1) / 3) + 1 };
}

function quarterSelection(year, quarters = []) {
  const selected = [...new Set(quarters.map(Number).filter(value => [1, 2, 3, 4].includes(value)))].sort();
  return {
    year: Number(year), quarters: selected,
    options: [1, 2, 3, 4].map(value => ({ value, label: `Q${value}`, selected: selected.includes(value) })),
    label: !selected.length ? '全部时间（跨年）' : `${year}年 ${selected.length === 4 ? '全年' : selected.map(value => `Q${value}`).join(' + ')}`,
  };
}

function matchesQuarter(value, selection) {
  if (!selection.quarters.length) return true;
  const date = beijingDateParts(value);
  return Boolean(date && date.year === selection.year && selection.quarters.includes(date.quarter));
}

function expectedCloseQuarter(item = {}) {
  if (item.expected_close_date) return beijingDateParts(item.expected_close_date);
  const year = Number(item.expected_close_year), quarter = Number(item.expected_close_quarter);
  return Number.isInteger(year) && year >= 1000 && year <= 9999 && Number.isInteger(quarter) && quarter >= 1 && quarter <= 4
    ? {year,quarter} : null;
}

function groupOpportunitiesByQuarter(items) {
  const groups = new Map();
  items.forEach(item => {
    const date = expectedCloseQuarter(item);
    const key = date ? `${date.year}-Q${date.quarter}` : 'undated';
    if (!groups.has(key)) groups.set(key, {
      key,
      label: date ? `${date.year}年 · Q${date.quarter}` : '待确认季度',
      order: date ? date.year * 4 + date.quarter : Infinity,
      items: [],
    });
    groups.get(key).items.push(item);
  });
  return [...groups.values()].sort((a, b) => a.order - b.order).map(group => ({
    key: group.key, label: group.label,
    items: group.items.sort((a, b) => (b.progressPercent || 0) - (a.progressPercent || 0)),
  }));
}

module.exports = { beijingDateParts, quarterSelection, matchesQuarter, groupOpportunitiesByQuarter, expectedCloseQuarter, OPPORTUNITY_OVERVIEW_HELP };
