const { beijingDateParts } = require('./opportunityQuarter');
function defaultQuarterKeys(now = new Date()) {
  const current = beijingDateParts(now);
  return Array.from({ length:current.quarter }, (_,index)=>`${current.year}-Q${index+1}`);
}
function selectedQuarters(options, keys) {
  const rows=options.filter(item=>keys.includes(item.key)).sort((a,b)=>a.year-b.year||a.quarter-b.quarter);
  if (!rows.length) return null;
  const last=rows[rows.length-1];
  return { ...last, quarters:rows.map(item=>item.quarter), keys:rows.map(item=>item.key),
    label:`${last.year} ${rows.map(item=>`Q${item.quarter}`).join('+')}` };
}
// BACKEND-CONTRACT QUARTER: selection只表示一个年份内多个季度，不支持跨年混合求和。
function matchesSelectedQuarter(date, selection) {
  return !!date && Number(date.year)===Number(selection.year) && (selection.quarters || [selection.quarter]).includes(Number(date.quarter));
}
module.exports={defaultQuarterKeys,selectedQuarters,matchesSelectedQuarter};
