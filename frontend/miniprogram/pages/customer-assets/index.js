const {DetailReadSession,pageStates}=require('../../utils/detailReadSession');
const {opportunityAdvicePool:advicePool}=require('../../utils/advicePool');
const {businessTimeLabel}=require('../../utils/customerAdvice');
const {TABS,opportunityContext,adviceResult} = require('../../utils/opportunityAdvice');
const { loadQuarterActuals, quarterActualDisplay } = require('../../utils/opportunityQuarterActuals');
const api = require('../../utils/apiClient');
const access = require('../../utils/access');
const {amountWan,requestId} = require('../../utils/customerMap');
const {normalizeCustomerDetail} = require('../../utils/customerDetail');
const {visitDetailUrl} = require('../../utils/visitNavigation');
function today() {const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;}
function dateTime(value) {if(!value)return '未记录';const d=new Date(value);if(Number.isNaN(d.getTime()))return String(value);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;}
function statusText(value) {return ({open:'推进中',won:'已赢单',lost:'已丢单'})[value]||value||'未记录';}
Page({
  data:{demos:[],demoTotal:0,demosLoading:false,demosMore:false,demoError:'',progressEventsExpanded:false,basis:'entries',historicalCount:0,period:'year',kind:'recognized',customerId:'',customerName:'',opportunityId:'',teamId:'',ownerId:'',
    items:[],loading:true,error:'',summary:null,totalText:'—',canManage:false,hasMore:false,asOf:today(),
    formOpen:false,saving:false,amount:'',occurredOn:today(),sourceRef:'',note:'',formOpportunity:{id:'',name:'暂不关联商机'},formSelectionRequired:false,formError:'',originCustomerId:'',originOpportunityId:'',
    quarterActualLoading:false,quarterActualError:'',quarterActualOptions:[],quarterActualIndex:0,quarterCollection:'未登记',quarterRecognized:'未登记',quarterEntryCount:0,
    opportunityTab:'overview',opportunityTabs:Object.entries(TABS).map(([key,label])=>({key,label})),opportunityAdvice:{},adviceExpanded:{},relatedTasks:[],relatedVisits:[],progressEvents:[],taskStats:{total:0,completed:0,pending:0},
    fdeVisitMode:'self',fdeOwnVisits:[],fdeOwnTotal:0,fdeOwnOffset:0,fdeOwnMore:false,fdeOwnLoading:false,fdeOwnError:'',canRecordThisOpportunity:false,
    readOnly:false,opportunity:null,opportunityLoading:false,opportunityError:''},
  toggleProgressEvents(){this.setData({progressEventsExpanded:!this.data.progressEventsExpanded});},
  onLoad(options) {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'customer-assets', options)) return;
    this.setData({basis:options.basis==='historical'?'historical':'entries',period:options.period==='all'?'all':'year',kind:options.kind==='collection'?'collection':'recognized',
      customerId:options.customer_id||'',customerName:options.customer_name||'',opportunityId:options.opportunity_id||'',
      originCustomerId:options.customer_id||'',originOpportunityId:options.opportunity_id||'',teamId:options.team_id||'',ownerId:options.owner_id||'',
      fdeAssetScope:options.scope||'',fdeAssetMemberId:options.member_id||'',readOnly:options.readonly==='1'});
    if(options.opportunity_id&&wx.setNavigationBarTitle)wx.setNavigationBarTitle({title:'商机详情'});
  },
  onShow(){
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'customer-assets')) return;if(!getApp().ensureLogin()) return;const session=getApp().globalData.session||{};const fde=['fde','fde_lead'].includes(session.role),showDemo=session.permissions?access.can(session,'demo_scene.read'):fde;this.setData({demos:[],demoError:'',opportunityTabs:Object.entries(TABS).map(([key,label])=>({key,label})).concat(showDemo?[{key:'demo',label:'Demo 场景'}]:[]),canReadDemo:showDemo,opportunityTab:!showDemo&&this.data.opportunityTab==='demo'?'overview':this.data.opportunityTab,fdeRestrictToTeams:session.role==='fde_lead',fdeAllowedTeamIds:session.teamIds||[]});this.load();this.loadOpportunity();this.loadQuarterActuals();},
  // BACKEND-CONTRACT /opportunities/{id}/header 先呈现商机；/overview 按需补全量统计，历史独立分页。
  // 建议通过原生 /advice 对象缓存，人工决定后可创建待办；季度计划来自 quarterly_forecasts，实绩另读 /customer-assets，严禁混算。
  // 详见 docs/backend-handoff/客户与商机详解.md「商机经营详情/客户实绩」。
  async loadOpportunity(){
    if(!this.data.customerId||!this.data.opportunityId){if(this._adviceOwner)advicePool.release(this._adviceOwner);if(this._detailReader)this._detailReader.close();this._opportunityContext=null;this._adviceGeneration=(this._adviceGeneration||0)+1;this.opportunitySerial=(this.opportunitySerial||0)+1;this.setData({opportunity:null,opportunityLoading:false,opportunityError:'',relatedTasks:[],relatedVisits:[],progressEvents:[],detailPages:{}});return;}
    if(!this._detailReader)this._detailReader=new DetailReadSession(()=>this.renderOpportunityHistory(),()=>access.identity(getApp().globalData.session));
    const reader=this._detailReader,token=reader.reset(this.data.opportunityId);
    const serial=this.opportunitySerial=(this.opportunitySerial||0)+1;
    this._adviceGeneration=(this._adviceGeneration||0)+1;this._opportunityContext=null;
    if(this._adviceOwner)advicePool.release(this._adviceOwner);
    this._adviceOwner={};
    const adviceIdentity=access.identity(getApp().globalData.session),previews={};
    Object.keys(TABS).forEach(tab=>{const cached=advicePool.peek(advicePool.key(adviceIdentity,this.data.opportunityId,tab));if(cached)previews[tab]={...adviceResult(cached),needsCheck:true,checking:true};});
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
    return this.loadOpportunitySection(key);
  },
  loadOpportunitySection(key,options={}){
    const customer_id=this.data.customerId,opportunity_id=this.data.opportunityId;
    const loaders={tasks:p=>api.listDetailTasks({...p,customer_id,opportunity_id}),visits:p=>api.listVisits({...p,customer_id,opportunity_id,sort:'created_desc'}),timeline:p=>api.getOpportunityTimeline(opportunity_id,{...p,customer_id})};
    return this._detailReader.load(key,loaders[key],options);
  },
  moreOpportunitySection(e){return this.loadOpportunitySection(e.currentTarget.dataset.section,{more:true});},
  retryOpportunitySection(e){return this.loadOpportunitySection(e.currentTarget.dataset.section,{retry:true});},
  openProgressEvent(e){const row=this.data.progressEvents.find(item=>item.key===e.currentTarget.dataset.key);if(!row)return;if(row.object_type==='visit' && row.object_id)wx.navigateTo({url:visitDetailUrl({id:row.object_id})});if(row.object_type==='task')wx.navigateTo({url:`/pages/task-detail/index?id=${encodeURIComponent(row.object_id)}`});},
  fdeMembersChanged(e){const session=getApp().globalData.session;if(session.permissions?!access.can(session,'opportunity.fde_members'):this.data.isFde&&!this.data.isFdeLead)return;this.setData({fdeMembers:e.detail.members,fdeRelationDirty:true,fdeRelationError:''});},
  async saveFdeMembers(){
    const op=this.data.opportunity,session=getApp().globalData.session;if(session.permissions?!access.can(session,'opportunity.fde_members'):this.data.isFde&&!this.data.isFdeLead)return;if(!op||!this.data.canManageFdeRelation||this.data.fdeRelationSaving)return;
    const removed=(op.fde_members||[]).filter(p=>!this.data.fdeMembers.some(m=>m.id===p.id));
    if(removed.length){const result=await new Promise(resolve=>wx.showModal({title:'确认移出协助人员？',content:`${removed.map(p=>p.name).join('、')}将退出本商机。未完成任务不会自动完成；请在任务详情检查并协调交接。`,success:resolve,fail:()=>resolve({confirm:false})}));if(!result.confirm)return;}
    this.setData({fdeRelationSaving:true,fdeRelationError:''});
    try{const r=await api.updateFdeMembers(this.data.opportunityId,this.data.fdeMembers.map(p=>p.id),op.version_no);if(!r.version_no||!Array.isArray(r.fde_members)||typeof r.changed!=='boolean')throw Error('成员保存回执不完整，请重新读取核对');this.setData({fdeMembers:r.fde_members,fdeRelationDirty:false,opportunity:{...op,version_no:r.version_no,fde_members:r.fde_members}});wx.showToast({title:r.changed?'协助名单已更新':'名单没有变化',icon:r.changed?'success':'none'});}catch(error){this.setData({fdeRelationError:error.statusCode===409?'名单已被其他人修改，请刷新项目后重新选择':error.message||'名单保存失败'});}finally{this.setData({fdeRelationSaving:false});}
  },
  onHide(){if(this._adviceOwner)advicePool.release(this._adviceOwner);this._formGeneration=(this._formGeneration||0)+1;clearTimeout(this._formSearchTimer);this.setData({formOpen:false});if(this._detailReader)this._detailReader.close();if(this._formReader)this._formReader.close();this._adviceGeneration=(this._adviceGeneration||0)+1;this.quarterActualSerial=(this.quarterActualSerial||0)+1;this.loadSerial=(this.loadSerial||0)+1;this.fdeOwnSerial=(this.fdeOwnSerial||0)+1;this.fdeEligibilitySerial=(this.fdeEligibilitySerial||0)+1;},
  onUnload(){this.onHide();clearTimeout(this._formSearchTimer);this.fdeOwnSerial=(this.fdeOwnSerial||0)+1;this.fdeEligibilitySerial=(this.fdeEligibilitySerial||0)+1;this._adviceGeneration=(this._adviceGeneration||0)+1;this.opportunitySerial=(this.opportunitySerial||0)+1;this.quarterActualSerial=(this.quarterActualSerial||0)+1;this.loadSerial=(this.loadSerial||0)+1;},
  async loadDemoScenes(more=false){
    if(!(this.data.canReadDemo||!getApp().globalData.session.permissions&&this.data.isFde)||!this.data.opportunity||this.data.demosLoading)return;
    const owner=access.identity(getApp().globalData.session),id=this.data.opportunityId;
    this.setData({demosLoading:true,demoError:''});
    try{
      const offset=more?this.data.demos.length:0;
      const result=await api.listDemoScenes(id,{offset,limit:20});
      if(owner!==access.identity(getApp().globalData.session)||id!==this.data.opportunityId)return;
      if(result.data_source!=='database'||!Array.isArray(result.items)||!Number.isInteger(result.total))throw Error('场景数据暂未加载完成');
      const rows=result.items.map(row=>({...row,createdLabel:dateTime(row.created_at)}));
      this.setData({canCreateDemoHere:result.can_create===true&&access.can(getApp().globalData.session,'demo_scene.create'),demos:more?this.data.demos.concat(rows):rows,demoTotal:result.total,demosMore:offset+rows.length<result.total});
    }catch(error){this.setData({demoError:error.message||'场景读取失败'});}
    finally{this.setData({demosLoading:false});}
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
    const serial=this.fdeEligibilitySerial=(this.fdeEligibilitySerial||0)+1, id=this.data.opportunityId, identity=access.identity(getApp().globalData.session);
    try { const r=await api.listFdeVisitOpportunities({customer_id:this.data.customerId,opportunity_id:id,limit:1,offset:0});
      if(serial===this.fdeEligibilitySerial&&identity===access.identity(getApp().globalData.session))this.setData({canRecordThisOpportunity:(r.items||[]).some(row=>row.id===id&&row.customer_id===this.data.customerId)});
    } catch (_) { if(serial===this.fdeEligibilitySerial&&identity===access.identity(getApp().globalData.session))this.setData({canRecordThisOpportunity:false}); }
  },
  recordFdeVisit() {
    if(!this.data.canRecordThisOpportunity || !access.can(getApp().globalData.session,'visit.create'))return;
    wx.navigateTo({url:`/pages/visit-entry/index?customer_id=${encodeURIComponent(this.data.customerId)}&opportunity_id=${encodeURIComponent(this.data.opportunityId)}`});
  },
  async loadFdeOwnVisits(more=false) {
    if(!this.data.isFde||!this.data.opportunityId||(more&&this.data.fdeOwnLoading))return;
    const serial=this.fdeOwnSerial=(this.fdeOwnSerial||0)+1,identity=access.identity(getApp().globalData.session);
    this.setData({fdeOwnLoading:true,fdeOwnError:'',...(!more?{fdeOwnVisits:[],fdeOwnOffset:0}:{})});
    try { const r=await api.getFdeActivity({scope:'self',period:'all',opportunity_id:this.data.opportunityId,limit:30,offset:more?this.data.fdeOwnOffset:0});
      if(serial!==this.fdeOwnSerial||identity!==access.identity(getApp().globalData.session))return;
      if(!Array.isArray(r.items)||!Number.isInteger(r.total))throw Error('本人拜访记录响应不完整');
      this.setData({fdeOwnVisits:(more?this.data.fdeOwnVisits:[]).concat(r.items.map(row=>({...row,dateLabel:dateTime(row.interaction_at)}))),fdeOwnTotal:r.total,fdeOwnOffset:r.next_offset,fdeOwnMore:Boolean(r.has_more),fdeOwnLoading:false});
    } catch(error) {if(serial===this.fdeOwnSerial&&identity===access.identity(getApp().globalData.session))this.setData({fdeOwnLoading:false,fdeOwnError:error.message||'本人拜访记录加载失败'});}
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
  async loadOpportunityAdvice(event,section,priority=100){
    const session=getApp().globalData.session;
    if(session.permissions&&!(access.can(session,'advice.request')&&access.can(session,'advice.opportunity')))return;
    const tab=section||this.data.opportunityTab,raw=this._opportunityContext,id=this.data.opportunityId;
    if(!raw||!TABS[tab])return;
    const previous=this.data.opportunityAdvice[tab],refresh=Boolean(event&&event.currentTarget);
    const generation=this._adviceGeneration;
    const identity=()=>{const session=(getApp().globalData||{}).session||{};return access.identity(session);};
    const owner=identity();
    const key=advicePool.key(owner,id,tab);
    if(previous&&(previous.status==='loading'||(previous.checking&&!previous.needsCheck))){if(priority)advicePool.prioritize(key);return;}
    if(previous&&previous.status==='ready'&&!previous.needsCheck&&!refresh)return;
    if(!this._adviceOwner)this._adviceOwner={};
    const current=()=>generation===this._adviceGeneration&&owner===identity()&&String(id)===String(this.data.opportunityId)&&String(raw.id)===String(this.data.customerId);
    const update=value=>this.setData({opportunityAdvice:{...this.data.opportunityAdvice,[tab]:value}});
    update(previous&&previous.status==='ready'?{...previous,checking:true,needsCheck:false,checkError:''}:{status:'loading',summary:'',rows:[]});
    try{
      const result=await advicePool.request(key,async()=>{const value=await api.queryBusinessAdvice('opportunity',id,tab,refresh);adviceResult(value);return value;},{owner:this._adviceOwner,priority,valid:()=>owner===identity()});
      if(current())update({...adviceResult(result),checking:false});
    }catch(error){if(current())update(previous&&previous.status==='ready'&&![401,403,404].includes(error.statusCode)?{...previous,checking:false,needsCheck:true,checkError:'更新未完成，以下为上次建议，请重试'}:{status:'error',error:error.message||'Agent建议暂不可用，请重试',rows:[]});}
  },
  openRelatedTask(e){const id=e.currentTarget.dataset.id;if(this.data.relatedTasks.some(t=>String(t.id)===String(id)))wx.navigateTo({url:`/pages/task-detail/index?id=${encodeURIComponent(id)}`});},
  openRelatedVisit(e){const row=this.data.relatedVisits.find(v=>String(v.id)===String(e.currentTarget.dataset.id));if(row)wx.navigateTo({url:visitDetailUrl(row)});},
  async loadQuarterActuals(){
    const serial=this.quarterActualSerial=(this.quarterActualSerial||0)+1,identity=access.identity(getApp().globalData.session);
    if(!this.data.customerId||!this.data.opportunityId){this._quarterActuals=null;this.setData({quarterActualOptions:[],quarterActualLoading:false});return;}
    this.setData({quarterActualLoading:true,quarterActualError:''});
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
      const rows=result.items.map(r=>({...r,taxBasisText:({inclusive:'含税',exclusive:'不含税',not_applicable:'不适用',unknown:'税口径未确认'})[r.tax_basis] || '税口径未确认',customerInitial:String(r.customer_name||'客').replace(/^【演示】\s*/, '').substring(0,1),amountText:amountWan(r.amount),recognizedText:amountWan(r.recognized_amount),collectionText:amountWan(r.collection_amount),totalText:amountWan(r[`${this.data.kind}_amount`])}));
      this.setData({items:more?[...this.data.items,...rows]:rows,summary:result.summary,basis:result.basis || this.data.basis,historicalCount:result.historical_count || 0,totalText:amountWan(result.summary[`${this.data.kind}_amount`] === null ? 0 : result.summary[`${this.data.kind}_amount`]),hasMore:result.has_more,canManage:result.can_manage && this.data.canCreateActual !== false,asOf:result.as_of});
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
  help(){wx.showModal({title:'经营实绩',content:'确收、回款按已登记金额展示，明细保留原始时间、来源和税口径。ACV和季度预测不计入。本年截至今天，历年累计包含本年。加载成功但没有记录时显示0。金额按当前客户归属汇总，不作为个人历史绩效。来源不全时，可见金额仅代表已登记部分。',showCancel:false});},
  async openForm(){
    if(!this.data.canManage||!this.data.customerId||this.data.basis==='historical')return;
    if(!this._formReader)this._formReader=new DetailReadSession(()=>this.renderFormChoices(),()=>access.identity(getApp().globalData.session));
    this._formReader.reset(this.data.customerId);
    const generation=this._formGeneration=(this._formGeneration||0)+1, identity=access.identity(getApp().globalData.session);
    const customerId=this.data.customerId, opportunityId=this.data.opportunityId;
    const current=()=>generation===this._formGeneration&&this.data.formOpen&&identity===access.identity(getApp().globalData.session)&&customerId===this.data.customerId;
    this.requestId=requestId();
    this.setData({formOpen:true,formError:'',amount:'',sourceRef:'',note:'',occurredOn:this.data.asOf,formQuery:'',formChoices:[],formChoiceState:{},formOpportunity:{id:'',name:'暂不关联商机'},formTargetLoading:Boolean(opportunityId),formSelectionRequired:Boolean(opportunityId)});
    this.loadFormChoices();
    if(!opportunityId)return;
    try {
      const raw=await api.getCustomerOpportunityOverview(customerId,opportunityId);
      if(!current())return;
      const row=(raw.opportunities||[]).find(item=>String(item.id)===String(opportunityId));
      if(String(raw.id)!==String(customerId)||!row)throw Error('商机不存在或无权查看');
      this.setData({formOpportunity:{id:row.id,name:row.name},formTargetLoading:false,formSelectionRequired:false});
    } catch(error){if(current())this.setData({formError:error.message||'原关联商机暂不可用，请重新选择',formTargetLoading:false});}
  },
  renderFormChoices(){if(!this._formReader||!this._formReader.current()||!this.data.formOpen)return;this.setData({formChoices:this._formReader.state('choices').items,formChoiceState:pageStates(this._formReader.pages).choices||{}});},
  loadFormChoices(options={}){const id=this.data.customerId,q=this.data.formQuery;return this._formReader.load('choices',p=>api.listCustomerOpportunities(id,{...p,q}),options);},
  searchFormOpportunity(e){
    this.setData({formQuery:e.detail.value});clearTimeout(this._formSearchTimer);
    this._formReader.reset(`${this.data.customerId}:${e.detail.value}`);this.setData({formChoices:[],formChoiceState:{}});
    this._formSearchTimer=setTimeout(()=>{if(this.data.formOpen)this.loadFormChoices();},250);
  },
  moreFormChoices(){return this.loadFormChoices({more:true});},
  retryFormChoices(){return this.loadFormChoices({retry:true});},
  selectFormOpportunity(e){
    if(this.data.saving)return;const id=e.currentTarget.dataset.id;
    const row=id?this.data.formChoices.find(item=>String(item.id)===String(id)):{id:'',name:'暂不关联商机'};
    if(row){this._formGeneration=(this._formGeneration||0)+1;this.setData({formOpportunity:{id:row.id,name:row.name},formTargetLoading:false,formSelectionRequired:false,formError:''});}
  },
  closeForm(){if(!this.data.saving){this._formGeneration=(this._formGeneration||0)+1;if(this._formReader)this._formReader.close();clearTimeout(this._formSearchTimer);this.setData({formOpen:false});}},
  stop(){},
  input(e){this.setData({[e.currentTarget.dataset.field]:e.detail.value,formError:this.data.formSelectionRequired?this.data.formError:''});},
  pickDate(e){this.setData({occurredOn:e.detail.value});},
  submit(){
    if(this.data.saving||this.data.formTargetLoading||this.data.formSelectionRequired) return;
    const raw=String(this.data.amount).trim();
    if(!/^\d+(\.\d{1,6})?$/.test(raw)||Number(raw)>999999999999){this.setData({formError:'请输入有效金额，单位为万元，最多保留6位小数'});return;}
    if(!this.data.sourceRef.trim()){this.setData({formError:'请填写来源编号，以便核对和避免重复登记'});return;}
    if(this.data.occurredOn>this.data.asOf){this.setData({formError:'实绩日期不能晚于今天'});return;}
    const generation=this._formGeneration,identity=access.identity(getApp().globalData.session);
    wx.showModal({title:'确认登记实绩',content:`${this.data.customerName}\n${this.data.kind==='recognized'?'确收':'回款'} ${raw} 万元\n日期 ${this.data.occurredOn}\n确认后计入客户资产。`,confirmText:'确认登记',success:r=>{if(r.confirm&&this.data.formOpen&&generation===this._formGeneration&&identity===access.identity(getApp().globalData.session))this.submitConfirmed();}});
  },
  // BACKEND-CONTRACT POST /api/v1/customer-assets：amount 为元的两位小数字符串；occurred_on 为业务日期。
  // confirmed=true + request_id 用于人工确认/幂等；source_ref 必填。服务端复核 can_manage、客户/商机关系和重复来源。
  // 本操作记录真实已发生金额，不是商机概率加权的季度计划。作废需保留审计记录。
  async submitConfirmed(){
    if(this.data.saving||!this.data.formOpen||this.data.formTargetLoading||this.data.formSelectionRequired)return;
    this.setData({saving:true,formError:''});
    try{
      const saved=await api.createCustomerActual({customer_id:this.data.customerId,opportunity_id:this.data.formOpportunity.id||null,
        kind:this.data.kind,amount:(Number(this.data.amount)*10000).toFixed(2),occurred_on:this.data.occurredOn,
        source_ref:this.data.sourceRef.trim(),note:this.data.note.trim(),request_id:this.requestId,confirmed:true});
      if(saved.voided) throw new Error('该记录已作废，请关闭表单后重新登记');
      this.setData({formOpen:false,opportunityId:this.data.opportunityId?this.data.formOpportunity.id:'',period:this.data.occurredOn.slice(0,4)===this.data.asOf.slice(0,4)?this.data.period:'all'});wx.showToast({title:'已登记',icon:'success'});this.load();this.loadQuarterActuals();
    }catch(e){this.setData({formError:e.message||'登记失败，请重试'});}
    finally{this.setData({saving:false});}
  },
  voidEntry(e){
    if(this.data.readOnly||this.data.basis==='historical')return;
    const id=e.currentTarget.dataset.id,session=getApp().globalData.session,row=this.data.items.find(item=>item.id===id);
    if(session.permissions?!(access.can(session,'actual.void')&&row&&row.can_void===true):!this.data.canManage)return;
    wx.showModal({title:'作废这条实绩',content:'作废后不再计入汇总，原记录仍保留。',editable:true,placeholderText:'请填写作废原因',confirmText:'确认作废',success:async r=>{
      if(!r.confirm)return;
      if(!String(r.content||'').trim()){wx.showToast({title:'请填写作废原因',icon:'none'});return;}
      try{await api.voidCustomerActual(id,r.content.trim());wx.showToast({title:'已作废'});this.load();this.loadQuarterActuals();}catch(error){wx.showToast({title:error.message||'操作失败',icon:'none'});}
    }});
  },
});
