const api=require('../../utils/apiClient'),access=require('../../utils/access'),contract=require('../../utils/opportunityAccess');
const uuid=v=>typeof v==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v);
Page({
 data:{id:'',record:null,loading:false,busy:false,error:'',query:'',items:[],more:false,offset:0,target:'',team:'',teams:[],reason:'',unknown:false,result:''},
 onLoad(options){this.setData({id:options.id||''});},
 onShow(){if(!getApp().ensureLogin())return;this.visible=true;this.setData({busy:false});const identity=access.identity(getApp().globalData.session);if(this.identity!==identity){this.seq=(this.seq||0)+1;this.identity=identity;this.pending=null;this.pendingKey='';const session=getApp().globalData.session;this.storageKey='opportunityTransfer:'+JSON.stringify([api.getBaseUrl(),session.workspaceId,session.userId,this.data.id]);this.setData({record:null,target:'',team:'',teams:[],reason:'',items:[],unknown:false,busy:false,result:''});}try{const saved=wx.getStorageSync(this.storageKey);if(saved&&uuid(saved.key)&&saved.body){this.pending=saved.body;this.pendingKey=saved.key;this.setData({unknown:true,reason:saved.body.reason});}}catch(error){this.setData({error:'原转交请求读取失败，请勿新建转交'});return;}return this.load();},
 onHide(){this.visible=false;this.seq=(this.seq||0)+1;this.searchSeq=(this.searchSeq||0)+1;},onUnload(){this.onHide();},
 current(identity,id){return this.visible&&identity===access.identity(getApp().globalData.session)&&id===this.data.id;},
 async load(){
  this.searchSeq=(this.searchSeq||0)+1;this.setData({candidateLoading:false});
  if(!uuid(this.data.id)){this.setData({error:'商机标识无效'});return;}
  const identity=this.identity,id=this.data.id,seq=this.seq=(this.seq||0)+1;this.setData({loading:true,error:''});
  try{const record=contract.read(await api.getOpportunityAccess(id),{opportunityId:id});if(!this.current(identity,id)||seq!==this.seq)return;
   this.setData({record,target:'',team:'',teams:[],items:[],offset:0,more:false});
   if(record.capabilities.can_transfer&&record.status==='open'&&!this.pending)await this.search();
  }catch(error){if(this.current(identity,id)&&seq===this.seq)this.setData({record:null,error:error.message||'权限读取失败'});}finally{if(this.current(identity,id)&&seq===this.seq)this.setData({loading:false});}
 },
 input(e){if(this.data.busy||this.pending)return;const key=e.currentTarget.dataset.key;if(key==='query'){this.searchSeq=(this.searchSeq||0)+1;this.setData({items:[],more:false,candidateLoading:false});}if(['query','reason'].includes(key))this.setData({[key]:e.detail.value});},
 async search(e){
  if(!this.data.record||!this.data.record.capabilities.can_transfer||this.pending||this.data.busy)return;
  const identity=this.identity,id=this.data.id,seq=this.searchSeq=(this.searchSeq||0)+1,offset=e&&e.currentTarget&&e.currentTarget.dataset.more?this.data.offset:0;
  this.setData({candidateLoading:true,error:''});
  try{const r=await api.getOpportunityTransferCandidates(id,{q:this.data.query.trim(),limit:20,offset});if(!this.current(identity,id)||seq!==this.searchSeq)return;
   if(!r||!Array.isArray(r.items)||r.items.length>20||!Number.isSafeInteger(r.total)||r.total<0||r.items.some(x=>!uuid(x.user_id)||typeof x.display_name!=='string'||!Array.isArray(x.teams)||x.teams.some(t=>!uuid(t.team_id)||typeof t.name!=='string')))throw Error('候选信息不完整');
   const rows=offset?this.data.items.slice():[],seen=new Set(rows.map(x=>x.user_id));for(const row of r.items)if(!seen.has(row.user_id)){rows.push(row);seen.add(row.user_id);}
   this.setData({items:rows,more:r.items.length===20&&offset+r.items.length<r.total,offset:offset+r.items.length});
  }catch(error){if(this.current(identity,id))this.setData({error:error.message||'候选读取失败'});}finally{if(this.current(identity,id)&&seq===this.searchSeq)this.setData({candidateLoading:false});}
 },
 choose(e){if(this.data.busy||this.pending)return;const row=this.data.items.find(x=>x.user_id===e.currentTarget.dataset.id);if(row)this.setData({target:row.user_id,targetName:row.display_name,team:'',teams:row.teams});},
 chooseTeam(e){if(this.data.busy||this.pending)return;const team=this.data.teams.find(x=>x.team_id===e.currentTarget.dataset.id);if(team)this.setData({team:team.team_id,teamName:team.name});},
 confirm(){
  if(this.data.busy||this.data.loading||!this.data.record||!this.data.record.capabilities.can_transfer||this.data.record.status!=='open')return;
  if(this.pending)return this.send();
  const reason=this.data.reason.trim();if(!this.data.target||!this.data.team||!reason||[...reason].length>1000){this.setData({error:'请明确选择接手人、目标团队并填写 1–1000 字原因'});return;}
  const identity=this.identity,id=this.data.id,body={version_no:this.data.record.version_no,new_owner_user_ref_id:this.data.target,new_owner_team_id:this.data.team,reason};
  wx.showModal({title:'确认转交商机主责？',content:`接手人：${this.data.targetName}\n团队：${this.data.teamName}\n仅转交该商机，不更改客户归属和任务责任。`,success:r=>{if(r.confirm&&this.current(identity,id)&&!this.data.busy){const key=require('../../utils/taskChecklist').id();try{wx.setStorageSync(this.storageKey,{key,body});const saved=wx.getStorageSync(this.storageKey);if(!saved||saved.key!==key)throw Error('persist');}catch(error){this.setData({error:'无法保存转交标识，尚未发送请求'});return;}this.pending=body;this.pendingKey=key;this.send();}}});
 },
 async send(){
  if(!this.pending||this.data.busy)return;const body=this.pending,identity=this.identity,id=this.data.id;this.setData({busy:true,error:''});
  try{const r=await api.transferOpportunity(id,body,this.pendingKey);if(!this.current(identity,id))return;
   if(!r||typeof r.changed!=='boolean'||!Number.isSafeInteger(r.version_no)||!r.owner||!uuid(r.owner.user_id)||!uuid(r.owner.team_id)||r.owner.user_id!==body.new_owner_user_ref_id||r.owner.team_id!==body.new_owner_team_id||!['pending','disabled','not_configured','unchanged'].includes(r.sync_status))throw Error('回执不完整，结果待核对');
   wx.removeStorageSync(this.storageKey);this.pending=null;this.pendingKey='';this.setData({unknown:false,result:r.changed?'商机主责已转交':'主责未变化',syncStatus:({pending:'外部同步已排队，尚未确认完成',disabled:'外部同步已禁用',not_configured:'未配置外部同步',unchanged:'无外部同步变更'})[r.sync_status],reason:'',target:'',team:'',record:null});await this.load();
  }catch(error){if(this.current(identity,id)){
   if(error.statusCode===409){wx.removeStorageSync(this.storageKey);this.pending=null;this.pendingKey='';this.setData({unknown:false,target:'',team:''});await this.load();this.setData({error:'转交状态或版本冲突，已重新读取；请重新选择并人工确认'});}
   else if([403,404,422].includes(error.statusCode)){wx.removeStorageSync(this.storageKey);this.pending=null;this.pendingKey='';this.setData({unknown:false,record:null,error:error.message||'当前转交不可用'});}
   else this.setData({unknown:true,error:'转交结果尚未确认。保留原内容，可用同一请求重试；请勿重复发起新转交。'});
  }}finally{if(this.current(identity,id))this.setData({busy:false});}
 }
});
