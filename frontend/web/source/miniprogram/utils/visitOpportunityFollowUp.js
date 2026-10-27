const contract=require('./opportunityAccess');
const access=require('./access');
const reference=require('./opportunityFollowUpReference');
// Minimal recording targets never carry customer/opportunity edit permissions.
const companyCustomer=session=>access.can(session,'visit.company_customer_follow_up');
const companyOpportunity=session=>access.can(session,'visit.company_opportunity_follow_up');
function create(api,session){return {
 cancelFollowUpSearch(){clearTimeout(this.followUpSearchTimer);this.followUpSearchTimer=null;this.followUpSearchSeq=(this.followUpSearchSeq||0)+1;},
 async verifyFollowUpTarget(){
  if(!this.data.opportunityId || this.data.opportunityId==='__new__' || !companyOpportunity(session()))return true;
  const id=this.data.opportunityId,customerId=this.data.customerId,identity=access.identity(session()),seq=this.followUpSeq=(this.followUpSeq||0)+1;
  const current=()=>!this.closed&&!this.unloaded&&seq===this.followUpSeq&&id===this.data.opportunityId&&customerId===this.data.customerId&&identity===access.identity(session());
  this.setData({followUpChecking:true});
  try {reference.read(await api.getOpportunityFollowUpAccess(id),{opportunityId:id,customerId});return current();}
  catch(error){if(current()){if(this.fail)this.fail(error.message);else this.setData({errorText:error.message});}return false;}
  finally{if(seq===this.followUpSeq&&!this.closed&&!this.unloaded)this.setData({followUpChecking:false});}
 },
 async refreshFollowUp(){
  const id=this.data.opportunityId,customerId=this.data.customerId,targetKind=id?'opportunity':'customer',identity=access.identity(session()),seq=this.followUpSeq=(this.followUpSeq||0)+1;
  const current=()=>!this.closed&&!this.unloaded&&seq===this.followUpSeq&&id===this.data.opportunityId&&customerId===this.data.customerId&&identity===access.identity(session());
  if(!id && (!customerId || !companyCustomer(session()))){this.setData({followUpResolved:false,opportunityResolved:false,canSubmit:false,opportunityResolveError:'请选择本次跟进的客户或商机'});return false;}
  this.setData({targetKind,followUpChecking:true,followUpResolved:false,opportunityResolved:false,opportunityResolveError:'正在核对跟进权限',canSubmit:false});
  try{
   const company=companyOpportunity(session());
   const value=targetKind==='customer'
    ? reference.customerAccess(await api.getCustomerFollowUpAccess(customerId),{customerId})
    : company?reference.read(await api.getOpportunityFollowUpAccess(id),{opportunityId:id,customerId:customerId||undefined}):contract.read(await api.getOpportunityAccess(id),{opportunityId:id,customerId:customerId||undefined});
   if(!current())return false;
   if(!(targetKind==='customer'||company?value.can_follow_up:value.capabilities.can_follow_up))throw Error('当前已无权跟进该目标，请保留草稿并重新选择');
   const customer=value.customer_reference,selected=targetKind==='opportunity'?{id:value.opportunity_id,name:value.name,customer_id:customer.id,capabilities:{can_edit:false}}:null;
   this.customerReferencePending=false;this.customerReferenceInvalid=false;
   this.setData({followUpAccess:value,followUpResolved:true,customerId:customer.id,customerName:customer.name,customerQuery:customer.name,customerInitial:customer.name.slice(0,1),customerConfirmed:true,
    opportunityName:selected?selected.name:'',opportunityResolved:true,selectedOpportunity:selected,opportunityOptions:selected?[selected]:[{id:'',name:'不关联商机'}],opportunityIndex:0,opportunityResolveError:'',opportunityError:'',errorText:'',
    isFirstVisit:false,opportunityDraft:null,opportunityDrafts:{},opportunityPendingId:'',opportunityPendingOption:null,opportunityEditing:false,opportunityAIRecognized:false,opportunityAIHint:''});
   if(this.entryReady)this.setData({canSubmit:this.entryReady(this.data.transcript)});
   if(this.refresh)this.refresh();return true;
  }catch(error){if(current()){this.customerReferenceInvalid=true;this.setData({followUpAccess:null,followUpResolved:false,opportunityResolved:false,opportunityResolveError:error.message||'跟进权限读取失败',opportunityError:error.message||'跟进权限读取失败',errorText:error.message||'跟进权限读取失败',canSubmit:false});if(this.refresh)this.refresh();}return false;
  }finally{if(!this.closed&&!this.unloaded&&seq===this.followUpSeq&&identity===access.identity(session()))this.setData({followUpChecking:false});}
 },
 async openFollowUpDiscovery(){
  if(this.data.isProcessing||this.data.isRecording)return;
  this.cancelFollowUpSearch();
  const customer=companyCustomer(session()),opportunity=companyOpportunity(session());
  this.setData({followUpPickerOpen:true,followUpCompanyPicker:customer||opportunity,followUpCanCompanyCustomer:customer,followUpCanCompanyOpportunity:opportunity,
   followUpCanCustomerOnly:access.can(session(),'visit.create'),followUpPickerMode:customer||opportunity?'customers':'opportunities',followUpCustomerId:'',followUpCustomerName:'',followUpQuery:'',followUpItems:[],followUpOffset:0,followUpMore:false,followUpLoading:false,followUpSearchPending:false});
  return this.searchFollowUp();
 },
 changeFollowUpPickerMode(e){
  if(this.data.isProcessing||this.data.isRecording||this.data.followUpChecking)return;
  const mode=e.currentTarget.dataset.mode==='opportunities'?'opportunities':'customers';
  if(mode==='opportunities'&&!companyOpportunity(session()) || mode==='customers'&&!companyCustomer(session())&&!companyOpportunity(session()))return;
  this.cancelFollowUpSearch();
  this.setData({followUpPickerMode:mode,followUpCustomerId:'',followUpCustomerName:'',followUpQuery:'',followUpItems:[],followUpOffset:0,followUpMore:false,followUpLoading:false,followUpSearchPending:false});return this.searchFollowUp();
 },
 clearFollowUpSelection(customer){
  this.customerLoadSerial=(this.customerLoadSerial||0)+1;this.searchSerial=(this.searchSerial||0)+1;this.followUpSeq=(this.followUpSeq||0)+1;
  this.customerReferencePending=false;this.customerReferenceInvalid=false;this.aiOpportunitySuggestion=null;
  this.setData({followUpOnly:true,targetKind:'customer',followUpResolved:false,customerId:customer?customer.id:'',customerName:customer?customer.name:'',customerQuery:customer?customer.name:'',customerInitial:customer?customer.name.slice(0,1):'',customerConfirmed:Boolean(customer),
   opportunityId:'',opportunityName:'',opportunityResolved:false,selectedOpportunity:null,opportunityOptions:[],opportunityIndex:0,followUpAccess:null,followUpChecking:false,canSubmit:false,
   isFirstVisit:false,opportunityRecoveryPending:false,opportunityRecoveryUnknown:false,opportunityEditing:false,opportunityDraft:null,opportunityDrafts:{},opportunityPendingId:'',opportunityPendingOption:null,opportunityAIRecognized:false,opportunityAIHint:'',opportunityResolveError:'请确认本次跟进目标',opportunityError:'',errorText:''});
  if(this.persist)this.persist();
 },
 returnToCustomerFollowUp(){
  if(this.data.isProcessing||this.data.isRecording||this.data.followUpChecking||!access.can(session(),'visit.create')||!this.searchDepartmentCustomers)return;
  this.clearFollowUpSelection();this.closeFollowUpDiscovery();this.setData({followUpOnly:false,opportunityResolveError:''});this.persist();return this.searchDepartmentCustomers();
 },
 async chooseFollowUpCustomer(e){
  if(this.data.isProcessing||this.data.isRecording||this.data.followUpChecking||this.data.followUpPickerMode!=='customers')return;
  const customer=(this.data.followUpItems||[]).find(row=>row.id===e.currentTarget.dataset.id);if(!customer)return;
  this.cancelFollowUpSearch();
  this.setData({followUpLoading:false,followUpSearchPending:false});
  // Web can offer the customer's opportunities before confirming a target.
  // Mini keeps its existing direct-customer selection unless explicitly opted in.
  if(e.currentTarget.dataset.showOpportunities && companyOpportunity(session()))return this.openCustomerFollowUpOpportunities(customer);
  this.clearFollowUpSelection(customer);
  if(companyCustomer(session())){
   if(await this.refreshFollowUp()){this.closeFollowUpDiscovery();this.persist();}return;
  }
  return this.openCustomerFollowUpOpportunities();
 },
 async openCustomerFollowUpOpportunities(customer){
  const selected=customer&&customer.id?customer:{id:this.data.customerId,name:this.data.customerName};
  if(this.data.isProcessing||this.data.isRecording||this.data.followUpChecking||!selected.id||!companyOpportunity(session()))return;
  this.cancelFollowUpSearch();
  this.setData({followUpPickerOpen:true,followUpCanCompanyCustomer:companyCustomer(session()),followUpCanCompanyOpportunity:true,followUpCompanyPicker:true,
   followUpPickerMode:'opportunities',followUpCustomerId:selected.id,followUpCustomerName:selected.name,followUpQuery:'',followUpItems:[],followUpOffset:0,followUpMore:false,followUpLoading:false,followUpSearchPending:false});return this.searchFollowUp();
 },
 async confirmFollowUpCustomer(){
  if(this.data.isProcessing||this.data.isRecording||this.data.followUpChecking||!this.data.followUpPickerOpen||!this.data.followUpCustomerId||!companyCustomer(session()))return;
  this.cancelFollowUpSearch();
  this.setData({followUpLoading:false,followUpSearchPending:false});
  this.clearFollowUpSelection({id:this.data.followUpCustomerId,name:this.data.followUpCustomerName});
  if(await this.refreshFollowUp()){this.closeFollowUpDiscovery();this.persist();}
 },
 async clearFollowUpOpportunity(){
  if(this.data.isProcessing||this.data.isRecording||this.data.followUpChecking||!companyCustomer(session()))return;
  this.clearFollowUpSelection({id:this.data.customerId,name:this.data.customerName});
  if(await this.refreshFollowUp())this.persist();
 },
 closeFollowUpDiscovery(){this.cancelFollowUpSearch();this.setData({followUpPickerOpen:false,followUpLoading:false,followUpSearchPending:false});},
 inputFollowUpQuery(e){
  this.cancelFollowUpSearch();
  const autoSearch=Boolean(e.detail.autoSearch),seq=this.followUpSearchSeq,identity=access.identity(session());
  this.setData({followUpQuery:e.detail.value,followUpItems:[],followUpOffset:0,followUpMore:false,followUpLoading:false,followUpError:'',followUpSearchPending:autoSearch});
  if(autoSearch)this.followUpSearchTimer=setTimeout(()=>{
   this.followUpSearchTimer=null;
   if(!this.closed&&!this.unloaded&&this.data.followUpPickerOpen&&seq===this.followUpSearchSeq&&identity===access.identity(session()))this.searchFollowUp();
  },300);
 },
 async searchFollowUp(e){
  clearTimeout(this.followUpSearchTimer);this.followUpSearchTimer=null;
  if(this.data.followUpLoading)return;const more=!!(e&&e.currentTarget&&e.currentTarget.dataset.more),offset=more?this.data.followUpOffset:0,identity=access.identity(session()),seq=this.followUpSearchSeq=(this.followUpSearchSeq||0)+1;
  const current=()=>!this.closed&&!this.unloaded&&this.data.followUpPickerOpen&&seq===this.followUpSearchSeq&&identity===access.identity(session());
  this.setData({followUpLoading:true,followUpSearchPending:false,followUpError:''});
  try{
   const customer=companyCustomer(session()),company=companyOpportunity(session()),options={q:String(this.data.followUpQuery||'').trim(),limit:20,offset,order:'recent_follow_up'};
   const customerMode=(customer||company)&&this.data.followUpPickerMode==='customers',customerId=company?this.data.followUpCustomerId:'';
   const result=customerMode?reference.customers(await (customer?api.listCompanyCustomerFollowUpOptions(options):api.listCompanyFollowUpCustomers(options)),{offset,limit:20})
    :company?reference.page(await api.listCompanyOpportunityFollowUpOptions({...options,...(customerId?{customer_id:customerId}:{})}),{offset,limit:20,customerId:customerId||undefined})
    :contract.page(await api.listOpportunityFollowUpOptions(options),{offset,limit:20});
   if(!current())return;
   const key=customerMode?'id':'opportunity_id',rows=more?this.data.followUpItems.slice():[],ids=new Set(rows.map(r=>r[key]));for(const row of result.items)if(!ids.has(row[key])){rows.push(row);ids.add(row[key]);}
   this.setData({followUpItems:rows,followUpOffset:result.nextOffset,followUpMore:result.hasMore});
  }catch(error){if(current())this.setData({followUpError:error.message||'跟进目录读取失败'});}finally{if(current())this.setData({followUpLoading:false});}
 },
 async chooseFollowUp(e){
  if(this.data.isProcessing||this.data.isRecording||this.data.followUpChecking)return;
  const row=(this.data.followUpItems||[]).find(r=>r.opportunity_id===e.currentTarget.dataset.id);if(!row)return;
  this.cancelFollowUpSearch();
  this.setData({followUpLoading:false,followUpSearchPending:false});
  this.clearFollowUpSelection(row.customer_reference);
  this.setData({followUpOnly:true,targetKind:'opportunity',customerId:row.customer_reference.id,opportunityId:row.opportunity_id,opportunityName:row.name,customerConfirmed:false,opportunityResolved:false,canSubmit:false});
  if(await this.refreshFollowUp()){this.closeFollowUpDiscovery();this.persist();}
 }
};}
module.exports={create};
