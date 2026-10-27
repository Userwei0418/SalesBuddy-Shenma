import {api, identity, runtime} from '../../../pc/data.js';
import {listItems} from './model.js';

const query=params=>new URLSearchParams(Object.entries(params).filter(([,value])=>value!==undefined&&value!==null&&value!=='')).toString();
const get=(path,params={})=>api().request({path:`${path}${Object.keys(params).length?'?'+query(params):''}`,fresh:true});
const pathFor=kind=>kind==='objective'?'/review/objectives':kind==='key_result'?'/review/key-results':(()=>{throw Error('未知目标类型');})();
const flights=new Map();
// Use the same persistent intent store as targets, but validate the specific
// M2 receipt before releasing it. A malformed success must remain retry-safe.
export async function okrWrite(path,data,method,validate){
  const client=api(),owner=identity(),auth=client.getAuth();if(!owner||!auth?.actor)throw Error('请先登录');
  const receipts=runtime().require('utils/requestIdentity'),options={path,data,method},scope=[client.getBaseUrl(),auth.actor.workspace_id,auth.actor.user_id];
  const key=`${owner}:${receipts.fingerprint(scope,options)}`;if(flights.has(key))return flights.get(key);
  const receipt=receipts.begin(scope,options);
  const promise=client.request({...options,idempotencyKey:receipt.key}).then(result=>{
    if(owner!==identity())throw Error('账号或权限已变化，请重新读取');
    if(!validate(result))throw Error('保存回执不完整，请先刷新核对');
    receipts.finish(receipt);return result;
  }).catch(error=>{if([400,403,404,422].includes(error.statusCode)||(error.statusCode===409&&!String(error.serverCode||'').startsWith('IDEMPOTENCY_')))receipts.finish(receipt);throw error;}).finally(()=>flights.delete(key));
  flights.set(key,promise);return promise;
}

export async function allPages(read) {
  const owner=identity(),rows=new Map();let expected;
  for(let offset=0;offset<100000;offset+=100){
    const result=await read({limit:100,offset});
    if(owner!==identity())throw Error('账号或权限已变化，请重新读取');
    const items=listItems(result);
    if(expected!==undefined&&expected!==result.total)throw Error('目标目录正在变化，请重新打开编辑');
    expected=result.total;
    for(const row of items){const key=row.row_key||row.id;if(!key||rows.has(key))throw Error('目标目录分页发生变化，请重新打开编辑');rows.set(key,row);}
    if(offset+items.length>=expected)return [...rows.values()];
    if(items.length!==100)throw Error('目标目录返回不完整，请重试');
  }
  throw Error('目标目录较大，请缩小范围后重试');
}
export async function participantDirectory() {
  const owner=identity();
  const [directory,scope]=await Promise.allSettled([get('/directory/members',{permission:'target.read',include_stats:false}),get('/review/targets/options')]);
  if(owner!==identity())throw Error('账号或权限已变化，请重新读取');
  const people=new Map();
  if(directory.status==='fulfilled'){
    if(!Array.isArray(directory.value?.items))throw Error('成员目录响应不完整');
    for(const person of directory.value.items)if(person.id&&person.active!==false)people.set(person.id,person);
  }
  if(scope.status==='fulfilled'){
    if(!Array.isArray(scope.value?.people))throw Error('目标成员目录响应不完整');
    for(const person of scope.value.people)if(person.id)people.set(person.id,{...people.get(person.id),...person});
  }
  if(directory.status==='rejected'&&scope.status==='rejected')throw directory.reason;
  return {items:[...people.values()],teams:directory.status==='fulfilled'&&Array.isArray(directory.value?.teams)?directory.value.teams:[],limited:directory.status==='rejected',data_source:'database'};
}
const save=async(kind,id,body)=>{
  return okrWrite(`${pathFor(kind)}${id?'/'+encodeURIComponent(id):''}`,body,id?'PATCH':'POST',result=>typeof result?.id==='string'&&Number.isInteger(result.version_no));
};
export const reviewOkrApi={
  objectives:params=>get('/review/objectives',params),
  objective:id=>get(`/review/objectives/${encodeURIComponent(id)}`),
  keyResults:params=>get('/review/key-results',params),
  keyResult:id=>get(`/review/key-results/${encodeURIComponent(id)}`),
  metrics:()=>get('/review/metrics'),options:()=>get('/review/targets/options'),participants:participantDirectory,
  objectiveParents:anchor_date=>allPages(params=>get('/review/objectives',{...params,anchor_date})),
  keyResultParents:()=>allPages(params=>get('/review/key-results',params)),
  targetsFor:objective=>allPages(params=>get('/review/targets',{...params,period_type:objective.period_type,anchor_date:objective.period_start,scope:objective.scope_type,
    ...(objective.scope_type==='person'?{user_id:objective.user_ref_id}:objective.scope_type==='team'?{team_id:objective.team_id}:{department_code:objective.department_code})})),
  // Existing directory is only a bounded candidate set; the POST validates each
  // recipient's own object access and refuses the entire send on any 403.
  recipients:async()=> (await participantDirectory()).items,
  saveObjective:(id,body)=>save('objective',id,body),saveKeyResult:(id,body)=>save('key_result',id,body),
  confirmProgress:(id,body)=>okrWrite(`/review/key-results/${encodeURIComponent(id)}/progress`,body,'POST',result=>typeof result?.key_result?.id==='string'&&Number.isInteger(result.key_result.version_no)&&typeof result.progress?.value==='string'),
  mention:(kind,id,body)=>okrWrite(`${pathFor(kind)}/${encodeURIComponent(id)}/mentions`,body,'POST',result=>result?.status==='queued'&&Number.isInteger(result.created)&&result.permission_changed===false),
};
