const {id}=require('../../utils/taskChecklist');
function draft(items){return (items||[]).map(item=>({item_id:item.item_id,definition_version:item.definition_version,title:item.title||'',acceptance_criteria:item.acceptance_criteria||''}));}
function add(){return {item_id:id(),definition_version:1,title:'',acceptance_criteria:''};}
function prepare(items,baseline=[]){
 if(Array.isArray(items)&&!items.length&&baseline.length)throw Error('已有推进清单至少保留一项');
 if(!Array.isArray(items)||items.length>100)throw Error('推进清单最多 100 项');
 const before=new Map(baseline.map(item=>[item.item_id,item])),seen=new Set();
 return items.map(item=>{
  if(!item.item_id||seen.has(item.item_id))throw Error('推进项标识重复或缺失');seen.add(item.item_id);
  const title=String(item.title||'').trim(),acceptance_criteria=String(item.acceptance_criteria||'').trim();
  if(!title||[...title].length>500||!acceptance_criteria||[...acceptance_criteria].length>2000)throw Error('每项请填写标题和完成标准');
  const old=before.get(item.item_id),changed=old&&(old.title!==title||old.acceptance_criteria!==acceptance_criteria);
  return {item_id:item.item_id,definition_version:old?old.definition_version+Number(Boolean(changed)):1,title,acceptance_criteria};
 });
}
function impact(items=[],baseline=[]){
 const before=new Map(baseline.map(item=>[item.item_id,item])),ids=new Set(items.map(item=>item.item_id));
 const changedCompleted=items.filter(item=>before.get(item.item_id)?.state==='completed'&&before.get(item.item_id).acceptance_criteria!==item.acceptance_criteria).length;
 return `有效项 ${baseline.length} → ${items.length}；新增 ${items.filter(item=>!before.has(item.item_id)).length}，移除 ${baseline.filter(item=>!ids.has(item.item_id)).length}。${changedCompleted?`${changedCompleted} 项已完成标准改变，保存后需执行人复核。`:'仅调整名称或顺序不重置完成成果。'}`;
}
module.exports={draft,add,prepare,impact};
