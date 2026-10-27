const { beijingDateParts } = require('./opportunityQuarter');

function dayNumber(value) {
  if (!beijingDateParts(value)) return null;
  let input = value;
  if (typeof value === 'string') {
    input = value.trim().replace(' ', 'T');
    if (input.length === 10) input += 'T00:00:00';
    if (!/[zZ]$|[+-]\d{2}:?\d{2}$/.test(input)) input += '+08:00';
  }
  return Math.floor((new Date(input).getTime() + 8 * 3600000) / 86400000);
}

function recentVisits(visits, now = new Date()) {
  const today = dayNumber(now);
  const seen = new Set();
  const recent = (visits || []).filter(item => {
    const day = dayNumber(item.interaction_at);
    if (day === null || day < today - 6 || day > today) return false;
    if (item.status && !['confirmed', 'archived'].includes(item.status)) return false;
    if (item.id && seen.has(item.id)) return false;
    if (item.id) seen.add(item.id);
    return true;
  });
  return recent;
}
function sevenDayVisits(visits, now = new Date()) {
  const today=dayNumber(now),counts={};
  (visits||[]).forEach(item=>{const day=dayNumber(item.interaction_at);if(day!==null)counts[day]=(counts[day]||0)+1;});
  const rows=Array.from({length:7},(_,index)=>{
    const day=today-6+index,date=new Date(day*86400000);
    return {key:date.toISOString().slice(0,10),weekday:`周${'日一二三四五六'[date.getUTCDay()]}`,count:counts[day]||0,isToday:index===6};
  });
  const max=Math.max(...rows.map(row=>row.count),1);
  return rows.map(row=>({...row,height:`${Math.max(row.count?22:5,Math.round(row.count/max*100))}%`}));
}
module.exports = { recentVisits, sevenDayVisits };
