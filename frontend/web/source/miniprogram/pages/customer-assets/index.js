const {DetailReadSession,pageStates}=require('../../utils/detailReadSession');
const {IntentHistoryReader,emptyHistory}=require('../../utils/opportunityIntentHistory');
const {opportunityAdvicePool:advicePool}=require('../../utils/advicePool');
const {businessTimeLabel}=require('../../utils/customerAdvice');
const {adviceAllowed,adviceState}=require('../../utils/businessAdviceState');
const {TABS,opportunityContext} = require('../../utils/opportunityAdvice');
const { loadQuarterActuals, quarterActualDisplay } = require('../../utils/opportunityQuarterActuals');
const api = require('../../utils/apiClient');
const access = require('../../utils/access');
const finance = require('../../utils/financialDisplay');
const writes = require('../../utils/pageWriteContext').createPageWriteContext(()=>getApp().globalData.session);
const {amountWan} = require('../../utils/customerMap');
function assetTotal(value){const n=finance.number(value);return {totalText:n===null?'未登记':n>=10000?amountWan(n):n.toLocaleString('zh-CN',{maximumFractionDigits:2}),totalUnit:n===null?'':n>=10000?'万元':'元'};}
const {normalizeCustomerDetail} = require('../../utils/customerDetail');
const {visitDetailUrl} = require('../../utils/visitNavigation');
function today() {const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;}
function dateTime(value) {if(!value)return '未记录';const d=new Date(value);if(Number.isNaN(d.getTime()))return String(value);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;}
function statusText(value) {return ({open:'推进中',won:'已赢单',lost:'已丢单'})[value]||value||'未记录';}
function opportunityAdviceView(value,customerId,opportunityId,section,analysisId,allowEmpty=false){
  return adviceState(value,'opportunity',opportunityId,section,{customerId,opportunityId,analysisId,allowEmpty});
}
Page({
  onFdeVisitSegment(e){return this.changeFdeVisitMode({currentTarget:{dataset:{mode:e.detail.value}}});},
  onOpportunityTabChange(e){return this.selectOpportunityTab({currentTarget:{dataset:{tab:e.detail.key}}});},
  onPeriodSegment(e){this.changePeriod({currentTarget:{dataset:{period:e.detail.value}}});},
  onKindSegment(e){this.changeKind({currentTarget:{dataset:{kind:e.detail.value}}});},

  data:{kindOptions:[{value:'recognized',label:'确收'},{value:'collection',label:'回款'}],fdeVisitOptions:[{value:'self',label:'我的拜访记录'},{value:'all',label:'全部跟进'}],periodOptions:[{value:'year',label:'本年'},{value:'all',label:'历年累计'}],demos:[],demoTotal:0,demosLoading:false,demosMore:false,demoError:'',progressEventsExpanded:false,basis:'effective',financialHints:[],quarterFinancial:null,quarterActualHint:'',historicalCount:0,period:'year',kind:'recognized',customerId:'',customerName:'',opportunityId:'',teamId:'',ownerId:'',
    items:[],loading:true,error:'',summary:null,totalText:'—',canManage:false,hasMore:false,asOf:today(),
    originCustomerId:'',originOpportunityId:'',
    quarterActualLoading:false,quarterActualError:'',quarterActualOptions:[],quarterActualIndex:0,quarterCollection:'未登记',quarterRecognized:'未登记',quarterEntryCount:0,
    opportunityTab:'overview',opportunityTabs:Object.entries(TABS).map(([key,label])=>({key,label})),opportunityAdvice:{},adviceExpanded:{},relatedTasks:[],relatedVisits:[],progressEvents:[],taskStats:{total:0,completed:0,pending:0},
    fdeVisitMode:'self',fdeOwnVisits:[],fdeOwnTotal:0,fdeOwnOffset:0,fdeOwnMore:false,fdeOwnLoading:false,fdeOwnError:'',canRecordThisOpportunity:false,
    readOnly:false,opportunity:null,opportunityLoading:false,opportunityError:'',intentHistory:emptyHistory()},
  toggleProgressEvents(){this.setData({progressEventsExpanded:!this.data.progressEventsExpanded});},
  assetContext(){return JSON.stringify([access.identity(getApp().globalData.session),this.data.customerId,this.data.opportunityId,this._pageGeneration||0]);},
  onLoad(options) {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'customer-assets', options)) return;
    this.setData({basis:'effective',period:options.period==='all'?'all':'year',kind:options.kind==='collection'?'collection':'recognized',
      customerId:options.customer_id||'',customerName:options.customer_name||'',opportunityId:options.opportunity_id||'',
      originCustomerId:options.customer_id||'',originOpportunityId:options.opportunity_id||'',teamId:options.team_id||'',ownerId:options.owner_id||'',
      fdeAssetScope:options.scope||'',fdeAssetMemberId:options.member_id||'',readOnly:options.readonly==='1'});
    if(options.opportunity_id&&wx.setNavigationBarTitle)wx.setNavigationBarTitle({title:'商机详情'});
  },
  onShow(){
    this.writeHidden=false;this.setData({fdeRelationSaving:Boolean(this.pageWrites&&this.pageWrites.fdeMembers&&this.pageWrites.fdeMembers.current())});
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'customer-assets')) return;if(!getApp().ensureLogin()) return;const session=getApp().globalData.session||{};const fde=['fde','fde_lead'].includes(session.role),showDemo=session.permissions?access.can(session,'demo_scene.read'):fde;this.setData({demos:[],demoError:'',opportunityTabs:Object.entries(TABS).map(([key,label])=>({key,label})).concat(showDemo?[{key:'demo',label:'Demo 场景'}]:[]),canReadDemo:showDemo,opportunityTab:!showDemo&&this.data.opportunityTab==='demo'?'overview':this.data.opportunityTab,fdeRestrictToTeams:session.role==='fde_lead',fdeAllowedTeamIds:session.teamIds||[]});this.load();this.loadOpportunity();this.loadQuarterActuals();},
  // BACKEND-CONTRACT /opportunities/{id}/header 先呈现商机；/overview 按需补全量统计，历史独立分页。
  // 建议通过原生 /advice 对象缓存，人工决定后可创建待办；季度计划来自 quarterly_forecasts，实绩另读 /customer-assets，严禁混算。
  // 详见 docs/backend-handoff/客户与商机详解.md「商机经营详情/客户实绩」。
  async loadOpportunity(){
    this._demoSerial=(this._demoSerial||0)+1;this.fdeOwnSerial=(this.fdeOwnSerial||0)+1;this.fdeEligibilitySerial=(this.fdeEligibilitySerial||0)+1;
    this.setData({demos:[],demoTotal:0,demosLoading:false,demosMore:false,demoError:'',canCreateDemoHere:false,fdeOwnVisits:[],fdeOwnTotal:0,fdeOwnOffset:0,fdeOwnMore:false,fdeOwnLoading:false,fdeOwnError:'',canRecordThisOpportunity:false});
    if(!this.data.customerId||!this.data.opportunityId){if(this._adviceOwner)advicePool.release(this._adviceOwner);if(this._detailReader)this._detailReader.close();if(this._intentHistory)this._intentHistory.close();this._pendingOpportunityAdvice=null;this._opportunityContext=null;this._adviceGeneration=(this._adviceGeneration||0)+1;this.opportunitySerial=(this.opportunitySerial||0)+1;this.setData({opportunityAdvice:{},opportunity:null,opportunityLoading:false,opportunityError:'',relatedTasks:[],relatedVisits:[],progressEvents:[],detailPages:{}});return;}
    if(!this._detailReader)this._detailReader=new DetailReadSession(()=>this.renderOpportunityHistory(),()=>access.identity(getApp().globalData.session));
    if(!this._intentHistory)this._intentHistory=new IntentHistoryReader((id,options)=>api.getOpportunityIntentHistory(id,options),state=>this.setData({intentHistory:state}),()=>access.identity(getApp().globalData.session));
    this._intentHistory.reset(this.data.opportunityId);this.setData({intentHistory:emptyHistory()});
    const reader=this._detailReader,token=reader.reset(this.data.opportunityId);
    const serial=this.opportunitySerial=(this.opportunitySerial||0)+1;
    this._adviceGeneration=(this._adviceGeneration||0)+1;this._opportunityContext=null;
    if(this._adviceOwner)advicePool.release(this._adviceOwner);
    this._adviceOwner={};
    const adviceSession=getApp().globalData.session,adviceIdentity=access.identity(adviceSession),previews={};
    const adviceScope=JSON.stringify([adviceIdentity,this.data.customerId,this.data.opportunityId]);
    // Page-instance memory only: onHide/onShow must not forget an accepted job.
    // Identity/object changes and onUnload cannot transfer its ID to another view.
    if(!this._pendingOpportunityAdvice||this._pendingOpportunityAdvice.scope!==adviceScope)this._pendingOpportunityAdvice={scope:adviceScope,items:{}};
    if(adviceSession&&(!adviceSession.permissions||(access.can(adviceSession,'advice.read')&&access.can(adviceSession,'advice.opportunity'))))Object.keys(TABS).forEach(tab=>{
      const pending=this._pendingOpportunityAdvice.items[tab];
      if(pending&&pending.id&&['queued','running'].includes(pending.status)){previews[tab]={...pending,summary:'',rows:[],needsCheck:true,checking:true};return;}
      const key=advicePool.key(adviceIdentity,this.data.opportunityId,tab),cached=advicePool.peek(key);
      if(cached){try{previews[tab]={...opportunityAdviceView(cached,this.data.customerId,this.data.opportunityId,tab),needsCheck:true,checking:true};}catch(_){advicePool.forget(key);}}
    });else this._pendingOpportunityAdvice=null;
    this.setData({opportunityAdvice:previews,adviceExpanded:{},relatedTasks:[],relatedVisits:[],progressEvents:[],detailPages:{}});
    this.setData({opportunity:null,opportunityLoading:true,opportunityError:''});
    try{
      const raw=await api.getOpportunityDetailHeader(this.data.opportunityId);
      if(serial!==this.opportunitySerial||!reader.current(token))return;
      if(String(raw.id)!==String(this.data.customerId))throw new Error('商机客户数据不匹配');
      const context=opportunityContext(raw,this.data.opportunityId);
      const customer=normalizeCustomerDetail(context,this.data.opportunityId);
      this._opportunityContext=context;
      const opportunity=(customer.opportunities||[]).find(item=>String(item.id)===String(this.data.opportunityId));
      if(!opportunity)throw new Error('商机不存在或无权查看');
      this.setData({relatedTasks:customer.tasks||[],relatedVisits:customer.visits||[],progressEvents:[],relatedVisitCount:customer.visitCount,taskStats:{total:customer.taskCount,completed:customer.completedTaskCount,pending:customer.summaryPending?'—':customer.taskCount-customer.completedTaskCount},customerName:this.data.customerName||customer.name,opportunity:{...opportunity,customerName:customer.name,
        statusLabel:statusText(opportunity.status),ownerLabel:opportunity.owner_name||customer.owner||'未记录',teamLabel:opportunity.team_name||customer.team||'未记录',
        createdLabel:dateTime(opportunity.created_at),updatedLabel:dateTime(opportunity.updated_at)},fdeMembers:opportunity.fde_members||[],canManageFdeRelation:Boolean(opportunity.can_manage_fde_members),fdeRelationDirty:false,fdeRelationError:""});
      this.renderOpportunityHistory();this.loadOpportunitySections();this.prefetchOpportunityAdvice();
      if(this.data.canReadDemo)this.loadDemoScenes();if(this.data.isFde){this.checkFdeVisitEligibility();this.loadFdeOwnVisits();}
    }catch(e){if(serial===this.opportunitySerial&&reader.current(token))this.setData({opportunity:null,opportunityError:e.message||'商机信息加载失败'});}
    finally{if(serial===this.opportunitySerial&&reader.current(token))this.setData({opportunityLoading:false});}
  },
  renderOpportunityHistory(){
    const reader=this._detailReader;if(!reader||!reader.current()||!this._opportunityContext)return;
    const summary=reader.resource('overview');
    const context={...(summary.data||this._opportunityContext),tasks:reader.state('tasks').items,visits:reader.state('visits').items};
    const customer=normalizeCustomerDetail(context,this.data.opportunityId,{preserveVisitOrder:true});
    this.setData({detailSummary:{loading:summary.loading,loaded:summary.loaded,error:summary.error},detailPages:pageStates(reader.pages),relatedTasks:customer.tasks,relatedVisits:customer.visits,relatedVisitCount:customer.visitCount,
      taskStats:{total:customer.taskCount,completed:customer.completedTaskCount,pending:customer.summaryPending?'—':customer.taskCount-customer.completedTaskCount},
      ...(this.data.opportunity?{opportunity:{...this.data.opportunity,signal:customer.opportunities[0].signal}}:{}),
      progressEvents:reader.state('timeline').items.map(row=>({...row,date:businessTimeLabel(row.at)}))});
  },
  loadOpportunitySummary(options={}){
    if(!this._detailReader||!this._opportunityContext)return;
    const id=this.data.opportunityId,customerId=this.data.customerId;
    return this._detailReader.loadResource('overview',async()=>{
      const raw=await api.getOpportunityDetailOverview(id);
      if(String(raw.id)!==String(customerId)||!raw.summary)throw Error('商机经营汇总响应不完整');
      return opportunityContext(raw,id);
    },options);
  },
  retryOpportunitySummary(){return this.loadOpportunitySummary({retry:true});},
  opportunitySection(){return {tasks:'tasks',visits:'visits',opportunity:'timeline'}[this.data.opportunityTab];},
  loadOpportunitySections(){
    if(this.data.opportunityTab==='overview')this.loadOpportunitySummary();
    const key=this.opportunitySection();
    if(!key||!this._detailReader||!this._opportunityContext||(key==='visits'&&this.data.isFde&&this.data.fdeVisitMode==='self'))return;
    return key==='timeline'?Promise.all([this.loadOpportunitySection(key),this.loadIntentHistory()]):this.loadOpportunitySection(key);
  },
  loadIntentHistory(options={}){if(!this._intentHistory||!this.data.opportunity||this.data.opportunityTab!=='opportunity')return;return this._intentHistory.load(options);},
  moreIntentHistory(){return this.loadIntentHistory({more:true});},
  retryIntentHistory(){return this.loadIntentHistory({retry:true});},
  refreshIntentHistory(){return this.loadIntentHistory({refresh:true});},
  loadOpportunitySection(key,options={}){
    const customer_id=this.data.customerId,opportunity_id=this.data.opportunityId;
    const loaders={tasks:p=>api.listDetailTasks({...p,customer_id,opportunity_id}),visits:p=>api.listVisits({...p,customer_id,opportunity_id,sort:'created_desc'}),timeline:p=>api.getOpportunityTimeline(opportunity_id,{...p,customer_id})};
    return this._detailReader.load(key,loaders[key],options);
  },
  moreOpportunitySection(e){return this.loadOpportunitySection(e.currentTarget.dataset.section,{more:true});},
  retryOpportunitySection(e){return this.loadOpportunitySection(e.currentTarget.dataset.section,{retry:true});},
  openProgressEvent(e){const row=this.data.progressEvents.find(item=>item.key===e.currentTarget.dataset.key);if(!row)return;if(row.object_type==='visit' && row.object_id)wx.navigateTo({url:visitDetailUrl({id:row.object_id})});if(row.object_type==='task')wx.navigateTo({url:`/pages/task-detail/index?id=${encodeURIComponent(row.object_id)}`});},
  fdeMembersChanged(e){if(this.data.fdeRelationSaving)return;const session=getApp().globalData.session;if(session.permissions?!access.can(session,'opportunity.fde_members'):this.data.isFde&&!this.data.isFdeLead)return;this.setData({fdeMembers:e.detail.members,fdeRelationDirty:true,fdeRelationError:''});},
  async saveFdeMembers(){
    const op=this.data.opportunity,session=getApp().globalData.session;if(session.permissions?!access.can(session,'opportunity.fde_members'):this.data.isFde&&!this.data.isFdeLead)return;if(!op||!this.data.canManageFdeRelation||this.data.fdeRelationSaving)return;
    const context=this.assetContext(),members=this.data.fdeMembers.map(p=>p.id),current=()=>context===this.assetContext()&&this.data.opportunity&&op.id===this.data.opportunity.id&&op.version_no===this.data.opportunity.version_no;
    const removed=(op.fde_members||[]).filter(p=>!this.data.fdeMembers.some(m=>m.id===p.id));
    if(removed.length){const result=await new Promise(resolve=>wx.showModal({title:'确认移出协助人员？',content:`${removed.map(p=>p.name).join('、')}将退出本商机。未完成任务不会自动完成；请在任务详情检查并协调交接。`,success:resolve,fail:()=>resolve({confirm:false})}));if(!result.confirm)return;}
    if(!current()||this.data.fdeRelationSaving||JSON.stringify(members)!==JSON.stringify(this.data.fdeMembers.map(p=>p.id)))return;
    const write=writes.begin(this,'fdeMembers',()=>[this.data.customerId,this.data.opportunityId]);if(!write)return;let applied=false;
    this.setData({fdeRelationSaving:true,fdeRelationError:''});
    try{const r=await api.updateFdeMembers(op.id,members,op.version_no);if(!current())return;if(!r.version_no||!Array.isArray(r.fde_members)||typeof r.changed!=='boolean')throw Error('成员保存回执不完整，请重新读取核对');applied=true;this.setData({fdeMembers:r.fde_members,fdeRelationDirty:false,fdeRelationSaving:false,opportunity:{...op,version_no:r.version_no,fde_members:r.fde_members}});wx.showToast({title:r.changed?'协助名单已更新':'名单没有变化',icon:r.changed?'success':'none'});}catch(error){if(current())this.setData({fdeRelationError:error.statusCode===409?'名单已被其他人修改，请刷新项目后重新选择':error.message||'名单保存失败'});}finally{write.finish('fdeRelationSaving');if(!applied&&write.settledVisible()&&!current())this.loadOpportunity();}
  },
  onHide(){this.writeHidden=true;this._pageGeneration=(this._pageGeneration||0)+1;this._demoSerial=(this._demoSerial||0)+1;if(this._adviceOwner)advicePool.release(this._adviceOwner);this.setData({demosLoading:false,fdeRelationSaving:Boolean(this.pageWrites&&this.pageWrites.fdeMembers&&this.pageWrites.fdeMembers.current())});if(this._detailReader)this._detailReader.close();if(this._intentHistory)this._intentHistory.close();this._adviceGeneration=(this._adviceGeneration||0)+1;this.quarterActualSerial=(this.quarterActualSerial||0)+1;this.loadSerial=(this.loadSerial||0)+1;this.fdeOwnSerial=(this.fdeOwnSerial||0)+1;this.fdeEligibilitySerial=(this.fdeEligibilitySerial||0)+1;},
  onUnload(){this.unloaded=true;this.onHide();this._pendingOpportunityAdvice=null;this.setData({opportunityAdvice:{}});this.fdeOwnSerial=(this.fdeOwnSerial||0)+1;this.fdeEligibilitySerial=(this.fdeEligibilitySerial||0)+1;this._adviceGeneration=(this._adviceGeneration||0)+1;this.opportunitySerial=(this.opportunitySerial||0)+1;this.quarterActualSerial=(this.quarterActualSerial||0)+1;this.loadSerial=(this.loadSerial||0)+1;},
  async loadDemoScenes(more=false){
    if(!(this.data.canReadDemo||!getApp().globalData.session.permissions&&this.data.isFde)||!this.data.opportunity||this.data.demosLoading)return;
    const context=this.assetContext(),id=this.data.opportunityId,serial=this._demoSerial=(this._demoSerial||0)+1;
    const current=()=>serial===this._demoSerial&&context===this.assetContext();
    this.setData({demosLoading:true,demoError:''});
    try{
      const offset=more?this.data.demos.length:0;
      const result=await api.listDemoScenes(id,{offset,limit:20});
      if(!current())return;
      if(result.data_source!=='database'||!Array.isArray(result.items)||!Number.isInteger(result.total))throw Error('场景数据暂未加载完成');
      const aggregate=finance.aggregate({...result,...result.summary},this.data.kind);
      const rows=result.items.map(row=>({...row,createdLabel:dateTime(row.created_at)}));
      this.setData({canCreateDemoHere:result.can_create===true&&access.can(getApp().globalData.session,'demo_scene.create'),demos:more?this.data.demos.concat(rows):rows,demoTotal:result.total,demosMore:offset+rows.length<result.total});
    }catch(error){if(current())this.setData({demoError:error.message||'场景读取失败'});}
    finally{if(current())this.setData({demosLoading:false});}
  },
  moreDemoScenes(){return this.loadDemoScenes(true);},
  viewDemoScene(e){
    if(!(this.data.canReadDemo||!getApp().globalData.session.permissions&&this.data.isFde))return;
    const id=e.currentTarget.dataset.id;
    if(!this.data.demos.some(row=>row.id===id))return;
    wx.navigateTo({events:{demoSaved:()=>this.loadDemoScenes()},url:'/pages/demo-create/index?customer_id='+encodeURIComponent(this.data.customerId)+'&opportunity_id='+encodeURIComponent(this.data.opportunityId)+'&demo_id='+encodeURIComponent(id)+'&view=1'});
  },
  openDemoScenes() {
    const session=getApp().globalData.session;
    if(session.permissions?!this.data.canCreateDemoHere:!this.data.isFde||!this.data.canRecordThisOpportunity)return;
    wx.navigateTo({events:{demoSaved:()=>{this.setData({opportunityTab:'demo'});this.loadDemoScenes();}},url:'/pages/demo-create/index?customer_id='+encodeURIComponent(this.data.customerId)+'&opportunity_id='+encodeURIComponent(this.data.opportunityId)});
  },
  async checkFdeVisitEligibility() {
    this.setData({canRecordThisOpportunity:false});
    if(!this.data.isFde || !access.can(getApp().globalData.session,'visit.create'))return;
    const serial=this.fdeEligibilitySerial=(this.fdeEligibilitySerial||0)+1, id=this.data.opportunityId,context=this.assetContext();
    try { const r=await api.listFdeVisitOpportunities({customer_id:this.data.customerId,opportunity_id:id,limit:1,offset:0});
      if(serial===this.fdeEligibilitySerial&&context===this.assetContext())this.setData({canRecordThisOpportunity:(r.items||[]).some(row=>row.id===id&&row.customer_id===this.data.customerId)});
    } catch (_) { if(serial===this.fdeEligibilitySerial&&context===this.assetContext())this.setData({canRecordThisOpportunity:false}); }
  },
  recordFdeVisit() {
    if(!this.data.canRecordThisOpportunity || !access.can(getApp().globalData.session,'visit.create'))return;
    wx.navigateTo({url:`/pages/visit-entry/index?customer_id=${encodeURIComponent(this.data.customerId)}&opportunity_id=${encodeURIComponent(this.data.opportunityId)}`});
  },
  async loadFdeOwnVisits(more=false) {
    if(!this.data.isFde||!this.data.opportunityId||(more&&this.data.fdeOwnLoading))return;
    const serial=this.fdeOwnSerial=(this.fdeOwnSerial||0)+1,context=this.assetContext();
    this.setData({fdeOwnLoading:true,fdeOwnError:'',...(!more?{fdeOwnVisits:[],fdeOwnOffset:0}:{})});
    try { const r=await api.getFdeActivity({scope:'self',period:'all',opportunity_id:this.data.opportunityId,limit:30,offset:more?this.data.fdeOwnOffset:0});
      if(serial!==this.fdeOwnSerial||context!==this.assetContext())return;
      if(!Array.isArray(r.items)||!Number.isInteger(r.total))throw Error('本人拜访记录响应不完整');
      this.setData({fdeOwnVisits:(more?this.data.fdeOwnVisits:[]).concat(r.items.map(row=>({...row,dateLabel:dateTime(row.interaction_at)}))),fdeOwnTotal:r.total,fdeOwnOffset:r.next_offset,fdeOwnMore:Boolean(r.has_more),fdeOwnLoading:false});
    } catch(error) {if(serial===this.fdeOwnSerial&&context===this.assetContext())this.setData({fdeOwnLoading:false,fdeOwnError:error.message||'本人拜访记录加载失败'});}
  },
  moreFdeOwnVisits(){return this.loadFdeOwnVisits(true);},
  retryFdeOwnVisits(){return this.loadFdeOwnVisits();},
  changeFdeVisitMode(e){this.setData({fdeVisitMode:e.currentTarget.dataset.mode==='all'?'all':'self'});this.loadOpportunitySections();},
  openFdeOwnVisit(e){const row=this.data.fdeOwnVisits.find(item=>item.id===e.currentTarget.dataset.id);if(!row)return;if(!row.can_read_detail){wx.showToast({title:'仅保留本人归档摘要，当前无完整资料权限',icon:'none'});return;}wx.navigateTo({url:visitDetailUrl(row)});},
  selectOpportunityTab(e){
    const tab=e.currentTarget.dataset.tab;
    if(!TABS[tab]&&!(this.data.canReadDemo&&tab==='demo'))return;
    this.setData({opportunityTab:tab});this.loadOpportunityAdvice();this.loadOpportunitySections();
  },
  prefetchOpportunityAdvice(){
    const selected=TABS[this.data.opportunityTab]?this.data.opportunityTab:'overview';
    this.loadOpportunityAdvice(null,selected,100);
    Object.keys(TABS).filter(tab=>tab!==selected).forEach(tab=>this.loadOpportunityAdvice(null,tab,0));
  },
  toggleAdvice(){const tab=this.data.opportunityTab;this.setData({adviceExpanded:{...this.data.adviceExpanded,[tab]:!(this.data.adviceExpanded||{})[tab]}});},
  // Every automatic entry and suggestion-changed event is read-only, even when
  // the event has currentTarget. Only the separately bound action can request AI.
  loadOpportunityAdvice(event,section,priority=100){
    return this.resolveOpportunityAdvice(section||this.data.opportunityTab,priority,false,Boolean(event));
  },
  requestOpportunityAdvice(event){
    const action=event&&event.currentTarget&&event.currentTarget.dataset;
    if(!action||action.adviceAction!=='generate'||String(action.opportunityId)!==String(this.data.opportunityId)||action.section!==this.data.opportunityTab)return;
    return this.resolveOpportunityAdvice(action.section,100,true,true);
  },
  async resolveOpportunityAdvice(tab,priority,generate,refresh){
    const allowed=()=>adviceAllowed(getApp().globalData.session,'opportunity',generate);
    if(!allowed()){if(!generate){this._pendingOpportunityAdvice=null;this.setData({opportunityAdvice:{}});}return;}
    const raw=this._opportunityContext,id=this.data.opportunityId;
    if(!raw||!TABS[tab]||String(raw.id)!==String(this.data.customerId)||!id||!(raw.opportunities||[]).some(item=>String(item.id)===String(id))||this.writeHidden||this.unloaded)return;
    const previous=this.data.opportunityAdvice[tab],analysisId=previous&&previous.id;
    const generation=this._adviceGeneration;
    const identity=()=>{const session=(getApp().globalData||{}).session||{};return access.identity(session);};
    const owner=identity();
    const cacheKey=advicePool.key(owner,id,tab);
    // Keep GET and explicit POST flights distinct across page instances.
    const key=generate?cacheKey:JSON.stringify([cacheKey,'latest']);
    if(previous&&(previous.status==='loading'||(previous.checking&&!previous.needsCheck))){if(priority)advicePool.prioritize(key);return;}
    if(generate&&previous&&['queued','running'].includes(previous.status))return this.loadOpportunityAdvice(null,tab,priority);
    const update=value=>this.setData({opportunityAdvice:{...this.data.opportunityAdvice,[tab]:value}});
    if(!generate&&previous&&previous.status==='ready'&&!previous.needsCheck&&!refresh)return;
    if(!this._adviceOwner)this._adviceOwner={};
    const current=()=>!this.unloaded&&!this.writeHidden&&generation===this._adviceGeneration&&owner===identity()&&raw===this._opportunityContext&&String(id)===String(this.data.opportunityId)&&String(raw.id)===String(this.data.customerId);
    const valid=()=>current()&&allowed()&&(!generate||tab===this.data.opportunityTab);
    update(generate?{status:'loading',summary:'',rows:[],checking:false}:{status:'idle',...previous,checking:true,needsCheck:false,checkError:''});
    try{
      const result=await advicePool.request(key,async()=>{
        // The pool dispatches in a microtask: recheck after navigation/permission changes.
        if(!valid())throw Error('页面或权限已变化');
        const value=generate?await api.requestBusinessAdvice('opportunity',id,tab,true):await api.getLatestBusinessAdvice('opportunity',id,tab);
        opportunityAdviceView(value,raw.id,id,tab,null,!generate);return value;
      },{owner:this._adviceOwner,priority,valid,replaceInvalid:!generate});
      if(current()&&allowed()){
        const state=opportunityAdviceView(result,raw.id,id,tab,null,!generate);
        const scope=JSON.stringify([owner,raw.id,id]);
        if(!this._pendingOpportunityAdvice||this._pendingOpportunityAdvice.scope!==scope)this._pendingOpportunityAdvice={scope,items:{}};
        if(['queued','running'].includes(state.status))this._pendingOpportunityAdvice.items[tab]={id:state.id,status:state.status};
        else delete this._pendingOpportunityAdvice.items[tab];
        if(state.status!=='ready')advicePool.forget(cacheKey);
        else if(!generate){
          // GET uses its own flight key; publish the verified result under the
          // existing preview key too, with the same TTL/capacity as AdvicePool.
          advicePool.cache.delete(cacheKey);advicePool.cache.set(cacheKey,{result,at:Date.now()});
          while(advicePool.cache.size>advicePool.capacity)advicePool.cache.delete(advicePool.cache.keys().next().value);
        }
        update({...state,checking:false});
      }
    }catch(error){if(current()){
      const denied=[401,403,404].includes(error.statusCode)||error.code==='ADVICE_CONTEXT_MISMATCH'||!allowed();
      if(denied){advicePool.forget(cacheKey);if(this._pendingOpportunityAdvice)delete this._pendingOpportunityAdvice.items[tab];}
      update(previous&&previous.status==='ready'&&!denied?{...previous,checking:false,needsCheck:true,checkError:'读取或生成未完成，以下为上次建议，请重试'}:{status:'error',...(!denied&&analysisId?{id:analysisId}:{}),error:error.message||'建议暂不可用，请重试',rows:[],checking:false});
    }}
  },
  openRelatedTask(e){const id=e.currentTarget.dataset.id;if(this.data.relatedTasks.some(t=>String(t.id)===String(id)))wx.navigateTo({url:`/pages/task-detail/index?id=${encodeURIComponent(id)}`});},
  openRelatedVisit(e){const row=this.data.relatedVisits.find(v=>String(v.id)===String(e.currentTarget.dataset.id));if(row)wx.navigateTo({url:visitDetailUrl(row)});},
  async loadQuarterActuals(){
    const serial=this.quarterActualSerial=(this.quarterActualSerial||0)+1,identity=access.identity(getApp().globalData.session);
    if(!this.data.customerId||!this.data.opportunityId){this._quarterActuals=null;this.setData({quarterActualOptions:[],quarterActualLoading:false,quarterFinancial:null,quarterActualHint:''});return;}
    this.setData({quarterActualLoading:true,quarterActualError:'',quarterFinancial:null,quarterActualHint:''});
    const filters={customer_id:this.data.customerId,opportunity_id:this.data.opportunityId,team_id:this.data.teamId,owner_id:this.data.ownerId};
    const d=new Date(Date.now()+8*60*60*1000);const asOf=d.toISOString().slice(0,10);
    try{
      const result=await loadQuarterActuals(api,filters,asOf);
      if(serial!==this.quarterActualSerial||identity!==access.identity(getApp().globalData.session))return;
      const previous=this.data.quarterActualOptions[this.data.quarterActualIndex];
      const key=previous?previous.key:result.currentKey;
      const index=Math.max(0,result.options.findIndex(item=>item.key===key));
      this._quarterActuals=result.groups;
      this.setData({quarterActualOptions:result.options,quarterActualIndex:index,quarterActualAsOf:asOf,...quarterActualDisplay(result.groups,result.options[index].key)});
    }catch(e){if(serial===this.quarterActualSerial&&identity===access.identity(getApp().globalData.session)){this._quarterActuals=null;this.setData({quarterActualError:e.message||'季度实绩加载失败',quarterCollection:'暂不可用',quarterRecognized:'暂不可用',quarterEntryCount:0});}}
    finally{if(serial===this.quarterActualSerial&&identity===access.identity(getApp().globalData.session))this.setData({quarterActualLoading:false});}
  },
  changeActualQuarter(e){
    const index=Number(e.detail.value);const option=this.data.quarterActualOptions[index];
    if(!option||!this._quarterActuals)return;
    this.setData({quarterActualIndex:index,...quarterActualDisplay(this._quarterActuals,option.key)});
  },
  params(offset=0){return {scope:this.data.fdeAssetScope||undefined,member_id:this.data.fdeAssetMemberId||undefined,basis:this.data.basis,period:this.data.period,kind:this.data.kind,customer_id:this.data.customerId,opportunity_id:this.data.opportunityId,team_id:this.data.teamId,owner_id:this.data.ownerId,offset,page_size:30};},
  async load(more=false){
    const serial=this.loadSerial=(this.loadSerial||0)+1,identity=access.identity(getApp().globalData.session);
    this.setData({loading:true,error:''});
    try{
      const result=await api.getCustomerAssets(this.params(more?this.data.items.length:0));
      if(serial!==this.loadSerial||identity!==access.identity(getApp().globalData.session)) return;
      // 实绩与来源类型沿用后台记录；页面只派生金额格式和客户首字。
      const aggregate=finance.aggregate({...result,...result.summary},this.data.kind);
      const rows=result.items.map(r=>{
        const financial=finance.bucket(r),customerFinancial=finance.aggregate(r,this.data.kind);
        return {...r,taxBasisText:({inclusive:'含税',exclusive:'不含税',not_applicable:'不适用',unknown:'税口径未确认'})[r.tax_basis]||'税口径未确认',
          customerInitial:String(r.customer_name||'客').replace(/^【演示】\s*/,'').substring(0,1),
          amountText:financial.amountText,amountUnit:'',qualityHints:this.data.customerId?financial.qualityHints:customerFinancial.qualityHints,
          sourceLabel:financial.sourceLabel,recognizedText:finance.aggregate(r,'recognized').amountText,collectionText:finance.aggregate(r,'collection').amountText,
          ...assetTotal(customerFinancial.displayAmount),totalText:(customerFinancial.isSubtotal?'小计 ':'')+assetTotal(customerFinancial.displayAmount).totalText};
      });
      this.setData({items:more?[...this.data.items,...rows]:rows,summary:result.summary,basis:result.basis || this.data.basis,historicalCount:result.historical_count || 0,totalPartial:aggregate.isSubtotal,...assetTotal(aggregate.displayAmount),hasMore:result.has_more,canManage:false,financialHints:aggregate.qualityHints,asOf:result.as_of});
      if(this.data.customerId && !this.data.customerName){const c=await api.getCustomerReference(this.data.customerId);if(serial===this.loadSerial&&identity===access.identity(getApp().globalData.session))this.setData({customerName:c.name});}
    }catch(e){if(serial===this.loadSerial&&identity===access.identity(getApp().globalData.session))this.setData({error:e.message||'加载失败，请重试',items:more?this.data.items:[],summary:null,totalText:'—'});}
    finally{if(serial===this.loadSerial&&identity===access.identity(getApp().globalData.session))this.setData({loading:false});}
  },
  retry(){this.load();},
  more(){if(!this.data.loading&&this.data.hasMore)this.load(true);},
  changePeriod(e){this.setData({period:e.currentTarget.dataset.period,items:[]},()=>this.load());},
  changeKind(e){this.setData({kind:e.currentTarget.dataset.kind,items:[]},()=>this.load());},
  openCustomer(e){const c=this.data.items.find(r=>r.customer_id===e.currentTarget.dataset.id);this.setData({customerId:c.customer_id,customerName:c.customer_name,opportunityId:'',items:[]},()=>{this.load();this.loadOpportunity();this.loadQuarterActuals();wx.pageScrollTo({scrollTop:0,duration:200});});},
  backToCustomers(){this.setData({customerId:'',customerName:'',opportunityId:'',items:[]},()=>{this.load();this.loadOpportunity();this.loadQuarterActuals();});},
  showAllOpportunities(){this.setData({opportunityId:'',items:[]},()=>{this.load();this.loadOpportunity();this.loadQuarterActuals();});},
  openProfile(e){
    wx.setStorageSync('pendingOpenCustomerId',this.data.customerId);
    const id=e.currentTarget.dataset.id || this.data.opportunityId;
    if(id) wx.setStorageSync('pendingOpenOpportunityId',id);else wx.removeStorageSync('pendingOpenOpportunityId');
    wx.switchTab({url:'/pages/customers/index'});
  },
  help(){wx.showModal({title:'经营实绩',content:'实际由运营在后台按期间确认：确收按物料行登记不含税金额，回款按商机期间登记。销售预测独立。未登记、零、部分登记和撤回分别展示。历史来源仅展示当前采用值，不与运营确认相加。季度金额不推算到账日期；所选日期未覆盖完整季度时单独提示。',showCancel:false});},
});
