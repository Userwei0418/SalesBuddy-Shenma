const {id}=require('../../utils/taskChecklist');
const FIELDS={title:'任务标题',description:'任务描述',deliverable:'交付物',acceptance_criteria:'验收标准',due_at:'截止时间',priority_code:'优先级'};
const LIMITS={title:500,description:8000,deliverable:4000,acceptance_criteria:4000};
const priority={normal:'普通',medium:'中',high:'高',urgent:'紧急'};
function draft(source){return JSON.parse(JSON.stringify(source||{title:'',description:'',deliverable:'',acceptance_criteria:'',due_at:'',priority_code:'normal',checklist:[]}));}
function prepare(source,baseline,{independentChecklist=false}={}){
 const result={};for(const key of Object.keys(LIMITS)){result[key]=String(source[key]||'').trim();if([...result[key]].length>LIMITS[key]||(key==='title'&&!result[key]))throw Error(`${FIELDS[key]}填写不完整或过长`);}
 if(!priority[source.priority_code])throw Error('请选择优先级');
 if(typeof source.due_at!=='string'||!/(Z|[+-]\d\d:\d\d)$/.test(source.due_at)||!Number.isFinite(Date.parse(source.due_at)))throw Error('请选择有效截止时间');
 result.priority_code=source.priority_code;result.due_at=source.due_at;
 if(independentChecklist){if(!result.deliverable||!result.acceptance_criteria)throw Error('请明确整体交付物和完成标准');return result;}
 if(!Array.isArray(source.checklist)||!source.checklist.length||source.checklist.length>100)throw Error('请保留 1–100 个推进项');
 const old=new Map((baseline.checklist||[]).map(i=>[i.item_id,i])),seen=new Set();
 result.checklist=source.checklist.map(item=>{
  if(!item.item_id||seen.has(item.item_id))throw Error('推进项标识重复或缺失');seen.add(item.item_id);
  const title=String(item.title||'').trim(),acceptance_criteria=String(item.acceptance_criteria||'').trim();
  if(!title||[...title].length>500||!acceptance_criteria||[...acceptance_criteria].length>2000)throw Error('每项请填写标题与验收标准');
  const previous=old.get(item.item_id);
  return {item_id:item.item_id,title,acceptance_criteria,definition_version:previous?previous.definition_version+Number(previous.title!==title||previous.acceptance_criteria!==acceptance_criteria):1};
 });return result;
}
function differences(before={},after={}){
 const rows=[];for(const [key,label] of Object.entries(FIELDS))if(before[key]!==after[key])rows.push({key,label,before:priority[before[key]]&&key==='priority_code'?priority[before[key]]:before[key]||'未填写',after:priority[after[key]]&&key==='priority_code'?priority[after[key]]:after[key]||'未填写'});
 const old=new Map((before.checklist||[]).map(i=>[i.item_id,i])),next=new Map((after.checklist||[]).map(i=>[i.item_id,i]));
 const text=i=>i?`${i.title}\n验收标准：${i.acceptance_criteria}`:'无';
 for(const key of new Set([...old.keys(),...next.keys()])){const a=old.get(key),b=next.get(key);if(!a||!b||a.title!==b.title||a.acceptance_criteria!==b.acceptance_criteria)rows.push({key:'item:'+key,label:!a?'新增推进项':!b?'移除推进项':'修改推进项',before:text(a),after:text(b)});}
 if(!rows.some(r=>r.key.startsWith('item:'))&&JSON.stringify([...old.keys()])!==JSON.stringify([...next.keys()]))rows.push({key:'order',label:'推进项顺序',before:(before.checklist||[]).map(i=>i.title).join(' → '),after:(after.checklist||[]).map(i=>i.title).join(' → ')});
 return rows;
}
function addItem(){return {item_id:id(),definition_version:1,title:'',acceptance_criteria:''};}
module.exports={draft,prepare,differences,addItem};
