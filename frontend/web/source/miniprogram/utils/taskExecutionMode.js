function mode(task,support){
 if(task&&['legacy','execution_v1','execution_v2','execution_v3'].includes(task.execution_mode))return task.execution_mode;
 if(task&&task.execution_mode!=null)throw Error('任务执行模式不受支持，请升级后重试');
 if((support&&support.protocol_version!=null)||(task&&(task.execution_protocol_version!=null||(task.execution&&task.execution.protocol_version!=null)||(task.execution_compatibility&&task.execution_compatibility.protocol_version!=null))))throw Error('任务执行模式缺失，服务响应不完整，请刷新');
 return 'legacy';
}
function supportsVersion(support,version){
 if(!support)return false;
 if(Array.isArray(support.supported_versions))return support.supported_versions.includes(version);
 return version===1?[1,2].includes(support.protocol_version):support.protocol_version===version;
}
function creationSupport(support={}){
 const v1=supportsVersion(support,1)&&(Object.prototype.hasOwnProperty.call(support,'v1_creation_enabled')?support.v1_creation_enabled===true:support.creation_enabled===true);
 const v2=supportsVersion(support,2)&&support.v2_creation_enabled===true;
 const v3=supportsVersion(support,3)&&support.v3_creation_enabled===true;
 return {v1,v2,v3,defaultMode:v3&&support.default_creation_mode==='execution_v3'?'execution_v3':v2&&support.default_creation_mode==='execution_v2'?'execution_v2':'legacy'};
}
function creation(input){
 const selected=input.executionMode||'legacy';
 if(!['legacy','execution_v1','execution_v2','execution_v3'].includes(selected))throw Error('请选择有效任务模式');
 if(selected==='legacy'){if(input.checklist&&input.checklist.length)throw Error('普通任务不能附带新版清单');return {};}
 if(selected==='execution_v3')return {execution_protocol_version:3};
 const items=input.checklist || [];if(!Array.isArray(items)||items.length>100||(selected==='execution_v1'&&!items.length))throw Error('请核对推进清单');
 const seen=new Set();for(const item of items){if(!item||typeof item.item_id!=='string'||seen.has(item.item_id)||item.definition_version!==1||!String(item.title||'').trim()||!String(item.acceptance_criteria||'').trim())throw Error('人工推进清单无效');seen.add(item.item_id);}
 return {execution_protocol_version:selected==='execution_v2'?2:1,checklist:items};
}
module.exports={mode,creation,supportsVersion,creationSupport};
