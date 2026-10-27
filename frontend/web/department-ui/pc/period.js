// 统计周期（北京时间）：今日、本周（周一至周日）、双周（上周＋本周）、本月、本季度。纯函数，便于单元测试。
const BEIJING = 8 * 3600000, DAY = 86400000;

export function dayKey(value) {
  if (value === null || value === undefined || value === '') return '';
  if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  let stamp = typeof value === 'number' ? value : NaN;
  if (typeof value === 'string') {
    let input = value.trim().replace(' ', 'T');
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(input)) input += '+08:00';
    stamp = Date.parse(input);
  } else if (value instanceof Date) stamp = value.getTime();
  return Number.isFinite(stamp) ? new Date(stamp + BEIJING).toISOString().slice(0, 10) : '';
}
export const todayKey = (now = Date.now()) => new Date(now + BEIJING).toISOString().slice(0, 10);
export const addDays = (key, days) => new Date(Date.parse(key + 'T00:00:00Z') + days * DAY).toISOString().slice(0, 10);
const label = key => { const [, m, d] = key.split('-').map(Number); return `${m}月${d}日`; };

export const PERIODS = Object.freeze([
  {key: 'day', label: '今日'}, {key: 'week', label: '本周'}, {key: 'biweek', label: '双周'}, {key: 'month', label: '本月'}, {key: 'quarter', label: '本季度'},
]);

// 展示完整自然周期；已经发生的工作只统计到今天。历史自定义区间保持原结束日。
export function statisticsRange(range) {
  return range.today && range.today < range.end ? {...range, end: range.today} : range;
}

// 自定义区间：起止日期都按北京时间的自然日，含首尾两天
export function customRange(start, end, now = Date.now()) {
  const today = todayKey(now);
  const [a, b] = [start, end].sort();
  const elapsed = Math.max(1, Math.round((Date.parse((today < b ? today : b) + 'T00:00:00Z') - Date.parse(a + 'T00:00:00Z')) / DAY) + 1);
  return {kind: 'custom', name: '所选区间', start: a, end: b, today, title: `${label(a)}–${label(b)}`, year: Number(a.slice(0, 4)), quarter: Math.floor((Number(a.slice(5, 7)) - 1) / 3) + 1, elapsed};
}

export function periodRange(kind = 'day', now = Date.now()) {
  const today = todayKey(now);
  const [year, month] = today.split('-').map(Number);
  let start = today, end = today;
  if (kind === 'week') {
    const weekday = (new Date(today + 'T00:00:00Z').getUTCDay() + 6) % 7; // 周一为 0
    start = addDays(today, -weekday); end = addDays(start, 6);
  } else if (kind === 'biweek') {
    const weekday = (new Date(today + 'T00:00:00Z').getUTCDay() + 6) % 7;
    start = addDays(today, -weekday - 7); end = addDays(start, 13);
  } else if (kind === 'month') {
    start = `${year}-${String(month).padStart(2, '0')}-01`;
    end = new Date(Date.UTC(year, month, 0)).toISOString().slice(0, 10);
  } else if (kind === 'quarter') {
    const first = Math.floor((month - 1) / 3) * 3 + 1;
    start = `${year}-${String(first).padStart(2, '0')}-01`;
    end = new Date(Date.UTC(year, first + 2, 0)).toISOString().slice(0, 10);
  } else kind = 'day';
  const quarter = Math.floor((month - 1) / 3) + 1;
  const name = {day: '今日', week: '本周', biweek: '双周', month: '本月', quarter: '本季度'}[kind];
  const title = kind === 'day' ? `${label(today)} 周${'日一二三四五六'[new Date(today + 'T00:00:00Z').getUTCDay()]}`
    : kind === 'quarter' ? `${year} 年 Q${quarter}（${label(start)}–${label(end)}）` : `${label(start)}–${label(end)}`;
  // 截至今天的天数：本周、本月、本季度还没过完时，人均与日均按已过天数算
  const elapsed = Math.max(1, Math.round((Date.parse((today < end ? today : end) + 'T00:00:00Z') - Date.parse(start + 'T00:00:00Z')) / DAY) + 1);
  return {kind, name, start, end, today, title, year, quarter, elapsed};
}
