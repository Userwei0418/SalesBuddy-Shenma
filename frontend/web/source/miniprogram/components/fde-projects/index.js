const api=require('../../utils/apiClient');
const {overviewRequest,validateFilteredOverview}=require('../../utils/opportunityOverview');
const {decorateOpportunity}=require('../../utils/opportunityListCard');
const {STAGES,OPPORTUNITY_GRADES,gradeOfAmount}=require('../../utils/opportunity');
const access=require('../../utils/access');
const {assertPersonDirectory}=require('../../utils/personPicker');
const {summaryQuarterSelection,toggleSummaryQuarter,listQuarterSelection,toggleListQuarter,OPPORTUNITY_OVERVIEW_HELP}=require('../../utils/opportunityQuarter');
const money=v=>v===null||v===undefined||Number(v)<0?'—':(Number(v)/10000).toLocaleString('zh-CN',{maximumFractionDigits:2});
Component({properties:{initialScope:String,memberId:String,customerId:String},
 data:{memberPickerOpen:false,memberTeams:[],memberDefaultTeamId:'',memberIds:[],showStages:false,showQuarters:false,gradeIndex:0,gradeOptions:['全部等级'],gradeCodes:[''],summaryQuarter:summaryQuarterSelection(new Date(Date.now()+28800000).getUTCFullYear()),summaryYears:[],summaryYearIndex:6,listQuarter:listQuarterSelection(new Date(Date.now()+28800000).getUTCFullYear()),overview:{},dataReady:false,overviewReady:false,overviewLoading:false,overviewError:'',scope:'self',canViewTeam:false,memberContextLabel:'指定成员',loading:true,error:'',membersLoading:false,membersError:'',items:[],filtered:[],members:[{id:'',name:'全部成员'}],memberIndex:0,query:'',stages:STAGES.map(s=>({...s,selected:false})),selectedStages:[],years:[],year:0,yearIndex:0,quarters:[],quarterOptions:[1,2,3,4].map(q=>({value:q,selected:false})),productOptions:['全部产品线'],productIndex:0,count:0,acv:'—',hasMore:false,nextOffset:null,loadingMore:false,moreError:''},
 lifetimes:{attached(){this.closed=false;const s=getApp().globalData.session||{},year=new Date(Date.now()+28800000).getUTCFullYear();const years=Array.from({length:8},(_,i)=>year-5+i),scope=this.queryScope();this.setData({memberContextLabel:this.properties.memberId===s.userId?s.userName||'本人':'指定成员',scope,canViewTeam:scope==='team',memberIds:scope==='team'&&this.properties.initialScope==='self'&&!this.properties.memberId&&!this.properties.customerId&&s.userId?[s.userId]:[],years,year,yearIndex:0,listQuarter:listQuarterSelection(year),summaryYears:[{value:'all',label:'全部年份'},...years.map(value=>({value,label:`${value}年`}))],summaryYearIndex:6,summaryQuarter:summaryQuarterSelection(year)});this.load();},detached(){this.closed=true;this.setData({memberPickerOpen:false});clearTimeout(this.searchTimer);this.serial=(this.serial||0)+1;this.membersSerial=(this.membersSerial||0)+1;}},
 pageLifetimes:{hide(){this.setData({memberPickerOpen:false});},show(){const stale=this.projectIdentity!==access.identity(getApp().globalData.session);if(!this.closed&&this.data.year&&(stale||!this.data.loading))return this.load();}},
 methods:{
  // FDE project list reuses the shared read-only opportunity card.
  editOpportunity(){},
  // Use this action's effective grant for every role; a self drill-in is a filter.
  queryScope(){return access.canViewTeam(getApp().globalData.session,'opportunity.read')?'team':'self';},
  loadOverview(){return this.load({refreshMembers:false});},
  summaryYear(e){const index=Number(e.detail.value),option=this.data.summaryYears[index];if(!option||(option.value!=='all'&&!Number.isInteger(option.value)))return;this.setData({summaryYearIndex:index,summaryQuarter:summaryQuarterSelection(option.value)});return this.load({refreshMembers:false});},
  summaryQuarterChange(e){const summaryQuarter=toggleSummaryQuarter(this.data.summaryQuarter,e.currentTarget.dataset.q);if(summaryQuarter===this.data.summaryQuarter)return;this.setData({summaryQuarter});return this.load({refreshMembers:false});},
  showOpportunityMetricHelp(){wx.showModal({title:'商机统计口径',showCancel:false,content:OPPORTUNITY_OVERVIEW_HELP});},
  toggleStages(){this.setData({showStages:!this.data.showStages,showQuarters:false});},
  toggleQuarters(){this.setData({showQuarters:!this.data.showQuarters,showStages:false});},
  grade(e){this.setData({gradeIndex:Number(e.detail.value)});this.filter();},
  resetFilters(){this.setData({memberIds:[],query:'',selectedStages:[],stages:STAGES.map(s=>({...s,selected:false})),quarters:[],listQuarter:listQuarterSelection(this.data.year),yearIndex:0,quarterOptions:[],gradeIndex:0,productIndex:0,showStages:false,showQuarters:false});return this.load();},

  async load({refreshMembers=true}={}) {
    clearTimeout(this.searchTimer);
    const session = getApp().globalData.session || {};
    const identity = access.identity(session);
    const identityChanged = this.projectIdentity !== identity;
    if (this.projectIdentity !== undefined && identityChanged) {
      this.membersSerial = (this.membersSerial || 0) + 1;
      const summaryYear = new Date(Date.now()+28800000).getUTCFullYear();
      const summaryYears = [{value:'all',label:'全部年份'},...Array.from({length:8},(_,i)=>summaryYear-5+i).map(value=>({value,label:`${value}年`}))];
      this.setData({items:[],filtered:[],count:0,acv:'—',members:[{id:'',name:'全部成员'}],memberIndex:0,memberIds:[],
        membersLoading:false,membersError:'',memberPickerOpen:false,memberTeams:[],memberDefaultTeamId:'',
        year:summaryYear,years:summaryYears.slice(1).map(option=>option.value),yearIndex:0,quarters:[],quarterOptions:[],listQuarter:listQuarterSelection(summaryYear),
        summaryQuarter:summaryQuarterSelection(summaryYear),summaryYears,summaryYearIndex:summaryYears.findIndex(option=>option.value===summaryYear),
        memberContextLabel:this.properties.memberId === session.userId ? session.userName || '本人' : '指定成员',
        productOptions:['全部产品线'],productIndex:0});
    }
    this.projectIdentity = identity;
    const scope=this.queryScope();
    this.setData({scope,canViewTeam:scope==='team',memberIds:scope==='team'?this.data.memberIds:[]});
    const serial = this.serial = (this.serial || 0) + 1;
    const current = () => !this.closed && serial === this.serial && identity === access.identity(getApp().globalData.session);
    const selectedMemberIds = [...this.data.memberIds];
    const selection = {...this.data.summaryQuarter,quarters:[...this.data.summaryQuarter.quarters]};
    const params = this.pageParams();
    this.pageRequest = params;
    this.setData({loading:true,dataReady:false,overview:{},overviewReady:false,overviewLoading:!this.properties.customerId,overviewError:'',error:'',items:[],filtered:[],count:0,acv:'—',hasMore:false,nextOffset:null,loadingMore:false,moreError:''});
    // This directory keeps the dashboard's authorized member scope, but never gates project rendering.
    // Query-only changes reuse the same directory, including an in-flight read.
    if (this.data.canViewTeam && (refreshMembers || identityChanged)) this.loadMembers(identity, selectedMemberIds);
    try {
      const response = await api.listOpportunities({...params,...(!this.properties.customerId?overviewRequest(selection):{}),pageSize:20,offset:0});
      if (!current()) return;
      const overview = this.properties.customerId ? null : validateFilteredOverview(response,selection);
      this.acceptPage(response, false);
      this.setData({loading:false,dataReady:true,overview:overview||{},overviewReady:!!overview,overviewLoading:false});
    } catch (error) {
      if (current()) this.setData({loading:false,dataReady:false,overviewLoading:false,overviewReady:false,overview:{},overviewError:this.properties.customerId?'':error.message||'筛选统计暂不可用',error:error.message||'协助商机加载失败',items:[],filtered:[],count:0,acv:'—'});
    }
  },
  async loadMembers(identity, selectedMemberIds = this.data.memberIds) {
    const serial = this.membersSerial = (this.membersSerial || 0) + 1;
    const current = () => !this.closed && serial === this.membersSerial && identity === access.identity(getApp().globalData.session);
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
  acceptPage(response, append) {
    this.setData({stages:STAGES.map(s=>({...s,selected:this.data.selectedStages.includes(s.code)})),
      gradeOptions:['全部等级',...OPPORTUNITY_GRADES.map(g=>g.label)],gradeCodes:['',...OPPORTUNITY_GRADES.map(g=>g.code)]});
    if (!response || !Array.isArray(response.items) || !response.summary || !Number.isInteger(response.summary.total) || typeof response.has_more !== 'boolean') throw new Error('商机分页数据不完整');
    if (response.has_more && (!response.items.length || !Number.isInteger(response.next_offset))) throw new Error('商机分页位置无效');
    const added = response.items.map(r => ({
      ...decorateOpportunity(r),
      canEdit: false,
      amountText: money(r.amount),
      stageLabel: (STAGES.find(s => s.code === r.stage_code || s.probability === r.probability) || {}).text || r.status,
      memberNames: (r.fde_members || []).map(m => m.name).join('、'),
      grade: (gradeOfAmount(r.amount) || {}).code || '未分级'
    }));
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
  async loadMore() {
    if (this.data.loading || this.data.loadingMore || !this.data.hasMore) return;
    const serial = this.serial,
      identity = this.projectIdentity,
      offset = this.data.nextOffset;
    const current = () => !this.closed && serial === this.serial && identity === access.identity(getApp().globalData.session);
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
      // Refresh both halves if the filtered population changed between pages.
      if (response && response.summary && response.summary.total !== this.data.count) {
        await this.load({refreshMembers:false});
        return;
      }
      this.acceptPage(response, true);
    } catch (error) {
      if (current()) this.setData({
        moreError: error.message || '下一页加载失败'
      });
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
    clearTimeout(this.searchTimer);
    // Invalidate the old filter immediately, before the new request is dispatched.
    this.serial=(this.serial||0)+1;
    this.setData({query:e.detail.value,loading:true,dataReady:false,overview:{},overviewReady:false,overviewLoading:!this.properties.customerId,overviewError:'',loadingMore:false,items:[],filtered:[],count:0,acv:'—',hasMore:false,nextOffset:null,error:'',moreError:''});
    const identity=access.identity(getApp().globalData.session);
    this.searchTimer=setTimeout(()=>{if(!this.closed&&identity===access.identity(getApp().globalData.session))this.filter();},250);
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
