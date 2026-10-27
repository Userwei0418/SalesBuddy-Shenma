// Separate transfer capability; never infer permission from collaboration/edit rights.
export function createTransferController(id,api){
 let alive=true,seq=0,searchSeq=0,pending=null;
 let s={phase:'idle',access:null,people:[],offset:0,total:0,query:'',directoryLoading:false,directoryError:'',person:null,teamId:'',reason:'',message:'',result:null};
 const listeners=new Set(),set=patch=>{if(alive){s={...s,...patch};listeners.forEach(fn=>fn(s));}};
 const allowed=()=>s.access?.capabilities.can_transfer===true,editable=()=>allowed()&&['ready','conflict'].includes(s.phase);
 async function load(preserve=false,conflict=false){
  if(!alive||['saving','uncertain','done'].includes(s.phase))return;const serial=++seq;++searchSeq;
  set({phase:'loading',access:null,people:[],directoryLoading:false,message:'',...(preserve?{}:{person:null,teamId:'',reason:''})});
  try{const access=await api.access(id);if(!alive||serial!==seq)return;set({access,phase:conflict?'conflict':'ready',message:conflict?'商机已变化，请核对最新主责与团队后重新确认。':''});}
  catch(e){if(alive&&serial===seq)set({phase:'denied',access:null,message:e.message});}
 }
 async function search(query='',more=false){
  if(!editable())return;const serial=++searchSeq,offset=more?s.offset:0;
  set({query,directoryLoading:true,directoryError:'',...(more?{}:{people:[],offset:0,total:0})});
  try{const r=await api.transferCandidates(id,query,offset);if(!alive||serial!==searchSeq)return;set({people:[...new Map([...(more?s.people:[]),...r.items].map(p=>[p.user_id,p])).values()],offset:offset+r.items.length,total:r.total,directoryLoading:false});}
  catch(e){if(!alive||serial!==searchSeq)return;set({directoryLoading:false,directoryError:e.message});if([403,404].includes(e.statusCode)||e.code==='SESSION_CHANGED')set({access:null,phase:'denied'});}
 }
 function choose(userId){if(!editable())return;const person=s.people.find(p=>p.user_id===userId);if(person)set({person,teamId:'',message:''});}
 function edit(key,value){if(!editable())return;if(key==='reason')set({reason:value,message:''});if(key==='teamId'&&s.person?.teams.some(t=>t.team_id===value))set({teamId:value,message:''});}
 async function save(){
  if(!alive||!allowed()||!['ready','uncertain'].includes(s.phase))return;
  if(s.phase==='ready'){
   const reason=s.reason.trim();if(!s.person||!s.person.teams.some(t=>t.team_id===s.teamId)||!reason||[...reason].length>1000){set({message:'请选择接手销售、明确所属团队，并填写 1–1000 字转交原因。'});return;}
   pending={version_no:s.access.version_no,new_owner_user_ref_id:s.person.user_id,new_owner_team_id:s.teamId,reason};
  }
  if(!pending)return;set({phase:'saving',message:'',directoryLoading:false});++searchSeq;
  try{
   const result=await api.transfer(id,pending);if(!alive)return;pending=null;set({phase:'done',result,message:result.changed?'商机主责已转交':'主责与团队未变化'});
   try{const access=await api.access(id);if(alive)set({access});}catch(e){if(alive)set({access:null,message:`${result.changed?'转交已完成':'未发生变更'}；${[403,404].includes(e.statusCode)?'当前账号已无权继续查看此商机。':'后续访问状态暂未读回，请重新打开核对。'}`});}
  }catch(e){
   if(!alive)return;
   if(e.code==='SESSION_CHANGED'){pending=null;set({phase:'denied',access:null,person:null,teamId:'',reason:'',people:[],message:e.message});return;}
   if([403,404].includes(e.statusCode)){pending=null;set({phase:'denied',access:null,message:'转交权限已变化，草稿保留，请重新读取商机。'});return;}
   if(e.statusCode===409&&!String(e.serverCode||'').startsWith('IDEMPOTENCY_')){pending=null;set({phase:'ready'});await load(true,true);return;}
   if([400,422].includes(e.statusCode)){pending=null;set({phase:'ready',message:e.message});return;}
   set({phase:'uncertain',message:'尚未确认转交结果。请重试本次转交，系统会核对同一次请求。'});
  }
 }
 return {getState:()=>s,subscribe:fn=>{listeners.add(fn);return()=>listeners.delete(fn);},load,search,choose,edit,save,
  acknowledge(){if(s.phase==='conflict'&&allowed())set({phase:'ready',message:'已核对最新版本，请再次确认目标销售和团队。'});},
  dispose(){alive=false;++seq;++searchSeq;pending=null;listeners.clear();s={...s,access:null,people:[],person:null,teamId:'',reason:''};}};
}
