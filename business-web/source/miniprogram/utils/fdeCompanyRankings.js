const {numeric, money} = require('./fdePresentation');

// Whole-company rankings must be explicitly supplied by the server. The existing
// dashboard ranking is scoped to self/team and cannot be promoted to this scope.
function companyRankings(source, year, quarters) {
  if (!source) return {demoRankingReady:false,demoRanking:[],companyRankingReady:false,companyRankingError:'全员榜单暂未接通',followupRanking:[],recognizedRanking:[]};
  const sameQuarters = values => Array.isArray(values) && JSON.stringify([...new Set(values)].sort()) === JSON.stringify([...new Set(quarters)].sort());
  if (source.data_source !== 'database' || !['all_fde','all_fde_leads','company_fde_teams'].includes(source.scope) || source.complete !== true || source.year !== year || !sameQuarters(source.quarters) || !Array.isArray(source.items) || source.total !== source.items.length) {
    throw Error('全员榜单数据不完整');
  }
  const ids = new Set();
  for (const row of source.items) {
    if (!row.user_id || ids.has(row.user_id) || typeof row.name !== 'string' || !row.name.trim()) throw Error('全员榜单成员数据不完整');
    ids.add(row.user_id);
    for (const key of ['followup_count','recognized_amount']) {
      const n=numeric(row[key]);
      if (row[key] === undefined || row[key] !== null && (n === null || n < 0 || key === 'followup_count' && !Number.isInteger(n))) throw Error('全员榜单指标无效');
    }
  }
  const rankBy=key=>{
    const sorted=source.items.slice().sort((a,b)=>{
      const av=numeric(a[key]),bv=numeric(b[key]);
      return av===null&&bv!==null?1:bv===null&&av!==null?-1:(bv||0)-(av||0)||String(a.user_id).localeCompare(String(b.user_id));
    });
    const max=Math.max(0,...sorted.map(r=>numeric(r[key])||0));
    let previous,rank=0;
    return sorted.map((row,index)=>{
      const value=numeric(row[key]);
      if(value!==null&&value!==previous)rank=index+1;
      previous=value;
      return {...row,rank:value===null?'—':rank,value:value===null?'未登记':key==='recognized_amount'?money(value):String(value),width:max&&value!==null?value/max*100:0};
    });
  };
  const demoRankingReady=source.items.every(row=>row.demo_scene_count!==undefined && (row.demo_scene_count===null || (numeric(row.demo_scene_count)!==null && Number.isInteger(numeric(row.demo_scene_count)) && numeric(row.demo_scene_count)>=0)));
  return {demoRankingReady,demoRanking:demoRankingReady?rankBy('demo_scene_count'):[],companyRankingReady:true,companyRankingError:'',companyRankingTotal:source.total,followupRanking:rankBy('followup_count'),recognizedRanking:rankBy('recognized_amount')};
}
module.exports={companyRankings};
