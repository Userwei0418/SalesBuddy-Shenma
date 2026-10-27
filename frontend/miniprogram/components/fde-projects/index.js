const opportunityPeriods=require('../../utils/opportunityPeriodFilters');
const api=require('../../utils/apiClient');
const {readOpportunityWindow}=require('../../utils/opportunityReadWindow');
const {overviewRequest,validateFilteredOverview}=require('../../utils/opportunityOverview');
const {decorateOpportunity}=require('../../utils/opportunityListCard');
const {STAGES,OPPORTUNITY_GRADES,gradeOfAmount}=require('../../utils/opportunity');
const access=require('../../utils/access');
const {assertPersonDirectory}=require('../../utils/personPicker');
const {summaryQuarterSelection,toggleSummaryQuarter,listQuarterSelection,toggleListQuarter,OPPORTUNITY_OVERVIEW_HELP}=require('../../utils/opportunityQuarter');
const readDenied=error=>[401,403].includes(error.statusCode)||['AUTH_REQUIRED','SESSION_CHANGED'].includes(error.code);
const transientReadFailure=error=>error.code==='NETWORK_ERROR'||error.statusCode>=500;
const money=v=>v===null||v===undefined||Number(v)<0?'—':(Number(v)/10000).toLocaleString('zh-CN',{maximumFractionDigits:2});
Component({properties:{initialScope:String,memberId:String,customerId:String},
 data:{opportunityPeriods:opportunityPeriods.initial(),memberPickerOpen:false,memberTeams:[],memberDefaultTeamId:'',memberIds:[],showStages:false,showQuarters:false,gradeIndex:0,gradeOptions:['全部等级'],gradeCodes:[''],summaryQuarter:summaryQuarterSelection(new Date(Date.now()+28800000).getUTCFullYear()),summaryYears:[],summaryYearIndex:6,listQuarter:listQuarterSelection(new Date(Date.now()+28800000).getUTCFullYear()),overview:{},dataReady:false,overviewReady:false,overviewLoading:false,overviewError:'',scope:'self',canViewTeam:false,memberContextLabel:'指定成员',loading:true,error:'',membersLoading:false,membersError:'',items:[],filtered:[],members:[{id:'',name:'全部成员'}],memberIndex:0,query:'',stages:STAGES.map(s=>({...s,selected:false})),selectedStages:[],years:[],year:0,yearIndex:0,quarters:[],quarterOptions:[1,2,3,4].map(q=>({value:q,selected:false})),productOptions:['全部产品线'],productIndex:0,count:0,acv:'—',hasMore:false,nextOffset:null,loadingMore:false,moreError:'',listChanged:false},
 lifetimes:{attached(){this.closed=false;this.hidden=false;const s=getApp().globalData.session||{},year=new Date(Date.now()+28800000).getUTCFullYear();const years=Array.from({length:8},(_,i)=>year-5+i),scope=this.queryScope();this.setData({memberContextLabel:this.properties.memberId===s.userId?s.userName||'本人':'指定成员',scope,canViewTeam:scope==='team',memberIds:scope==='team'&&this.properties.initialScope==='self'&&!this.properties.memberId&&!this.properties.customerId&&s.userId?[s.userId]:[],years,year,yearIndex:0,listQuarter:listQuarterSelection(year),summaryYears:[{value:'all',label:'全部年份'},...years.map(value=>({value,label:`${value}年`}))],summaryYearIndex:6,summaryQuarter:summaryQuarterSelection(year)});this.load();},detached(){this.closed=true;this.suspendReads();}},
 pageLifetimes:{hide(){this.suspendReads();},show(){
  this.hidden=false;
  const identity=access.identity(getApp().globalData.session);
  const stale=this.projectIdentity!==identity||this.pageKey!==JSON.stringify([identity,{...this.pageParams(),includeOverview:false}]);
  if(this.closed||!this.data.year)return;
  if(stale||!this.data.dataReady||Date.now()-(this.loadedAt||0)>=30000)return this.load({restoreLoaded:true});
  if(this.data.canViewTeam&&this.membersInterrupted)this.loadMembers(identity,[...this.data.memberIds]);
  if(!this.data.overviewReady&&!this.properties.customerId)return this.loadOverview();
 }},
 methods:{
  // FDE project list reuses the shared read-only opportunity card.
  editOpportunity(){},
  // Use this action's effective grant for every role; a self drill-in is a filter.
  queryScope(){return access.canViewTeam(getApp().globalData.session,'opportunity.read')?'team':'self';},
  suspendReads(){
    this.membersInterrupted=Boolean(this.data.membersLoading)||this.membersInterrupted;
    this.hidden=true;clearTimeout(this.searchTimer);
    this.serial=(this.serial||0)+1;this.overviewSerial=(this.overviewSerial||0)+1;this.membersSerial=(this.membersSerial||0)+1;
    this.listFlight=this.overviewFlight=null;
    this.setData({memberPickerOpen:false,loading:false,loadingMore:false,overviewLoading:false,membersLoading:false});
  },
  clearResults(){
    this.loadedAt=0;
    this.membersSerial=(this.membersSerial||0)+1;
    this.serial=(this.serial||0)+1;this.overviewSerial=(this.overviewSerial||0)+1;
    this.listFlight=this.overviewFlight=null;
    this.setData({items:[],filtered:[],count:0,acv:'—',dataReady:false,overview:{},overviewReady:false,
      error:'登录、权限或列表状态已变化，请重新加载',
      loading:false,loadingMore:false,overviewLoading:false,hasMore:false,nextOffset:null,
      members:[{id:'',name:'全部成员'}],memberTeams:[],memberIds:[],memberIndex:0,memberPickerOpen:false,membersLoading:false,
      productOptions:['全部产品线'],productIndex:0});
  },
  loadOverview(){
    if(this.closed||this.hidden||this.properties.customerId)return;
    if(this.projectIdentity!==access.identity(getApp().globalData.session)){this.clearResults();return;}
    if(!this.data.dataReady||this.data.loading){
      if(this.data.error&&!this.data.loading)return this.load();
      this.overviewSerial=(this.overviewSerial||0)+1;this.overviewFlight=null;
      this.setData({overviewReady:false,overviewError:'',overviewLoading:false});return;
    }
    const identity=this.projectIdentity,listSerial=this.serial;
    const selection={...this.data.summaryQuarter,quarters:[...this.data.summaryQuarter.quarters]};
    const params={...this.pageRequest,...overviewRequest(selection),pageSize:1,offset:0};
    const key=JSON.stringify([identity,listSerial,params]);
    if(this.overviewFlight&&this.overviewFlight.key===key)return this.overviewFlight.promise;
    const serial=this.overviewSerial=(this.overviewSerial||0)+1;
    const current=()=>!this.closed&&!this.hidden&&serial===this.overviewSerial&&listSerial===this.serial&&identity===access.identity(getApp().globalData.session);
    this.setData({overviewLoading:true,overviewReady:false,overviewError:''});
    const flight={key};
    flight.promise=api.listOpportunities(params).then(response=>{
      if(current())this.setData({overview:validateFilteredOverview(response,selection),overviewReady:true});
    }).catch(error=>{
      if(!current())return;
      if(readDenied(error))this.clearResults();
      this.setData({overviewReady:false,overviewError:error.message||'筛选统计暂不可用'});
    }).finally(()=>{
      if(this.overviewFlight===flight)this.overviewFlight=null;
      if(current())this.setData({overviewLoading:false});
    });
    this.overviewFlight=flight;return flight.promise;
  },
  summaryYear(e){const index=Number(e.detail.value),option=this.data.summaryYears[index];if(!option||(option.value!=='all'&&!Number.isInteger(option.value)))return;this.setData({summaryYearIndex:index,summaryQuarter:summaryQuarterSelection(option.value)});return this.loadOverview();},
  summaryQuarterChange(e){const summaryQuarter=toggleSummaryQuarter(this.data.summaryQuarter,e.currentTarget.dataset.q);if(summaryQuarter===this.data.summaryQuarter)return;this.setData({summaryQuarter});return this.loadOverview();},
  showOpportunityMetricHelp(){wx.showModal({title:'商机统计口径',showCancel:false,content:OPPORTUNITY_OVERVIEW_HELP});},
  toggleStages(){this.setData({showStages:!this.data.showStages,showQuarters:false});},
  toggleQuarters(){this.setData({showQuarters:!this.data.showQuarters,showStages:false});},
  grade(e){this.setData({gradeIndex:Number(e.detail.value)});this.filter();},
  changeOpportunityPeriods(e){const value=e.detail.value;opportunityPeriods.params(value);this.setData({opportunityPeriods:value});return this.load();},
  resetFilters(){this.setData({opportunityPeriods:opportunityPeriods.initial(),memberIds:[],query:'',selectedStages:[],stages:STAGES.map(s=>({...s,selected:false})),quarters:[],listQuarter:listQuarterSelection(this.data.year),yearIndex:0,quarterOptions:[],gradeIndex:0,productIndex:0,showStages:false,showQuarters:false});return this.load();},

  load({refreshMembers=true,restoreLoaded=false}={}) {
    if(this.closed||this.hidden)return;
    clearTimeout(this.searchTimer);
    const session = getApp().globalData.session || {};
    const identity = access.identity(session);
    const identityChanged = this.projectIdentity !== identity;
    if (this.projectIdentity !== undefined && identityChanged) {
      this.membersSerial = (this.membersSerial || 0) + 1;
      const summaryYear = new Date(Date.now()+28800000).getUTCFullYear();
      const summaryYears = [{value:'all',label:'全部年份'},...Array.from({length:8},(_,i)=>summaryYear-5+i).map(value=>({value,label:`${value}年`}))];
      this.setData({opportunityPeriods:opportunityPeriods.initial(),items:[],filtered:[],count:0,acv:'—',members:[{id:'',name:'全部成员'}],memberIndex:0,memberIds:[],
        membersLoading:false,membersError:'',memberPickerOpen:false,memberTeams:[],memberDefaultTeamId:'',
        year:summaryYear,years:summaryYears.slice(1).map(option=>option.value),yearIndex:0,quarters:[],quarterOptions:[],listQuarter:listQuarterSelection(summaryYear),
        summaryQuarter:summaryQuarterSelection(summaryYear),summaryYears,summaryYearIndex:summaryYears.findIndex(option=>option.value===summaryYear),
        memberContextLabel:this.properties.memberId === session.userId ? session.userName || '本人' : '指定成员',
        productOptions:['全部产品线'],productIndex:0});
    }
    this.projectIdentity = identity;
    const scope=this.queryScope();
    this.setData({scope,canViewTeam:scope==='team',memberIds:scope==='team'?this.data.memberIds:[]});
    const params = JSON.parse(JSON.stringify({...this.pageParams(),includeOverview:false}));
    const pageKey = JSON.stringify([identity,params]);
    if(this.listFlight&&this.listFlight.key===pageKey)return this.listFlight.promise;
    const preserve = this.pageKey===pageKey && this.data.items.length;
    const serial = this.serial = (this.serial || 0) + 1;
    const current = () => !this.closed && !this.hidden && serial === this.serial && identity === access.identity(getApp().globalData.session);
    this.pageKey=pageKey;this.pageRequest=params;
    this.overviewSerial=(this.overviewSerial||0)+1;this.overviewFlight=null;
    const selectedMemberIds = [...this.data.memberIds];
    this.setData({loading:true,overviewReady:false,overviewLoading:false,overviewError:'',error:'',loadingMore:false,moreError:'',listChanged:false,
      ...(preserve?{}:{dataReady:false,overview:{},items:[],filtered:[],count:0,acv:'—',hasMore:false,nextOffset:null})});
    if (this.data.canViewTeam && (refreshMembers || identityChanged)) this.loadMembers(identity, selectedMemberIds);
    const flight={key:pageKey};
    const read=restoreLoaded&&preserve
      ? readOpportunityWindow(api.listOpportunities,params,this.data.items.length,current,rows=>{
          const updates=new Map(this.decorateRows(rows).map(row=>[row.id,row]));
          const items=this.data.items.map(row=>updates.get(row.id)||row);
          this.setData({items,filtered:items});
        })
      : api.listOpportunities({...params,pageSize:20,offset:0});
    flight.promise=read.then(response=>{
      if(!current())return;
      this.acceptPage(response,false,0);
      this.loadedAt=Date.now();
      this.setData({loading:false,dataReady:true});
      this.loadOverview();
    }).catch(error=>{
      if(!current())return;
      if(!transientReadFailure(error))this.clearResults();
      this.setData({error:error.message||'协助商机加载失败'});
    }).finally(()=>{
      if(this.listFlight===flight)this.listFlight=null;
      if(current())this.setData({loading:false});
    });
    this.listFlight=flight;return flight.promise;
  },
  async loadMembers(identity, selectedMemberIds = this.data.memberIds) {
    this.membersInterrupted=false;
    const serial = this.membersSerial = (this.membersSerial || 0) + 1;
    const current = () => !this.closed && !this.hidden && serial === this.membersSerial && identity === access.identity(getApp().globalData.session);
    this.setData({membersLoading:true,membersError:''});
    try {
      const directory = await api.getDirectoryMembers({permission:'opportunity.read',include_stats:false});
      if (!current()) return;
      if (!directory || !Array.isArray(directory.items) || !Array.isArray(directory.teams)) throw new Error('成员目录暂不可用');
      assertPersonDirectory(directory.items,directory.teams,directory.defaults);
      const dashboard={members:directory.items};
      const members = [{id:'',name:'全部成员'},...dashboard.members];
      const available = new Set(dashboard.members.map(person => person.id));
      // Never relabel a filtered project list as "全部成员" if its selected member disappeared.
      if (selectedMemberIds.some(id => !available.has(id))) throw new Error('当前成员不在最新目录中，请重新选择');
      this.setData({members,memberTeams:directory.teams,memberDefaultTeamId:(directory.defaults||{}).team_id||'',memberContextLabel:(dashboard.members.find(person=>person.id===this.properties.memberId)||{}).name||this.data.memberContextLabel});
    } catch (error) {
      if (current()) this.setData({membersError:error.message||'成员目录暂不可用'});
    } finally {
      if (current()) this.setData({membersLoading:false});
    }
  },
  pageParams() {
    const scope=this.queryScope();
    return {
      ...opportunityPeriods.params(this.data.opportunityPeriods),
      includeClosed: true,
      grade: this.data.gradeCodes[this.data.gradeIndex],
      scope,
      memberId: this.properties.memberId || '',
      memberIds: scope==='team'&&!this.properties.memberId?[...this.data.memberIds]:[],
      customerId: this.properties.customerId,
      query: this.data.query.trim(),
      stages: [...this.data.selectedStages],
      productLine: this.data.productIndex ? this.data.productOptions[this.data.productIndex] : null,
      year: this.data.year,
      quarters: [...this.data.quarters]
    };
  },
  decorateRows(rows) {
    return rows.map(r => ({
      ...decorateOpportunity(r),
      canEdit: false,
      amountText: money(r.amount),
      stageLabel: (STAGES.find(s => s.code === r.stage_code || s.probability === r.probability) || {}).text || r.status,
      memberNames: (r.fde_members || []).map(m => m.name).join('、'),
      grade: (gradeOfAmount(r.amount) || {}).code || '未分级'
    }));
  },
  acceptPage(response, append, offset=append?this.data.nextOffset:0) {
    this.setData({stages:STAGES.map(s=>({...s,selected:this.data.selectedStages.includes(s.code)})),
      gradeOptions:['全部等级',...OPPORTUNITY_GRADES.map(g=>g.label)],gradeCodes:['',...OPPORTUNITY_GRADES.map(g=>g.code)]});
    if (!response || !Array.isArray(response.items) || !response.summary || !Number.isInteger(response.summary.total) || typeof response.has_more !== 'boolean') throw new Error('商机分页数据不完整');
    if (response.has_more && (!response.items.length || !Number.isInteger(response.next_offset))) throw new Error('商机分页位置无效');
    if(response.has_more&&response.next_offset!==offset+response.items.length)throw Error('商机列表分页未继续，请重试');
    if(response.items.some(row=>!row.id)||new Set(response.items.map(row=>row.id)).size!==response.items.length)throw Error('商机列表正在变化，请刷新列表');
    const added = this.decorateRows(response.items);
    const items = append ? this.data.items.concat(added.filter(r => !this.data.items.some(old => old.id === r.id))) : added;
    const selectedProduct = this.data.productIndex ? this.data.productOptions[this.data.productIndex] : null;
    const hasProductFacets = response.facets && Array.isArray(response.facets.product_lines);
    const productOptions = append && !hasProductFacets ? this.data.productOptions : ['全部产品线', ...(hasProductFacets ? response.facets.product_lines : [])];
    const productIndex = selectedProduct ? productOptions.indexOf(selectedProduct) : 0;
    if (selectedProduct && productIndex < 1) throw new Error('所选产品线已不在当前范围，请重新选择');
    this.setData({
      items,
      filtered: items,
      count: append ? this.data.count : response.summary.total,
      acv: append ? this.data.acv : money(response.summary.open_amount),
      hasMore: response.has_more,
      nextOffset: response.next_offset,
      productOptions,
      productIndex,
      moreError: ''
    });
  },
  prefetchMore(){if(!this.data.moreError&&!this.data.error)return this.loadMore();},
  async loadMore() {
    if(this.projectIdentity!==access.identity(getApp().globalData.session)){this.clearResults();return;}
    if(!this.closed&&!this.hidden&&!this.data.dataReady&&this.data.error&&!this.data.loading)return this.load();
    if (this.closed || this.hidden || !this.data.dataReady || this.data.loading || this.data.loadingMore || !this.data.hasMore) return;
    if(this.data.listChanged)return this.load({refreshMembers:false});
    const serial = this.serial,
      identity = this.projectIdentity,
      offset = this.data.nextOffset;
    const current = () => !this.closed && !this.hidden && serial === this.serial && identity === access.identity(getApp().globalData.session);
    this.setData({
      loadingMore: true,
      moreError: ''
    });
    try {
      const response = await api.listOpportunities({
        ...this.pageRequest,
        pageSize: 20,
        offset
      });
      if (!current()) return;
      if (response && response.summary && response.summary.total !== this.data.count) {
        this.setData({listChanged:true});
        throw Error('商机列表已变化，请刷新列表');
      }
      this.acceptPage(response, true);
    } catch (error) {
      if(!current())return;
      if(readDenied(error))this.clearResults();
      this.setData({moreError:error.message||'下一页加载失败'});
    } finally {
      if (current()) this.setData({
        loadingMore: false
      });
    }
  },
  retryMembers() {
    if (!this.data.canViewTeam) return;
    return this.loadMembers(access.identity(getApp().globalData.session), [...this.data.memberIds]);
  },
  filter() {
    return this.load({refreshMembers:false});
  },

  openMemberPicker(){if(!this.data.canViewTeam||this.data.membersLoading||this.data.membersError)return;this.memberPickerIdentity=access.identity(getApp().globalData.session);this.setData({memberPickerOpen:true});},
  closeMemberPicker(){this.setData({memberPickerOpen:false});},
  confirmMemberPicker(e){
    if(!this.data.memberPickerOpen||this.memberPickerIdentity!==access.identity(getApp().globalData.session)||this.data.membersLoading||this.data.membersError)return;
    const ids=e.detail.ids||[];if(ids.some(id=>!id||!this.data.members.some(row=>row.id===id)))return;
    this.closeMemberPicker();return this.member({detail:{ids}});
  },
  member(e){this.setData({memberIds:e.detail.ids});return this.load();},
  search(e){
    this.listFlight=this.overviewFlight=null;
    this.overviewSerial=(this.overviewSerial||0)+1;
    clearTimeout(this.searchTimer);
    // Invalidate the old filter immediately, before the new request is dispatched.
    this.serial=(this.serial||0)+1;
    this.setData({query:e.detail.value,loading:true,dataReady:false,overview:{},overviewReady:false,overviewLoading:!this.properties.customerId,overviewError:'',loadingMore:false,items:[],filtered:[],count:0,acv:'—',hasMore:false,nextOffset:null,error:'',moreError:''});
    const identity=access.identity(getApp().globalData.session);
    this.searchTimer=setTimeout(()=>{if(!this.closed&&!this.hidden&&identity===access.identity(getApp().globalData.session))this.filter();},250);
  },
  product(e){this.setData({productIndex:Number(e.detail.value)});this.filter();},
  year(e){const yearIndex=Number(e.detail.value),option=this.data.summaryYears[yearIndex];if(!option)return;
    const selection=option.value==='all'?listQuarterSelection(this.data.year):summaryQuarterSelection(option.value);
    this.setListPeriod(selection);this.filter();},
  setListPeriod(selection){this.setData({listQuarter:selection,year:selection.year,quarters:selection.quarters,quarterOptions:selection.options,
    yearIndex:Math.max(0,this.data.summaryYears.findIndex(option=>option.value===(selection.isAllYears?'all':selection.year)))});},
  quarter(e){const selection=toggleListQuarter(listQuarterSelection(this.data.year,this.data.quarters),e.currentTarget.dataset.q);
    this.setListPeriod(selection);this.filter();},
  stage(e){const code=e.currentTarget.dataset.code;const selectedStages=code==='all'?[]:this.data.selectedStages.includes(code)?this.data.selectedStages.filter(c=>c!==code):this.data.selectedStages.concat(code);this.setData({selectedStages,stages:this.data.stages.map(s=>({...s,selected:selectedStages.includes(s.code)}))});this.filter();},
  open(e){const row=this.data.items.find(r=>r.id===e.currentTarget.dataset.id);if(row)wx.navigateTo({url:`/pages/customer-assets/index?customer_id=${encodeURIComponent(row.customer_id)}&opportunity_id=${encodeURIComponent(row.id)}&period=all&readonly=1`});}
 }
});
