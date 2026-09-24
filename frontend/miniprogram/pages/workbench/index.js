const apiClient = require('../../utils/apiClient');
const {can} = require('../../utils/access');
const {
  STAGES,
  OPPORTUNITY_GRADES
} = require('../../utils/opportunity');
const {
  beijingDateParts,
  quarterSelection,
  groupOpportunitiesByQuarter,
  OPPORTUNITY_OVERVIEW_HELP
} = require('../../utils/opportunityQuarter');
const CURRENT_YEAR = beijingDateParts(new Date()).year;
const WORKBENCH_REFRESH_INTERVAL = 30000;
const { decorateOpportunity } = require('../../utils/opportunityListCard');
function optionIndex(options, value) {
  const index = options.findIndex(item => item.value === value);
  return index < 0 ? 0 : index;
}
function workbenchContext(app) {
  const session = app.globalData.session || {};
  return JSON.stringify([session.workspaceId, session.userId, app.globalData.role, session.loginAt, session.permissionVersion, session.scope, session.teamIds]);
}
Page({
  data: {
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
    opportunityDataReady: false,
    opportunityDataError: "",
    opportunityListLoading: false,
    opportunityOverviewLoading: false,
    opportunityListError: "",
    summaryQuarter: quarterSelection(CURRENT_YEAR),
    listQuarter: quarterSelection(CURRENT_YEAR),
    quarterYearOptions: [CURRENT_YEAR - 1, CURRENT_YEAR, CURRENT_YEAR + 1].map(value => ({
      value,
      label: `${value}年`
    })),
    summaryYearIndex: 1,
    listYearIndex: 1,
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
    opportunityFilterActive: false, canViewTeam:false,
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
    return {
      pageSize: 20,
      includeClosed: true,
      order: 'quarter_stage',
      query: this.data.opportunityQuery.trim(),
      teamId: this.data.executionTeamKey,
      owner: owner === 'all' ? null : owner,
      stages: this.data.opportunitySelectedStages,
      closePeriod: this.data.opportunityCloseOptions[this.data.opportunityCloseIndex].value,
      grade: this.data.opportunityGradeOptions[this.data.opportunityGradeIndex].value,
      year: this.data.listQuarter.year,
      quarters: this.data.listQuarter.quarters
    };
  },
  loadAllOpportunities() {
    clearTimeout(this.opportunitySearchTimer);
    const serial = this.opportunityListSerial = (this.opportunityListSerial || 0) + 1,
      context = workbenchContext(getApp());
    const current = () => serial === this.opportunityListSerial && context === workbenchContext(getApp());
    const params = this.opportunityParams(),
      pageKey = JSON.stringify([context, params]);
    const preserve = this.opportunityPageKey === pageKey && this.listOpportunities && this.listOpportunities.length;
    this.opportunityPageKey = pageKey;
    this.opportunityPageRequest = params;
    if (!preserve) this.listOpportunities = [];
    this.setData({
      opportunityListLoading: true,
      opportunityListError: '',
      opportunityMoreError: '',
      opportunityLoadingMore: false,
      ...(preserve ? {} : {
        opportunityHasMore: false,
        opportunityTotal: 0,
        opportunityItems: [],
        filteredOpportunities: [],
        opportunityGroups: []
      })
    });
    return apiClient.listOpportunities({
      ...this.opportunityPageRequest,
      offset: 0
    }).then(response => {
      if (current()) this.acceptOpportunityPage(response, false);
    }).catch(error => {
      if (current()) this.setData({
        opportunityListError: error.message || '商机列表加载失败，请重试'
      });
    }).finally(() => {
      if (current()) this.setData({
        opportunityListLoading: false
      });
    });
  },
  acceptOpportunityPage(response, append) {
    this.setData({opportunityStageOptions:[{value:'all',label:'全部阶段'},...STAGES.map(s=>({value:s.code,label:s.text,selected:this.data.opportunitySelectedStages.includes(s.code)}))],
      opportunityGradeOptions:[{value:'all',label:'全部等级'},...OPPORTUNITY_GRADES.map(g=>({value:g.code,label:g.label}))]});
    if (!response || !Array.isArray(response.items) || !response.summary || !Number.isInteger(response.summary.total) || typeof response.has_more !== 'boolean' || response.has_more && (!response.items.length || !Number.isInteger(response.next_offset))) throw new Error('商机分页数据不完整');
    if(this.data.canViewTeam&&!Array.isArray(response.team_options))throw Error('团队目录暂不可用，请稍后重试');
    const session = getApp().globalData.session || {};
    const added = response.items.map(item => ({...decorateOpportunity(item), canEdit: can(session, 'opportunity.update') && (item.can_edit === true || (!session.permissions && (session.role !== 'sales' || item.owner_id === session.userId)))})),
      previous = this.listOpportunities || [];
    const items = append ? previous.concat(added.filter(r => !previous.some(old => old.id === r.id))) : added;
    this.listOpportunities = items;
    const facets = response.facets || {},
      opportunityOwnerOptions = [{
        value: 'all',
        label: '全部负责人'
      }, ...(facets.owners || []).filter(value => this.data.executionMemberName === 'all' || value === this.data.executionMemberName).map(value => ({
        value,
        label: value
      }))];
    const previousOwner = (this.data.opportunityOwnerOptions[this.data.opportunityOwnerIndex] || {}).value || 'all';
    const years = new Set([CURRENT_YEAR - 1, CURRENT_YEAR, CURRENT_YEAR + 1, this.data.summaryQuarter.year, this.data.listQuarter.year, ...(facets.years || [])]);
    const quarterYearOptions = [...years].sort((a, b) => a - b).map(value => ({
      value,
      label: `${value}年`
    }));
    const teamOptions=[{value:'all',label:'全部团队'},...(response.team_options||[]).map(team=>({value:team.id,label:team.name}))];
    const teamIndex=optionIndex(teamOptions,this.data.executionTeamKey);
    this.setData({
      opportunityItems: items,
      filteredOpportunities: [].concat(...groupOpportunitiesByQuarter(items).map(group => group.items)),
      opportunityGroups: groupOpportunitiesByQuarter(items),
      opportunityTotal: response.summary.total,
      opportunityHasMore: response.has_more,
      opportunityNextOffset: response.next_offset,
      executionTeamOptions: teamOptions,
      executionTeamIndex: teamIndex,
      executionTeamLabel: teamOptions[teamIndex].label,
      opportunityOwnerOptions,
      opportunityOwnerIndex: optionIndex(opportunityOwnerOptions, previousOwner),
      quarterYearOptions,
      summaryYearIndex: optionIndex(quarterYearOptions, this.data.summaryQuarter.year),
      listYearIndex: optionIndex(quarterYearOptions, this.data.listQuarter.year),
      opportunityMoreError: ''
    });
  },
  loadMoreOpportunities() {
    if (this.data.opportunityListLoading || this.data.opportunityLoadingMore || !this.data.opportunityHasMore) return;
    const serial = this.opportunityListSerial,
      context = workbenchContext(getApp()),
      current = () => serial === this.opportunityListSerial && context === workbenchContext(getApp());
    this.setData({
      opportunityLoadingMore: true,
      opportunityMoreError: ''
    });
    return apiClient.listOpportunities({
      ...this.opportunityPageRequest,
      offset: this.data.opportunityNextOffset
    }).then(response => {
      if (current()) this.acceptOpportunityPage(response, true);
    }).catch(error => {
      if (current()) this.setData({
        opportunityMoreError: error.message || '下一页加载失败'
      });
    }).finally(() => {
      if (current()) this.setData({
        opportunityLoadingMore: false
      });
    });
  },
  onReachBottom() {
    if (this.data.isFde) {
      const component = this.selectComponent('#fdeContent');
      if (component) component.loadMore();
    } else this.loadMoreOpportunities();
  },
  applyOpportunitySummary() {
    const serial = this.opportunityOverviewSerial = (this.opportunityOverviewSerial || 0) + 1;
    const context = workbenchContext(getApp());
    const current = () => serial === this.opportunityOverviewSerial && context === workbenchContext(getApp());
    const selection = this.data.summaryQuarter;
    this.setData({
      opportunityDataReady: false,
      opportunityOverviewLoading: true,
      opportunityDataError: ""
    });
    return apiClient.getOpportunityOverview(selection).then(response => {
      if (!current()) return;
      const metrics = response && response.metrics;
      if (!metrics || !['won', 'total', 'active', 'newCount', 'missingCloseDates', 'missingWonDates', 'missingCreatedDates'].every(key => Number.isInteger(metrics[key]) && metrics[key] >= 0)) throw new Error('商机总览数据不完整');
      this.setData({
        opportunityBoard: metrics,
        opportunityDataReady: true
      });
    }).catch(error => {
      if (current()) this.setData({
        opportunityDataError: error.message || '商机总览暂不可用'
      });
    }).finally(() => {
      if (current()) this.setData({
        opportunityOverviewLoading: false
      });
    });
  },
  onUnload() {
    clearTimeout(this.opportunitySearchTimer);
    this.workbenchRequestId = (this.workbenchRequestId || 0) + 1;
    this.opportunityListSerial = (this.opportunityListSerial || 0) + 1;
    this.opportunityOverviewSerial = (this.opportunityOverviewSerial || 0) + 1;
  },
  toggleQuarter(e) {
    const scope = e.currentTarget.dataset.scope;
    if (!["summary", "list"].includes(scope)) return;
    const key = `${scope}Quarter`;
    const previous = this.data[key];
    const value = e.currentTarget.dataset.value;
    const quarter = Number(value);
    if (value !== "all" && ![1, 2, 3, 4].includes(quarter)) return;
    const quarters = value === "all" ? [] : previous.quarters.includes(quarter) ? previous.quarters.filter(item => item !== quarter) : [...previous.quarters, quarter];
    this.setData({
      [key]: quarterSelection(previous.year, quarters),
      ...(scope === "list" ? {
        opportunityCloseIndex: 0
      } : {})
    }, () => {
      if (scope === "summary") this.applyOpportunitySummary();else this.applyOpportunityFilters();
    });
  },
  changeQuarterYear(e) {
    const scope = e.currentTarget.dataset.scope;
    if (!["summary", "list"].includes(scope)) return;
    const index = Number(e.detail.value);
    const option = this.data.quarterYearOptions[index];
    if (!option) return;
    const previous = this.data[`${scope}Quarter`];
    this.setData({
      [`${scope}Quarter`]: quarterSelection(option.value, scope === 'summary' || !previous.quarters.length ? [1, 2, 3, 4] : previous.quarters),
      [`${scope}YearIndex`]: index,
      ...(scope === "list" ? {
        opportunityCloseIndex: 0
      } : {})
    }, () => {
      if (scope === "summary") this.applyOpportunitySummary();else this.applyOpportunityFilters();
    });
  },
  resetSummaryQuarter() {
    this.setData({
      summaryQuarter: quarterSelection(this.data.summaryQuarter.year)
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
    if (!customerId || !opportunityId) return;
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
    this.setData({
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
      listQuarter: quarterSelection(CURRENT_YEAR),
      listYearIndex: optionIndex(this.data.quarterYearOptions, CURRENT_YEAR)
    }, () => this.applyOpportunityFilters());
  },
  toggleOpportunityStageFilter() {
    this.setData({
      showOpportunityStageFilter: !this.data.showOpportunityStageFilter,
      showOpportunityQuarterFilter: false
    });
  },
  toggleOpportunityStage(e) {
    const value = e.currentTarget.dataset.value;
    const selected = value === "all" ? [] : this.data.opportunitySelectedStages.includes(value) ? this.data.opportunitySelectedStages.filter(item => item !== value) : [...this.data.opportunitySelectedStages, value];
    const opportunityStageOptions = this.data.opportunityStageOptions.map(item => ({
      ...item,
      selected: item.value !== "all" && selected.includes(item.value)
    }));
    this.setData({
      opportunitySelectedStages: selected,
      opportunityStageOptions,
      opportunityStageLabel: selected.length ? `已选${selected.length}项` : "全部阶段"
    }, () => this.applyOpportunityFilters());
  },
  searchOpportunities(e) {
    clearTimeout(this.opportunitySearchTimer);
    this.opportunityListSerial = (this.opportunityListSerial || 0) + 1;
    this.listOpportunities = [];
    this.setData({
      opportunityQuery: e.detail.value,
      opportunityListLoading: true,
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
    this.setData({
      opportunityFilterActive: Boolean(this.data.opportunityQuery.trim()) || this.data.listQuarter.quarters.length > 0 || [this.data.opportunityOwnerIndex, this.data.opportunityCloseIndex, this.data.opportunityGradeIndex].some(index => index > 0) || this.data.opportunitySelectedStages.length > 0 || this.data.canViewTeam && this.data.executionTeamIndex > 0
    });
    return this.loadAllOpportunities();
  },
  onShow() {
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
    const fresh = this.data.dataReady && this.workbenchContext === workbenchContext(app) && Date.now() - (this.workbenchLoadedAt || 0) < WORKBENCH_REFRESH_INTERVAL;
    if (!fresh) return this.loadData();
  },
  loadData() {
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
        opportunityQuery: '',
        opportunityFilterActive: false,
        summaryQuarter: quarterSelection(CURRENT_YEAR),
        listQuarter: quarterSelection(CURRENT_YEAR),
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
      scope: roleInfo.scope,
      filterRole: role,
      canCreateOpportunity: can(session,'opportunity.create'),
      dataReady: true,
      loading: false,
      loadError: ''
    });
    this.workbenchLoading = Promise.allSettled([this.loadAllOpportunities(), this.applyOpportunitySummary()]).then(() => {
      if (requestId !== this.workbenchRequestId || context !== workbenchContext(getApp())) return;
      if (!this.data.opportunityListError && !this.data.opportunityDataError) this.workbenchLoadedAt = Date.now();
    }).finally(() => {
      if (requestId === this.workbenchRequestId) this.workbenchLoading = null;
    });
    return this.workbenchLoading;
  },
  changeExecutionTeam(e) {
    const index = Number(e.detail.value),
      option = this.data.executionTeamOptions[index];
    if (!option) return;
    this.setData({
      executionTeamIndex: index,
      executionTeamKey: option.value,
      executionTeamLabel: option.label,
      opportunityOwnerIndex: 0
    }, () => this.applyOpportunityFilters());
  },
  resetOpportunityFilters() {
    this.setData({
      opportunityQuery: '',
      listQuarter: quarterSelection(this.data.listQuarter.year),
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
      executionTeamIndex: 0,
      executionTeamKey: 'all',
      executionTeamLabel: '全部团队',
      executionMemberName: 'all'
    }, () => this.applyOpportunityFilters());
  }
});
