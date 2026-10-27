const opportunityPeriods = require('../../utils/opportunityPeriodFilters');
const apiClient = require('../../utils/apiClient');
const {overviewRequest,validateFilteredOverview}=require('../../utils/opportunityOverview');
const access = require('../../utils/access');
const {readOpportunityWindow}=require('../../utils/opportunityReadWindow');
const {can} = access;
const {
  STAGES,
  OPPORTUNITY_GRADES
} = require('../../utils/opportunity');
const {
  beijingDateParts,
  listQuarterSelection,
  toggleListQuarter,
  summaryQuarterSelection,
  toggleSummaryQuarter,
  groupOpportunitiesByQuarter,
  OPPORTUNITY_OVERVIEW_HELP
} = require('../../utils/opportunityQuarter');
const CURRENT_YEAR = beijingDateParts(new Date()).year;
const WORKBENCH_REFRESH_INTERVAL = 30000;
const readDenied = error => [401, 403].includes(error.statusCode) || ['AUTH_REQUIRED', 'SESSION_CHANGED'].includes(error.code);
const transientReadFailure = error => error.code === 'NETWORK_ERROR' || error.statusCode >= 500;
const { decorateOpportunity } = require('../../utils/opportunityListCard');
function optionIndex(options, value) {
  const index = options.findIndex(item => item.value === value);
  return index < 0 ? 0 : index;
}
function opportunityFiltersActive(data) {
  const activity=data.activityWindowOptions[data.activityWindowIndex];
  return opportunityPeriods.active(data.opportunityPeriods) || Boolean(data.opportunityQuery.trim()) || data.listQuarter.quarters.length>0 || data.opportunityOwnerIndex>0 || data.opportunityCloseIndex>0 || data.opportunityGradeIndex>0 || Boolean(activity&&activity.value!=='all') || data.opportunitySelectedStages.length>0 || data.canViewTeam&&data.executionTeamIndex>0;
}
function workbenchContext(app) {
  return access.identity(app.globalData.session);
}
Page({
  data: {
    opportunityPeriods: opportunityPeriods.initial(),
    fdeScope: '',
    fdeMemberId: '',
    fdeCustomerId: '',
    loading: true,
    loadError: "",
    dataReady: false,
    role: "sales",
    roleName: "一线销售",
    scope: "仅本人",
    opportunityItems: [],
    filteredOpportunities: [],
    opportunityGroups: [],
    opportunityBoard: {},
    opportunityTotal: 0,
    opportunityHasMore: false,
    opportunityNextOffset: null,
    opportunityLoadingMore: false,
    opportunityMoreError: "",
    opportunityListChanged: false,
    opportunityDataReady: false,
    opportunityDataError: "",
    opportunityListLoading: false,
    opportunityOverviewLoading: false,
    opportunityListError: "",
    summaryQuarter: summaryQuarterSelection(CURRENT_YEAR),
    listQuarter: listQuarterSelection(CURRENT_YEAR),
    quarterYearOptions: [CURRENT_YEAR - 1, CURRENT_YEAR, CURRENT_YEAR + 1].map(value => ({
      value,
      label: `${value}年`
    })),
    summaryYearOptions: [{value: 'all', label: '全部年份'}, ...[CURRENT_YEAR - 1, CURRENT_YEAR, CURRENT_YEAR + 1].map(value => ({value, label: `${value}年`}))],
    summaryYearIndex: 2,
    listYearIndex: 0,
    canCreateOpportunity: false,
    opportunityOwnerOptions: [{
      value: "all",
      label: "全部负责人"
    }],
    opportunityOwnerIndex: 0,
    opportunityStageOptions: [{
      value: "all",
      label: "全部阶段"
    }, ...STAGES.map(item => ({
      value: item.code,
      label: item.text,
      selected: false
    }))],
    opportunitySelectedStages: [],
    opportunityStageLabel: "全部阶段",
    showOpportunityQuarterFilter: false,
    showOpportunityStageFilter: false,
    opportunityCloseOptions: [{
      value: "all",
      label: "全部关单日期"
    }, {
      value: "month",
      label: "当月"
    }, {
      value: "quarter",
      label: "当季度"
    }, {
      value: "year",
      label: "当年"
    }],
    opportunityCloseIndex: 0,
    opportunityGradeOptions: [{
      value: "all",
      label: "全部等级"
    }, ...OPPORTUNITY_GRADES.map(item => ({
      value: item.code,
      label: item.label
    }))],
    opportunityGradeIndex: 0,
    activityWindowOptions: [{value: 'today', label: '今天有跟进'}, {value: '3d', label: '近3天有跟进'}, {value: 'week', label: '本周有跟进'}, {value: 'month', label: '本月有跟进'}, {value: 'all', label: '全部跟进'}],
    activityWindowIndex: 4,
    activityWindow: 'all',
    executionScope: 'self',
    executionScopeIndex: 0,
    executionScopeOptions: [{value: 'self', label: '我的'}, {value: 'team', label: '授权范围'}],
    opportunityFilterActive: false, canViewTeam:false, canViewAuthorized:false,
    opportunityQuery: '',
    filterRole: "",
    executionTeamOptions: [{
      value: "all",
      label: "全部团队"
    }],
    executionTeamKey: "all",
    executionMemberName: "all",
    executionTeamIndex: 0,
    executionTeamLabel: "全部团队"
  },
  opportunityParams() {
    const selectedOwner = (this.data.opportunityOwnerOptions[this.data.opportunityOwnerIndex] || {}).value;
    const owner = selectedOwner === 'all' ? this.data.executionMemberName : selectedOwner;
    const scope = this.data.canViewAuthorized ? this.data.executionScope : 'self';
    const activityWindow = (this.data.activityWindowOptions[this.data.activityWindowIndex] || {}).value || 'all';
    return {
      ...opportunityPeriods.params(this.data.opportunityPeriods),
      pageSize: 20,
      includeClosed: true,
      order: 'quarter_stage',
      query: this.data.opportunityQuery.trim(),
      teamId: scope === 'self' ? 'all' : this.data.executionTeamKey,
      scope,
      ownerId: scope === 'self' || owner === 'all' ? null : owner,
      stages: this.data.opportunitySelectedStages,
      closePeriod: this.data.opportunityCloseOptions[this.data.opportunityCloseIndex].value,
      grade: this.data.opportunityGradeOptions[this.data.opportunityGradeIndex].value,
      year: this.data.listQuarter.year,
      quarters: this.data.listQuarter.quarters,
      activityWindow,
      ...overviewRequest(this.data.summaryQuarter)
    };
  },
  changeOpportunityPeriods(e){const value=e.detail.value;opportunityPeriods.params(value);this.setData({opportunityPeriods:value},()=>this.applyOpportunityFilters());},
  opportunityScopeLabel(params) {
    if(params.scope==='self')return '本人';
    const parts=[params.scope==='authorized'?'授权范围':access.scopeLabel(getApp().globalData.session,'opportunity.read')];
    if(params.teamId && params.teamId!=='all')parts.push((this.data.executionTeamOptions.find(item=>item.value===params.teamId)||{}).label||'指定部门');
    if(params.ownerId)parts.push((this.data.opportunityOwnerOptions.find(item=>item.value===params.ownerId)||{}).label||'指定成员');
    return parts.join(' · ');
  },
  clearOpportunityResults() {
    this.opportunityListSerial = (this.opportunityListSerial || 0) + 1;
    this.opportunityOverviewSerial = (this.opportunityOverviewSerial || 0) + 1;
    this.opportunityListFlight = this.opportunityOverviewFlight = null;
    this.opportunityPageReady = false;
    this.listOpportunities = [];
    this.setData({opportunityItems:[],filteredOpportunities:[],opportunityGroups:[],
      opportunityBoard:{},opportunityDataReady:false,opportunityTotal:0,opportunityHasMore:false,
      opportunityListError:'登录、权限或列表状态已变化，请重新加载',
      opportunityNextOffset:null,opportunityListLoading:false,opportunityLoadingMore:false,opportunityOverviewLoading:false,
      opportunityOwnerOptions:[{value:'all',label:'全部负责人'}],opportunityOwnerIndex:0,executionTeamOptions:[{value:'all',label:'全部团队'}],
      executionTeamKey:'all',executionTeamIndex:0,executionTeamLabel:'全部团队',executionMemberName:'all'});
  },
  loadAllOpportunities({restoreLoaded=false}={}) {
    if (this.opportunityHidden) return;
    clearTimeout(this.opportunitySearchTimer);
    const context = workbenchContext(getApp());
    // Freeze only the list filters; the summary period has its own request lifecycle.
    const {includeOverview,overviewYear,overviewQuarters,...filters} = this.opportunityParams();
    const params = JSON.parse(JSON.stringify({...filters,includeOverview:false}));
    const pageKey = JSON.stringify([context, params]);
    if (this.opportunityListFlight && this.opportunityListFlight.key === pageKey) return this.opportunityListFlight.promise;
    const serial = this.opportunityListSerial = (this.opportunityListSerial || 0) + 1;
    const current = () => !this.opportunityHidden && serial === this.opportunityListSerial && context === workbenchContext(getApp());
    const preserve = this.opportunityPageKey === pageKey && this.listOpportunities && this.listOpportunities.length;
    this.opportunityPageKey = pageKey;
    this.opportunityPageContext = context;
    this.opportunityPageRequest = params;
    this.opportunityPageReady = false;
    this.opportunityOverviewSerial = (this.opportunityOverviewSerial || 0) + 1;
    this.opportunityOverviewFlight = null;
    if (!preserve) this.listOpportunities = [];
    this.setData({
      scope:this.opportunityScopeLabel(params),opportunityListLoading:true,
      opportunityDataReady:false,opportunityOverviewLoading:false,opportunityDataError:'',
      opportunityListError:'',opportunityMoreError:'',opportunityListChanged:false,opportunityLoadingMore:false,
      ...(preserve ? {} : {opportunityHasMore:false,opportunityTotal:0,opportunityNextOffset:null,
        opportunityItems:[],filteredOpportunities:[],opportunityGroups:[]})
    });
    const flight = {key:pageKey};
    const read = restoreLoaded && preserve
      ? readOpportunityWindow(apiClient.listOpportunities,params,this.listOpportunities.length,current,rows=>{
          const updates=new Map(this.decorateOpportunityRows(rows).map(row=>[row.id,row]));
          this.listOpportunities=this.listOpportunities.map(row=>updates.get(row.id)||row);
          const groups=groupOpportunitiesByQuarter(this.listOpportunities,{activeFirst:true});
          this.setData({opportunityItems:this.listOpportunities,opportunityGroups:groups,filteredOpportunities:[].concat(...groups.map(group=>group.items))});
        })
      : apiClient.listOpportunities({...params,pageSize:20,offset:0});
    flight.promise = read.then(response => {
      if (!current()) return;
      this.acceptOpportunityPage(response, false, 0);
      this.opportunityPageReady = true;
      this.workbenchLoadedAt = Date.now();
      this.setData({opportunityListLoading:false});
      // Start the full filtered statistics only after the first rows are visible.
      this.loadOpportunityOverview();
    }).catch(error => {
      if (!current()) return;
      if (!transientReadFailure(error)) this.clearOpportunityResults();
      else this.opportunityPageReady=Boolean(preserve);
      this.setData({opportunityListError:error.message || '商机列表加载失败，请重试'});
    }).finally(() => {
      if (this.opportunityListFlight === flight) this.opportunityListFlight = null;
      if (current()) this.setData({opportunityListLoading:false});
    });
    this.opportunityListFlight = flight;
    return flight.promise;
  },
  loadOpportunityOverview() {
    if (this.opportunityHidden) return;
    if(this.opportunityPageContext !== workbenchContext(getApp())) {this.clearOpportunityResults();return;}
    if (!this.opportunityPageReady) {
      if(this.data.opportunityListError&&!this.data.opportunityListLoading)return this.loadAllOpportunities();
      this.opportunityOverviewSerial = (this.opportunityOverviewSerial || 0) + 1;
      this.opportunityOverviewFlight = null;
      this.setData({opportunityDataReady:false,opportunityDataError:'',opportunityOverviewLoading:false});
      return;
    }
    const selection = {...this.data.summaryQuarter,quarters:[...this.data.summaryQuarter.quarters]};
    const listSerial = this.opportunityListSerial, context = workbenchContext(getApp());
    const params = {...this.opportunityPageRequest,...overviewRequest(selection),pageSize:1,offset:0};
    const key = JSON.stringify([listSerial,context,params]);
    if (this.opportunityOverviewFlight && this.opportunityOverviewFlight.key === key) return this.opportunityOverviewFlight.promise;
    const serial = this.opportunityOverviewSerial = (this.opportunityOverviewSerial || 0) + 1;
    const current = () => !this.opportunityHidden && serial === this.opportunityOverviewSerial && listSerial === this.opportunityListSerial && context === workbenchContext(getApp());
    this.setData({opportunityOverviewLoading:true,opportunityDataReady:false,opportunityDataError:''});
    const flight = {key};
    flight.promise = apiClient.listOpportunities(params).then(response => {
      if (!current()) return;
      const metrics = validateFilteredOverview(response,selection);
      this.setData({opportunityBoard:metrics,opportunityDataReady:true,opportunityDataError:''});
    }).catch(error => {
      if (!current()) return;
      if (readDenied(error)) this.clearOpportunityResults();
      this.setData({opportunityDataReady:false,opportunityDataError:error.message || '商机统计加载失败，请重试'});
    }).finally(() => {
      if (this.opportunityOverviewFlight === flight) this.opportunityOverviewFlight = null;
      if (current()) this.setData({opportunityOverviewLoading:false});
    });
    this.opportunityOverviewFlight = flight;
    return flight.promise;
  },
  decorateOpportunityRows(rows) {
    const session=getApp().globalData.session||{};
    return rows.map(item=>({...decorateOpportunity(item),canEdit:access.canObject(session,'opportunity.update',item,'can_edit') &&
      (item.can_edit===true || !!session.permissions || session.role!=='sales' || item.owner_id===session.userId)}));
  },
  acceptOpportunityPage(response, append, offset=append?this.data.opportunityNextOffset:0) {
    this.setData({opportunityStageOptions:[{value:'all',label:'全部阶段'},...STAGES.map(s=>({value:s.code,label:s.text,selected:this.data.opportunitySelectedStages.includes(s.code)}))],
      opportunityGradeOptions:[{value:'all',label:'全部等级'},...OPPORTUNITY_GRADES.map(g=>({value:g.code,label:g.label}))]});
    if (!response || !Array.isArray(response.items) || !response.summary || !Number.isInteger(response.summary.total) || typeof response.has_more !== 'boolean' || response.has_more && (!response.items.length || !Number.isInteger(response.next_offset))) throw new Error('商机分页数据不完整');
    if(this.data.canViewTeam&&!Array.isArray(response.team_options))throw Error('团队目录暂不可用，请稍后重试');
    if (response.has_more && response.next_offset !== offset + response.items.length) throw Error('商机列表分页未继续，请重试');
    if (response.items.some(item=>!item.id) || new Set(response.items.map(item=>item.id)).size !== response.items.length) throw Error('商机列表正在变化，请刷新列表');
    const added = this.decorateOpportunityRows(response.items), previous = this.listOpportunities || [];
    const items = append ? previous.concat(added.filter(r => !previous.some(old => old.id === r.id))) : added;
    const facets = response.facets || {},
      ownerRows=response.owner_options||[],
      opportunityOwnerOptions = [{
        value: 'all',
        label: '全部负责人'
      }, ...ownerRows.filter(row=>this.data.executionTeamKey==='all'||(row.team_ids||[]).includes(this.data.executionTeamKey)).map(row => ({
        value:row.id,
        label:ownerRows.filter(other=>other.name===row.name).length>1?`${row.name} · ${row.account_code}`:row.name
      }))];
    const previousOwner = (this.data.opportunityOwnerOptions[this.data.opportunityOwnerIndex] || {}).value || 'all';
    if(this.data.canViewTeam&&!Array.isArray(response.owner_options))throw Error('负责人目录暂不可用，请稍后重试');
    if(previousOwner!=='all'&&!opportunityOwnerOptions.some(option=>option.value===previousOwner))throw Error('所选负责人已不在当前团队，请重新选择');
    const years = new Set([CURRENT_YEAR - 1, CURRENT_YEAR, CURRENT_YEAR + 1, this.data.summaryQuarter.year, this.data.listQuarter.year, ...(facets.years || [])]);
    const quarterYearOptions = [...years].filter(Number.isInteger).sort((a, b) => a - b).map(value => ({
      value,
      label: `${value}年`
    }));
    const summaryYearOptions = [{value: 'all', label: '全部年份'}, ...quarterYearOptions];
    const teamOptions=[{value:'all',label:'全部团队'},...(response.team_options||[]).map(team=>({value:team.id,label:team.name}))];
    if(this.data.executionTeamKey!=='all'&&!teamOptions.some(option=>option.value===this.data.executionTeamKey))throw Error('所选团队已不可用，请重新选择');
    const teamIndex=optionIndex(teamOptions,this.data.executionTeamKey);
    this.listOpportunities = items;
    this.setData({
      opportunityItems: items,
      filteredOpportunities: [].concat(...groupOpportunitiesByQuarter(items, {activeFirst: true}).map(group => group.items)),
      opportunityGroups: groupOpportunitiesByQuarter(items, {activeFirst: true}),
      opportunityTotal: response.summary.total,
      opportunityHasMore: response.has_more,
      opportunityNextOffset: response.next_offset,
      executionTeamOptions: teamOptions,
      executionTeamIndex: teamIndex,
      executionTeamLabel: teamOptions[teamIndex].label,
      opportunityOwnerOptions,
      opportunityOwnerIndex: optionIndex(opportunityOwnerOptions, previousOwner),
      quarterYearOptions,
      summaryYearOptions,
      summaryYearIndex: optionIndex(summaryYearOptions, this.data.summaryQuarter.year),
      listYearIndex: optionIndex(summaryYearOptions, this.data.listQuarter.isAllYears ? 'all' : this.data.listQuarter.year),
      opportunityMoreError: ''
    });
  },
  loadMoreOpportunities() {
    if(this.opportunityPageContext !== workbenchContext(getApp())) {this.clearOpportunityResults();return;}
    if(!this.opportunityHidden&&!this.opportunityPageReady&&this.data.opportunityListError&&!this.data.opportunityListLoading)return this.loadAllOpportunities();
    if (this.opportunityHidden || !this.opportunityPageReady || this.data.opportunityListLoading || this.data.opportunityLoadingMore || !this.data.opportunityHasMore) return;
    if (this.data.opportunityListChanged) return this.loadAllOpportunities();
    const serial = this.opportunityListSerial,
      context = workbenchContext(getApp()),
      current = () => !this.opportunityHidden && serial === this.opportunityListSerial && context === workbenchContext(getApp());
    this.setData({
      opportunityLoadingMore: true,
      opportunityMoreError: ''
    });
    return apiClient.listOpportunities({
      ...this.opportunityPageRequest,
      includeOverview:false,
      offset: this.data.opportunityNextOffset
    }).then(response => {
      if (!current()) return;
      if(response.summary && response.summary.total!==this.data.opportunityTotal) {
        this.setData({opportunityListChanged:true});
        throw Error('商机列表已变化，请刷新列表');
      }
      this.acceptOpportunityPage(response, true);
    }).catch(error => {
      if (!current()) return;
      if (readDenied(error)) this.clearOpportunityResults();
      this.setData({opportunityMoreError:error.message || '下一页加载失败'});
    }).finally(() => {
      if (current()) this.setData({
        opportunityLoadingMore: false
      });
    });
  },
  onReachBottom() {
    if (this.data.isFde) {
      const component = this.selectComponent('#fdeContent');
      if (component) component.prefetchMore();
    } else if (!this.data.opportunityMoreError && !this.data.opportunityListError) this.loadMoreOpportunities();
  },
  applyOpportunitySummary() {
    return this.loadOpportunityOverview();
  },
  onHide() {
    this.opportunityHidden = true;
    this.opportunityListFlight = this.opportunityOverviewFlight = null;
    clearTimeout(this.opportunitySearchTimer);
    if(this.data.opportunityListLoading)this.workbenchLoadedAt=0;
    this.workbenchLoading=null;
    this.workbenchRequestId = (this.workbenchRequestId || 0) + 1;
    this.opportunityListSerial = (this.opportunityListSerial || 0) + 1;
    this.opportunityOverviewSerial = (this.opportunityOverviewSerial || 0) + 1;
    this.setData({opportunityListLoading:false,opportunityLoadingMore:false,opportunityOverviewLoading:false});
  },
  onUnload() {this.onHide();},
  setOpportunityQuarterSelection(e) {
    const scope=e&&((e.currentTarget&&e.currentTarget.dataset)||e.dataset||{}).scope,detail=e&&e.detail||{};
    const {year,quarters}=detail;
    if(!['summary','list'].includes(scope)||!Array.isArray(quarters)||quarters.some(q=>!Number.isInteger(q)||q<1||q>4)||year!=='all'&&(!Number.isInteger(year)||year<2000||year>2100)||year==='all'&&quarters.length)return false;
    const previous=this.data[`${scope}Quarter`],selected=[...new Set(quarters)].sort();
    const selection=scope==='list'&&year==='all'?listQuarterSelection(Number.isInteger(previous.year)?previous.year:CURRENT_YEAR):summaryQuarterSelection(year,selected);
    const years=[...new Set([...this.data.summaryYearOptions.map(o=>o.value).filter(Number.isInteger),...(year==='all'?[]:[year])])].sort((a,b)=>a-b);
    const quarterYearOptions=years.map(value=>({value,label:`${value}年`})),summaryYearOptions=[{value:'all',label:'全部年份'},...quarterYearOptions];
    const patch={[`${scope}Quarter`]:selection,[`${scope}YearIndex`]:optionIndex(summaryYearOptions,year),summaryYearOptions,quarterYearOptions,...(scope==='list'?{opportunityCloseIndex:0}:{})};
    // A year inserted for one control must not shift the other control's index.
    const other=scope==='list'?'summary':'list',otherSelection=this.data[`${other}Quarter`];
    patch[`${other}YearIndex`]=optionIndex(summaryYearOptions,otherSelection.isAllYears?'all':otherSelection.year);
    this.setData({...patch,opportunityFilterActive:opportunityFiltersActive({...this.data,...patch})});
    return scope === 'summary' ? this.applyOpportunitySummary() : this.loadAllOpportunities();
  },
  toggleQuarter(e) {
    const scope = e.currentTarget.dataset.scope;
    if (!["summary", "list"].includes(scope)) return;
    const key = `${scope}Quarter`;
    const previous = this.data[key];
    const value = e.currentTarget.dataset.value;
    if (scope === 'summary') {
      if (previous.isAllYears) return;
      this.setData({summaryQuarter: toggleSummaryQuarter(previous, value)}, () => this.applyOpportunitySummary());
      return;
    }
    const listQuarter = toggleListQuarter(previous, value);
    this.setData({listQuarter, opportunityCloseIndex: 0,
      listYearIndex: optionIndex(this.data.summaryYearOptions, listQuarter.isAllYears ? 'all' : listQuarter.year)
    }, () => this.applyOpportunityFilters());
  },
  changeQuarterYear(e) {
    const scope = e.currentTarget.dataset.scope;
    if (!["summary", "list"].includes(scope)) return;
    const index = Number(e.detail.value);
    const option = this.data.summaryYearOptions[index];
    if (!option) return;
    const previous = this.data[`${scope}Quarter`];
    this.setData({
      [`${scope}Quarter`]: scope === 'summary' ? summaryQuarterSelection(option.value) : option.value === 'all' ? listQuarterSelection(previous.year) : summaryQuarterSelection(option.value),
      [`${scope}YearIndex`]: index,
      ...(scope === "list" ? {
        opportunityCloseIndex: 0
      } : {})
    }, () => {
      if (scope === "summary") this.applyOpportunitySummary();else this.applyOpportunityFilters();
    });
  },
  resetSummaryQuarter() {
    const year = this.data.summaryQuarter.isAllYears ? CURRENT_YEAR : this.data.summaryQuarter.year;
    this.setData({
      summaryQuarter: summaryQuarterSelection(year),
      summaryYearIndex: optionIndex(this.data.summaryYearOptions, year)
    }, () => this.applyOpportunitySummary());
  },
  showOpportunityMetricHelp() {
    wx.showModal({
      title: "商机统计口径",
      showCancel: false,
      content: OPPORTUNITY_OVERVIEW_HELP
    });
  },
  createOpportunity() {
    wx.navigateTo({
      url: "/pages/opportunity-create/index"
    });
  },
  editOpportunity(e) {
    const {customerId, opportunityId} = e.currentTarget.dataset;
    const opportunity=(this.data.opportunityItems||[]).find(item=>String(item.id)===String(opportunityId));
    if (!customerId || !opportunity || !opportunity.canEdit || !access.canObject(getApp().globalData.session,'opportunity.update',opportunity,'can_edit')) return;
    this.workbenchLoadedAt = 0;
    wx.navigateTo({url: `/pages/opportunity-create/index?customerId=${encodeURIComponent(customerId)}&opportunityId=${encodeURIComponent(opportunityId)}`});
  },
  openOpportunity(e) {
    const customerId = e.currentTarget.dataset.customerId;
    const opportunityId = e.currentTarget.dataset.opportunityId;
    if (!customerId || !opportunityId) return;
    wx.navigateTo({
      url: `/pages/customer-assets/index?customer_id=${encodeURIComponent(customerId)}&opportunity_id=${encodeURIComponent(opportunityId)}&period=all&readonly=1`
    });
  },
  changeOpportunityFilter(e) {
    const key = e.currentTarget.dataset.key;
    if (key === 'Activity') {
      const index = Number(e.detail.value), option = this.data.activityWindowOptions[index];
      if (!option) return;
      this.setData({activityWindowIndex: index, activityWindow: option.value}, () => this.applyOpportunityFilters());
      return;
    }
    this.setData({
      ...(key === 'Owner' && this.data.canViewTeam ? {executionScope:access.can(getApp().globalData.session,'opportunity.shared_read')?'authorized':'team',executionScopeIndex:1} : {}),
      [`opportunity${key}Index`]: Number(e.detail.value)
    }, () => this.applyOpportunityFilters());
  },
  toggleOpportunityQuarterFilter() {
    this.setData({
      showOpportunityQuarterFilter: !this.data.showOpportunityQuarterFilter,
      showOpportunityStageFilter: false
    });
  },
  selectOpportunityClosePeriod(e) {
    const index = Number(e.currentTarget.dataset.index);
    if (!this.data.opportunityCloseOptions[index]) return;
    this.setData({
      opportunityCloseIndex: index,
      listQuarter: listQuarterSelection(CURRENT_YEAR),
      listYearIndex: 0
    }, () => this.applyOpportunityFilters());
  },
  toggleOpportunityStageFilter() {
    this.setData({
      showOpportunityStageFilter: !this.data.showOpportunityStageFilter,
      showOpportunityQuarterFilter: false
    });
  },
  setOpportunityStages(e) {
    const values=e&&e.detail&&e.detail.value,codes=STAGES.map(stage=>stage.code);
    if(!Array.isArray(values)||values.some(value=>!codes.includes(value)))return false;
    const selected=codes.filter(code=>values.includes(code));
    const patch={opportunitySelectedStages:selected,opportunityStageOptions:this.data.opportunityStageOptions.map(item=>({...item,selected:item.value!=='all'&&selected.includes(item.value)})),opportunityStageLabel:selected.length?`已选${selected.length}项`:'全部阶段'};
    this.setData({...patch,opportunityFilterActive:opportunityFiltersActive({...this.data,...patch})});
    return this.loadAllOpportunities();
  },
  toggleOpportunityStage(e) {
    const value = e.currentTarget.dataset.value;
    const selected = value === "all" ? [] : this.data.opportunitySelectedStages.includes(value) ? this.data.opportunitySelectedStages.filter(item => item !== value) : [...this.data.opportunitySelectedStages, value];
    return this.setOpportunityStages({detail:{value:selected}});
  },
  searchOpportunities(e) {
    this.opportunityPageReady = false;
    this.opportunityListFlight = this.opportunityOverviewFlight = null;
    this.opportunityOverviewSerial = (this.opportunityOverviewSerial || 0) + 1;
    clearTimeout(this.opportunitySearchTimer);
    this.opportunityListSerial = (this.opportunityListSerial || 0) + 1;
    this.listOpportunities = [];
    this.setData({
      opportunityQuery: e.detail.value,
      opportunityListLoading: true,
      opportunityDataReady:false,opportunityOverviewLoading:true,opportunityDataError:'',
      opportunityLoadingMore: false,
      opportunityItems: [], filteredOpportunities: [], opportunityGroups: [],
      opportunityTotal: 0, opportunityHasMore: false, opportunityNextOffset: null,
      opportunityListError: '', opportunityMoreError: ''
    });
    const context = workbenchContext(getApp());
    this.opportunitySearchTimer = setTimeout(() => {
      if (context === workbenchContext(getApp())) this.applyOpportunityFilters();
    }, 250);
  },
  applyOpportunityFilters() {
    this.setData({opportunityFilterActive:opportunityFiltersActive(this.data)});
    return this.loadAllOpportunities();
  },
  onShow() {
    this.opportunityHidden = false;
    const app = getApp();
    if (app.guardPage && !app.guardPage(this, 'workbench')) return;
    if (require('../../utils/access').fdeProjectView(app.globalData.session)) {
      this.setData({
        isFde: true
      });
      return;
    }
    this.setData({
      isFde: false
    });
    if (!app.ensureLogin()) return;
    const fresh = this.opportunityPageReady && this.workbenchContext === workbenchContext(app) && Date.now() - (this.workbenchLoadedAt || 0) < WORKBENCH_REFRESH_INTERVAL;
    if (fresh) {
      if(!this.data.opportunityDataReady)return this.loadOpportunityOverview();
      return;
    }
    return this.loadData({restoreLoaded:true});
  },
  loadData({restoreLoaded=false}={}) {
    const app = getApp(),
      session = app.globalData.session,
      role = app.globalData.role,
      roleInfo = app.globalData.roles[role],
      context = workbenchContext(app);
    if (this.workbenchLoading && this.workbenchContext === context) return this.workbenchLoading;
    const sameIdentity = this.workbenchContext === context;
    this.workbenchContext = context;
    const requestId = this.workbenchRequestId = (this.workbenchRequestId || 0) + 1;
    if (!sameIdentity) {
      clearTimeout(this.opportunitySearchTimer);
      this.listOpportunities = [];
      this.setData({
        ...access.readStateReset('workbench'),
        opportunityQuery: '',
        opportunityPeriods: opportunityPeriods.initial(),
        opportunityFilterActive: false,
        summaryQuarter: summaryQuarterSelection(CURRENT_YEAR),
        summaryYearIndex: optionIndex(this.data.summaryYearOptions, CURRENT_YEAR),
        listQuarter: listQuarterSelection(CURRENT_YEAR),
        listYearIndex: 0,
        opportunityOwnerIndex: 0,
        opportunityOwnerOptions: [{
          value: 'all',
          label: '全部负责人'
        }],
        opportunitySelectedStages: [],
        opportunityStageLabel: '全部阶段',
        opportunityStageOptions: this.data.opportunityStageOptions.map(item => ({
          ...item,
          selected: false
        })),
        opportunityCloseIndex: 0,
        opportunityGradeIndex: 0,
        activityWindowIndex: 4,
        activityWindow: 'all',
        executionScope: access.can(session,'opportunity.shared_read')?'authorized':'self',
        executionScopeIndex: access.can(session,'opportunity.shared_read')?1:0,
        executionTeamKey: 'all',
        executionTeamIndex: 0,
        executionTeamLabel: '全部团队',
        executionTeamOptions: [{
          value: 'all',
          label: '全部团队'
        }],
        executionMemberName: 'all',
        opportunityDataReady: false,
        opportunityBoard: {}
      });
    }
    // The page has only an overview and a browsable list. Neither waits for
    // unrelated customer/task/risk projections from the legacy workbench API.
    this.setData({
      role,
      roleName: session.roleName || roleInfo.name,
      filterRole: role,
      canViewTeam: access.canViewTeam(session, 'opportunity.read'),
      canViewAuthorized:access.canViewTeam(session,'opportunity.read')||access.can(session,'opportunity.shared_read'),
      executionScopeOptions:[{value:'self',label:'我的'},{value:access.can(session,'opportunity.shared_read')?'authorized':'team',label:'授权范围'}],
      ...(!access.canViewTeam(session,'opportunity.read')&&!access.can(session,'opportunity.shared_read') ? {executionScope:'self',executionScopeIndex:0} : {}),
      canCreateOpportunity: can(session,'opportunity.create'),
      dataReady: true,
      loading: false,
      loadError: ''
    });
    this.workbenchLoading = Promise.allSettled([this.loadAllOpportunities({restoreLoaded})]).then(() => {
      if (requestId !== this.workbenchRequestId || context !== workbenchContext(getApp())) return;
      if (!this.data.opportunityListError && !this.data.opportunityDataError) this.workbenchLoadedAt = Date.now();
    }).finally(() => {
      if (requestId === this.workbenchRequestId) this.workbenchLoading = null;
    });
    return this.workbenchLoading;
  },
  changeExecutionScope(e) {
    const index = Number(e.detail.value), option = this.data.executionScopeOptions[index];
    if (!option || (option.value !== 'self' && !this.data.canViewAuthorized)) return;
    this.setData({executionScope: option.value, executionScopeIndex: index, executionTeamKey: 'all', executionTeamIndex: 0,
      executionTeamLabel: '全部团队', executionMemberName:'all', opportunityOwnerIndex: 0}, () => this.applyOpportunityFilters());
  },
  changeActivityWindow(e) {
    const index = Number(e.detail.value), option = this.data.activityWindowOptions[index];
    if (!option) return;
    this.setData({activityWindowIndex: index, activityWindow: option.value}, () => this.applyOpportunityFilters());
  },
  changeExecutionTeam(e) {
    const index = Number(e.detail.value),
      option = this.data.executionTeamOptions[index];
    if (!option || !this.data.canViewTeam) return;
    this.setData({
      executionScope: access.can(getApp().globalData.session,'opportunity.shared_read')?'authorized':'team',
      executionScopeIndex: 1,
      executionTeamIndex: index,
      executionTeamKey: option.value,
      executionTeamLabel: option.label,
      opportunityOwnerIndex: 0
    }, () => this.applyOpportunityFilters());
  },
  resetOpportunityFilters() {
    this.setData({
      opportunityPeriods: opportunityPeriods.initial(),
      opportunityQuery: '',
      listQuarter: listQuarterSelection(this.data.listQuarter.year),
      listYearIndex: 0,
      opportunityOwnerIndex: 0,
      opportunitySelectedStages: [],
      opportunityStageLabel: '全部阶段',
      opportunityStageOptions: this.data.opportunityStageOptions.map(item => ({
        ...item,
        selected: false
      })),
      showOpportunityQuarterFilter: false,
      showOpportunityStageFilter: false,
      opportunityCloseIndex: 0,
      opportunityGradeIndex: 0,
      activityWindowIndex: 4,
      activityWindow: 'all',
      executionScope:access.can(getApp().globalData.session,'opportunity.shared_read')?'authorized':'self',
      executionScopeIndex:access.can(getApp().globalData.session,'opportunity.shared_read')?1:0,
      executionTeamIndex: 0,
      executionTeamKey: 'all',
      executionTeamLabel: '全部团队',
      executionMemberName: 'all'
    }, () => this.applyOpportunityFilters());
  }
});
