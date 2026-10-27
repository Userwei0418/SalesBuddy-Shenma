const {businessTimeLabel}=require('./customerAdvice');
const {IntentHistoryReader,emptyHistory}=require('./opportunityIntentHistory');

const kinds=new Set(['scalar','list','people','materials','unavailable']);
const statuses=new Set(['recorded','empty','unavailable','restricted']);
const nullableText=value=>value===null||typeof value==='string';
function requireHistory(condition){
  if(!condition)throw Error('历史记录响应不完整，请重试');
}

// The server projects saved evidence into display values. Do not look up
// current directories, infer missing facts, or stringify raw change JSON.
function historyValue(value){
  requireHistory(value&&kinds.has(value.kind)&&statuses.has(value.status)&&
    Array.isArray(value.rows)&&value.rows.length<=100&&
    typeof value.empty_label==='string'&&typeof value.truncated==='boolean');
  requireHistory(value.status==='recorded'||value.rows.length===0);
  const rows=value.rows.map((row,index)=>{
    requireHistory(row&&nullableText(row.name)&&nullableText(row.id)&&
      nullableText(row.id_label)&&nullableText(row.detail)&&typeof row.missing_name==='boolean');
    return {
      key:`value-${index}`,id:row.id,
      name:row.missing_name?'名称未记录':row.name||'历史值未完整记录',
      missingName:row.missing_name,idLabel:row.id_label||'记录 ID',detail:row.detail||'',
    };
  });
  let emptyLabel='';
  if(!rows.length){
    const fallback=value.status==='restricted'?'当前无权查看此历史值':
      value.status==='empty'?'未填写':'历史值未完整记录';
    emptyLabel=value.empty_label||fallback;
  }
  return {rows,emptyLabel,status:value.status,truncated:value.truncated};
}

function changeHistoryItem(item){
  requireHistory(item&&typeof item.id==='string'&&item.id&&
    Number.isInteger(item.version_no)&&item.version_no>=1&&
    typeof item.created_at==='string'&&businessTimeLabel(item.created_at)&&
    item.actor&&nullableText(item.actor.id)&&nullableText(item.actor.name)&&
    ['recorded','current','unavailable'].includes(item.actor.name_status)&&
    nullableText(item.reason)&&Array.isArray(item.fields)&&item.fields.length<=200&&
    ['recorded','partial','unavailable'].includes(item.detail_status)&&
    Number.isInteger(item.unavailable_field_count)&&item.unavailable_field_count>=0);
  const actor=item.actor;
  const hasName=actor.name_status!=='unavailable'&&actor.name&&actor.name.trim();
  const actorLabel=hasName?`${actor.name}${actor.name_status==='current'?'（当前姓名）':''}`:
    `操作人姓名未记录${actor.id?`（用户 ID：${actor.id}）`:''}`;
  const changes=item.fields.map((field,index)=>{
    requireHistory(field&&typeof field.field==='string'&&field.field&&typeof field.label==='string'&&field.label);
    return {key:`${field.field}-${index}`,label:field.label,
      before:historyValue(field.before),after:historyValue(field.after)};
  });
  return {id:item.id,actor:actorLabel,at:businessTimeLabel(item.created_at),
    versionLabel:`记录版本 ${item.version_no}`,reason:item.reason||'',changes,
    unavailable:item.detail_status==='unavailable',
    partial:item.detail_status==='partial',unavailableFieldCount:item.unavailable_field_count};
}

class ChangeHistoryReader extends IntentHistoryReader{
  format(row){return changeHistoryItem(row);}
  failure(error,prior,offset){
    // A stale readable page must not survive revoked access or deletion.
    if([401,403,404].includes(error.statusCode)){
      return {...emptyHistory(),error:error.message||'当前无法读取该商机历史，请确认访问权限'};
    }
    return super.failure(error,prior,offset);
  }
}

module.exports={historyValue,changeHistoryItem,ChangeHistoryReader,emptyHistory};
