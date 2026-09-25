const { adviceResult, businessTimeLabel } = require('./customerAdvice');
const TABS = {overview:'商机概览',tasks:'待办事项',visits:'跟进记录',opportunity:'商机进展'};
function opportunityContext(raw, id) {
  const opportunity = (raw.opportunities || []).find(item => String(item.id) === String(id));
  if (!id || !opportunity) throw Error('商机不存在或无权查看');
  const linked = items => (items || []).filter(item => (item.opportunity_id && String(item.opportunity_id) === String(id)) ||
    (Array.isArray(item.linked_opportunities) && item.linked_opportunities.some(link=>String(link.id)===String(id))));
  return {read_model:raw.read_model,summary:raw.summary,profile:raw.profile,primary_opportunity:opportunity,id:raw.id,name:raw.name,owner_name:raw.owner_name,team_name:raw.team_name,opportunities:[opportunity],tasks:linked(raw.tasks),visits:linked(raw.visits),risks:linked(raw.risks)};
}
function opportunityEvents(context) {
  const item=context.opportunities[0], rows=[];
  const add=(key,title,date,detail)=>{if(date && Number.isFinite(Date.parse(date)))rows.push({key,title,date:businessTimeLabel(date),at:Date.parse(date),detail});};
  add('created','商机创建',item.created_at,item.name);
  if(item.updated_at && item.updated_at !== item.created_at)add('updated','商机信息更新',item.updated_at,'此为信息更新时间，不代表阶段变更时间');
  context.visits.forEach(v=>add(`visit:${v.id}`,'跟进记录',v.interaction_at||v.visit_date,v.follow_up_record||v.visit_goal||'已记录跟进'));
  context.tasks.forEach(t=>{add(`task:${t.id}`,'任务创建',t.created_at,t.title);if(t.status==='completed')add(`done:${t.id}`,'任务完成',t.completed_at,t.title);});
  return rows.sort((a,b)=>b.at-a.at).slice(0,20);
}
module.exports={TABS,opportunityContext,opportunityEvents,adviceResult};
