const api = require('../../utils/apiClient');
const { identity } = require('../../utils/access');
const opportunityAmount = require('../../utils/opportunityAmount');
const { validOpportunityReceipt } = require('../../utils/requestIdentity');
const writes = require('../../utils/pageWriteContext').createPageWriteContext(()=>getApp().globalData.session);
Page({
 data: { customerId:'', customerName:'', existing:null, busy:false, loading:false, error:'', query:'', customers:[],
   customerSearchLoading:false,customerSearchError:'',customerSearchDone:false,
   saveStatus:'idle',saveRecoveryMessage:'',saveRecoveryOpportunityId:'',saveRecoveryAction:'',canRetrySave:false },
 onLoad(o) {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'opportunity-create', o)) return; if (!getApp().ensureLogin()) return; this.loadedIdentity=identity(getApp().globalData.session); this.opportunityId=o.opportunityId || ''; this.setData({customerId:o.customerId || ''}); this.restoreSaveState(); if(this.data.customerId) this.load(); },
 onShow() {
   this.writeHidden=false;
   this.restoreSaveState();
   if (this.loadedIdentity !== undefined && this.loadedIdentity !== identity(getApp().globalData.session)) {
     clearTimeout(this.timer);this.searchSerial=(this.searchSerial||0)+1;
     this.loadedIdentity=identity(getApp().globalData.session);
     this.setData({query:'',customers:[],customerSearchLoading:false,customerSearchError:'',customerSearchDone:false});
     if (getApp().guardPage && !getApp().guardPage(this, 'opportunity-create')) return;
     if (this.data.customerId) return this.load();
   }
 },
 load() {
   const customerId=this.data.customerId, opportunityId=this.opportunityId;
   const context=identity(getApp().globalData.session), serial=this.loadSerial=(this.loadSerial||0)+1;
   this.loadedIdentity=context;
   this.writeIdentity=context;
   const current=()=>!this.closed && serial===this.loadSerial && customerId===this.data.customerId
     && opportunityId===this.opportunityId && context===identity(getApp().globalData.session);
   this.setData({loading:true,busy:!!(this.pageWrites && this.pageWrites.save && this.pageWrites.save.current()),error:'',customerName:'',existing:null});
   const request=opportunityId ? api.getOpportunityDetailOverview(opportunityId) : api.getCustomerReference(customerId);
   return request.then(c=>{
     if(!current()) return;
     const row=opportunityId ? c.primary_opportunity : null;
     if(c.id!==customerId || (opportunityId && (!row || row.id!==opportunityId))) throw new Error('商机不存在或无权查看');
     this.setData({customerName:c.name,existing:row});
     if(!this.writeHidden)wx.setNavigationBarTitle({title:row ? '修改商机' : '新增商机'});
   }).catch(e=>{if(current()) this.setData({error:e.message || '商机信息加载失败'});})
     .finally(()=>{if(current()) this.setData({loading:false});});
 },
 inputCustomer(e) {
   if(this.saveLocked() || this.data.customerId)return;
   const q=typeof e.detail.value==='string' ? [...e.detail.value].slice(0,100).join('') : '';
   const serial=this.searchSerial=(this.searchSerial||0)+1;
   const context=identity(getApp().globalData.session);
   this.customerSearchIdentity=context;
   this.setData({query:q,customers:[],customerSearchLoading:!!q.trim(),customerSearchError:'',customerSearchDone:false}); clearTimeout(this.timer);
   if(!q.trim()) return;
   const current=()=>!this.closed && serial===this.searchSerial && !this.data.customerId
     && context===identity(getApp().globalData.session);
   this.timer=setTimeout(()=>{
     if(!current()) return;
     api.listOpportunityCustomerReferences({q:q.trim(),pageSize:20}).then(r=>{
       if(!current())return;
       if(!r || !Array.isArray(r.items))throw Error('客户搜索结果暂不可用，请重试');
       this.setData({customers:r.items,customerSearchLoading:false,customerSearchDone:true});
     }).catch(e=>{if(current()) this.setData({customerSearchLoading:false,customerSearchError:e.message || '客户搜索失败，请重试',customerSearchDone:false});});
   },250);
 },
 retryCustomerSearch(){return this.inputCustomer({detail:{value:this.data.query}});},
 selectCustomer(e) {
   if(this.saveLocked())return;
   if(this.customerSearchIdentity!==undefined && this.customerSearchIdentity!==identity(getApp().globalData.session))return;
   clearTimeout(this.timer);this.searchSerial=(this.searchSerial||0)+1;
   this.setData({customerId:e.currentTarget.dataset.id,customers:[],customerSearchLoading:false,customerSearchError:'',customerSearchDone:false}); return this.load();
 },
 onHide(){this.writeHidden=true;},
 onUnload(){this.unloaded=true;this.closed=true;this.loadSerial=(this.loadSerial||0)+1;this.searchSerial=(this.searchSerial||0)+1;clearTimeout(this.timer);},
 saveLocked(){return ['saving','unknown','checking','succeeded'].includes(this.data.saveStatus);},
 restoreSaveState(){
   const owner=identity(getApp().globalData.session);
   if(this.saveOwner!==undefined&&this.saveOwner!==owner)this.setData({saveStatus:'idle',saveRecoveryMessage:'',saveRecoveryOpportunityId:'',saveRecoveryAction:'',canRetrySave:false});
   this.saveOwner=owner;
   if(['saving','checking','succeeded'].includes(this.data.saveStatus))return this.saveLocked();
   try {
     const pending=api.getPendingOpportunitySave(this.data.customerId,this.opportunityId||'');
     if(pending){
       this.opportunityId=pending.opportunityId;
       this.setData({customerId:pending.customerId,saveStatus:'unknown',saveRecoveryMessage:'保存结果待确认，请查询原提交结果。确认前不能修改或再次新建。',
         canRetrySave:api.canRetryOpportunitySave(pending.customerId,pending.opportunityId)});
       return true;
     }
   } catch(e){this.setData({saveStatus:'unknown',saveRecoveryMessage:e.message,canRetrySave:false});return true;}
   return this.saveLocked();
 },
 acceptSaveResult(r,write){
   if(!validOpportunityReceipt(r,{opportunityId:this.opportunityId||'',customerId:this.data.customerId}))throw new Error('未收到完整保存回执，请查询原保存结果，勿重复创建');
   if(!write.current())return;
   this.setData({existing:{...r},error:'',saveStatus:'succeeded',saveRecoveryOpportunityId:r.id,saveRecoveryMessage:'已确认保存成功，可打开原商机。',canRetrySave:false});
   wx.setStorageSync('pendingOpenCustomerId',this.data.customerId);wx.setStorageSync('pendingOpenOpportunityId',r.id);
   if(write.visible())wx.showToast({title:r.changed?'商机已保存':'内容未变化',icon:r.changed?'success':'none'});
   setTimeout(()=>{if(write.settledVisible())wx.navigateBack();},700);
 },
 openSavedOpportunity(){
   if(this.data.saveStatus!=='succeeded'||!this.data.saveRecoveryOpportunityId||this.saveOwner!==identity(getApp().globalData.session)||this.writeHidden||this.unloaded)return;
   wx.setStorageSync('pendingOpenCustomerId',this.data.customerId);wx.setStorageSync('pendingOpenOpportunityId',this.data.saveRecoveryOpportunityId);
   wx.switchTab({url:'/pages/customers/index'});
 },
 async recoverSave(mode){
   if(this.data.busy||this.data.loading||['saving','checking','succeeded'].includes(this.data.saveStatus))return;
   if(!this.restoreSaveState()||this.data.saveStatus!=='unknown')return;
   const write=writes.begin(this,'save',()=>[this.data.customerId,this.opportunityId||'']);if(!write)return;
   this.setData({busy:true,saveStatus:mode==='retry'?'saving':'checking',saveRecoveryAction:mode==='check'?'query':mode,error:''});
   try {
     const response=mode==='retry'?{status:'committed',result:await api.retryOpportunitySave(this.data.customerId,this.opportunityId||'')}
       :await (mode==='cancel'?api.cancelOpportunitySave:api.checkOpportunitySaveResult)(this.data.customerId,this.opportunityId||'');
     if(!write.current())return;
     if(response.status==='committed')this.acceptSaveResult(response.result,write);
     else if(response.status==='cancelled')this.setData({saveStatus:'rejected',saveRecoveryMessage:'已安全终止原提交，可以修改后重新保存。',canRetrySave:false});
     else this.setData({saveStatus:'unknown',saveRecoveryMessage:'暂未查到可确认的保存结果，不能据此认定未保存。请稍后查询；如持续未确定，请联系管理员核对。'});
   } catch(e){if(write.current())this.setData({saveStatus:'unknown',saveRecoveryMessage:e.message||'尚未确认原保存结果，请稍后查询'});}
   finally{if(write.current())this.setData({saveRecoveryAction:''});write.finish('busy');}
 },
 checkSaveResult(){return this.recoverSave('check');},
 retrySave(){return this.recoverSave('retry');},
 async cancelSave(){
   if(this.data.busy||this.data.saveStatus!=='unknown')return;
   const context=writes.capture(this,()=>[this.data.customerId,this.opportunityId||'']);
   const result=await new Promise(resolve=>wx.showModal({title:'结束未完成的提交',content:'若原提交已保存，将打开已保存的商机；若尚未保存，服务器会终止这次提交。仅在收到确认后才可重新填写。',confirmText:'确认结束',success:resolve,fail:()=>resolve({confirm:false})}));
   if(result.confirm&&context.visible())return this.recoverSave('cancel');
 },
 // BACKEND-CONTRACT 新增和修改均 POST /api/v1/customers/{id}/opportunities，action 区分。
 // 请求 amount/quarterly_forecasts 金额为元；更新携带 opportunity_id/version_no。
 // 必须返回 changed:boolean + version_no，发生变化还须 event_id；缺少回执时可能已保存，需读取核对。
 // 详见 docs/backend-handoff/客户与商机详解.md「商机表单与保存」。
 async submit() {
   if(this.data.busy || this.data.loading) return;
   if(this.restoreSaveState())return;
   const write=writes.begin(this,'save',()=>[this.data.customerId,this.opportunityId || '']);if(!write)return;
   const customerId=this.data.customerId;
   this.setData({busy:true,error:'',saveStatus:'saving'});
   let submitted=false;
   try {
     const form=this.selectComponent('#opportunityForm');
     const signature=()=>JSON.stringify([form.data && form.data.form,this.data.existing]);
     const before=signature();
     const prepared=await form.prepare();
     if(!write.visible())return;
     if(before!==signature())throw Error('商机内容已变化，请重新保存');
     const assessment=await opportunityAmount.assess(api,prepared,this.data.existing);
     if(!write.visible())return;
     if(before!==signature())throw Error('商机内容已变化，请重新保存');
     if(assessment.required){
       const result=await new Promise(resolve=>wx.showModal({title:'请核对商机金额',content:assessment.content,confirmText:'金额正确',cancelText:'返回修改',success:resolve,fail:()=>resolve({confirm:false})}));
       if(!write.visible() || !result.confirm)return;
       if(before!==signature())throw Error('商机内容已变化，请重新保存');
     }
     const payload=opportunityAmount.acknowledged(assessment);
     submitted=true;
     const r=await api.createOpportunity(customerId,payload);
     if(!write.current())return;
     this.acceptSaveResult(r,write);
   } catch(e) {
     if(write.current()){
       const unknown=submitted&&e.opportunitySaveUnknown!==false&&(![400,403,404,409,422].includes(e.statusCode)||e.opportunitySaveUnknown===true);
       this.setData({error:e.message||'保存失败，请重试',saveStatus:unknown?'unknown':submitted?'rejected':'idle',
         saveRecoveryMessage:unknown?'保存结果待确认，请先查询原提交结果，勿重复创建。':'',
         canRetrySave:unknown&&api.canRetryOpportunitySave(customerId,this.opportunityId||'')});
     }
   }
   finally {if(write.current()&&this.data.saveStatus==='saving')this.setData({saveStatus:'idle'});write.finish('busy');}
 }
});
