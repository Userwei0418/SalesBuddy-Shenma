// Quarter boundaries follow the business timezone, Asia/Shanghai (UTC+8).
const OPPORTUNITY_OVERVIEW_HELP = "列表筛选同时作用于上方总览和下方列表。团队、负责人、成员、阶段、等级、关键词、产品线及预计关单时间，共同限定商机范围。全部商机为完整筛选结果数量，包含在推、已成单和已丢单，不受分页影响。\n统计时间不改变商机范围：已成单按实际成单日期；活跃按正式跟进日期去重并排除已丢单；新增按原始建单日期，历史导入使用来源日期，缺失时不按导入时间补算；FDE的Demo场景按场景创建时间。各项可能重叠，不能相加，Demo场景数也不等于商机数。预计关单日期不代替实际成单或建单日期。\n全年等于所选年份四季全选；全部年份才是跨年。缺少实际日期的记录显示待补，不归入年度或季度；跨年成单数量包含成单时间待补记录。";

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
  const isAllYears = year === 'all';
  const selected = isAllYears ? [] : [...new Set(quarters.map(Number).filter(value => [1, 2, 3, 4].includes(value)))].sort();
  return {
    year: isAllYears ? 'all' : Number(year), isAllYears, quarters: selected,
    options: [1, 2, 3, 4].map(value => ({ value, label: `Q${value}`, selected: selected.includes(value) })),
    label: !selected.length ? '全部时间（跨年）' : `${year}年 ${selected.length === 4 ? '全年' : selected.map(value => `Q${value}`).join(' + ')}`,
  };
}

function summaryQuarterSelection(year, quarters = [1, 2, 3, 4]) {
  if (year === 'all') return {
    year: 'all', quarters: [], isAllYears: true, isFullYear: false,
    label: '全部年份', yearLabel: '全部年份', options: [],
  };
  const normalized = quarterSelection(year, quarters);
  const selected = normalized.quarters.length ? normalized.quarters : [1, 2, 3, 4];
  const isFullYear = selected.length === 4;
  return {
    year: normalized.year, quarters: selected, isAllYears: false, isFullYear,
    label: `${normalized.year}年 ${isFullYear ? '全年' : selected.map(value => `Q${value}`).join(' + ')}`,
    yearLabel: `${normalized.year}年`,
    options: [1, 2, 3, 4].map(value => ({value, label: `Q${value}`, selected: !isFullYear && selected.includes(value)})),
  };
}

function toggleSummaryQuarter(selection, value) {
  if (selection.isAllYears || selection.year === 'all') return selection;
  if (value === 'all') return summaryQuarterSelection(selection.year);
  const quarter = Number(value);
  if (![1, 2, 3, 4].includes(quarter)) return selection;
  const quarters = selection.isFullYear || selection.quarters.length === 4 ? [quarter]
    : selection.quarters.includes(quarter) ? selection.quarters.filter(item => item !== quarter)
      : selection.quarters.concat(quarter);
  return summaryQuarterSelection(selection.year, quarters);
}

// Lists default to unrestricted history. Keep a numeric year for the existing
// API, but represent that state explicitly instead of an empty quarter toggle.
function listQuarterSelection(year, quarters = []) {
  return quarters.length ? summaryQuarterSelection(year, quarters)
    : {...summaryQuarterSelection('all'), year: Number(year)};
}

function toggleListQuarter(selection, value) {
  if (selection.isAllYears) {
    if (value === 'all') return summaryQuarterSelection(selection.year);
    if (![1, 2, 3, 4].includes(Number(value))) return selection;
    return summaryQuarterSelection(selection.year, [Number(value)]);
  }
  return toggleSummaryQuarter(selection, value);
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

function groupOpportunitiesByQuarter(items, options = {}) {
  const activeFirst = options.activeFirst === true;
  const isActive = item => ['open', 'active', 'in_progress'].includes(String(item?.status || '').toLowerCase());
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
  return [...groups.values()].sort((a, b) => {
    const aActive = a.items.some(isActive), bActive = b.items.some(isActive);
    return (activeFirst ? Number(bActive) - Number(aActive) : 0) || (a.order - b.order);
  }).map(group => ({
    key: group.key, label: group.label,
    items: group.items.sort((a, b) =>
      (activeFirst ? Number(isActive(b)) - Number(isActive(a)) : 0)
      || ((b.progressPercent || 0) - (a.progressPercent || 0))),
  }));
}

module.exports = { beijingDateParts, quarterSelection, summaryQuarterSelection, toggleSummaryQuarter, listQuarterSelection, toggleListQuarter, matchesQuarter, groupOpportunitiesByQuarter, expectedCloseQuarter, OPPORTUNITY_OVERVIEW_HELP };
