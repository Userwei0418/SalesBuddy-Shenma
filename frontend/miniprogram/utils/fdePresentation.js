// Presentation only: null remains unknown and chart values come from the API.
const numeric = value => value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? null : Number(value);
const money = value => numeric(value) === null ? '未登记' : (Number(value) / 10000).toLocaleString('zh-CN', {maximumFractionDigits: 2});
const pad = value => String(value).padStart(2, '0');
function beijingTime(value, dateOnly = false) {
  if (!value) return '';
  if (/^\d{4}-\d{2}-\d{2}$/.test(String(value))) return String(value).replace(/-/g, '/');
  const time = Date.parse(value);
  if (!Number.isFinite(time)) return '';
  const date = new Date(time + 8 * 60 * 60 * 1000);
  const day = `${date.getUTCFullYear()}/${pad(date.getUTCMonth() + 1)}/${pad(date.getUTCDate())}`;
  return dateOnly ? day : `${day} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
}
function visitItem(row) { return {...row, dateText: beijingTime(row.interaction_at)}; }
function stageBars(rows) {
  const maximum = Math.max(0, ...rows.map(row => numeric(row.count) || 0));
  return rows.map((row, index) => ({...row, amountText: money(row.amount), tone: index % 6,
    width: maximum ? Math.max(0, Number(row.count) || 0) / maximum * 100 : 0}));
}
function visibleStageBars(rows) {
  const {STAGES} = require('./opportunity');
  const order = new Map(STAGES.map((stage, index) => [stage.code, index]));
  const visible = rows.filter(row => numeric(row.count) > 0).slice().sort((a, b) =>
    (order.has(a.code) ? order.get(a.code) : STAGES.length) -
    (order.has(b.code) ? order.get(b.code) : STAGES.length));
  return stageBars(visible);
}
function rhythmBars(rows) {
  const recent = rows.slice(-7), maximum = Math.max(0, ...recent.map(row => numeric(row.visits) || 0));
  return recent.map(row => ({...row, dateLabel: String(row.date || '').slice(5).replace('-', '/'),
    height: maximum ? Math.max(0, Number(row.visits) || 0) / maximum * 100 : 0}));
}
// API dates are calendar days in Asia/Shanghai. UTC arithmetic here avoids
// device timezone/DST changing Monday boundaries; aggregate before display limits.
function progressBars(rows, mode = 'day') {
  const ordered = rows.slice().sort((a,b)=>String(a.date).localeCompare(String(b.date)));
  if (mode !== 'week') return rhythmBars(ordered);
  const weeks = new Map();
  for (const row of ordered) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(row.date))) continue;
    const day = new Date(row.date + 'T00:00:00Z');
    if (!Number.isFinite(day.getTime())) continue;
    day.setUTCDate(day.getUTCDate() - (day.getUTCDay() + 6) % 7);
    const key = day.toISOString().slice(0,10);
    const week = weeks.get(key) || {date:key,visits:0};
    const value = numeric(row.visits);
    week.visits = week.visits === null || value === null ? null : week.visits + value;
    // Server weekly buckets may end today or at the selected reporting boundary.
    if (/^\d{4}-\d{2}-\d{2}$/.test(String(row.end_date))) week.end_date = row.end_date;
    weeks.set(key,week);
  }
  const recent = [...weeks.values()].slice(-12), maximum = Math.max(0,...recent.map(row=>row.visits || 0));
  return recent.map(row=>{
    const end = new Date(row.date+'T00:00:00Z');end.setUTCDate(end.getUTCDate()+6);
    return {...row, dateLabel:row.date.slice(5).replace('-','/'), endLabel:(row.end_date||end.toISOString().slice(0,10)).slice(5,10).replace('-','/'),
      height:maximum && row.visits !== null ? row.visits / maximum * 100 : 0};
  });
}
function rankedRows(rows, key) {
  const sorted = rows.slice().sort((a, b) => (numeric(b[key]) || 0) - (numeric(a[key]) || 0) || String(a.name || '').localeCompare(String(b.name || '')));
  const maximum = Math.max(0, ...sorted.map(row => numeric(row[key]) || 0));
  return sorted.map((row, index) => ({...row, rank: index + 1, value: numeric(row[key]) === null ? '—' : row[key],
    width: maximum ? Math.max(0, Number(row[key]) || 0) / maximum * 100 : 0}));
}
module.exports = {numeric, money, beijingTime, visitItem, stageBars, visibleStageBars, rhythmBars, progressBars, rankedRows};
