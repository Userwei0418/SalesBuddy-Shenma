const opportunityPeriods = require('../../utils/opportunityPeriodFilters');
const { opportunitySignal } = require("../../utils/customerSignals");
const apiClient = require("../../utils/apiClient");
const { cardFields } = require("../../utils/opportunityCard");
const { amountText, opportunityProbability } = require("../../utils/customerDetail");
const { STAGES, OPPORTUNITY_GRADES, gradeOfAmount, stageOf } = require("../../utils/opportunity");

const access = require("../../utils/access");
const {assertPersonDirectory}=require("../../utils/personPicker");
const { groupOpportunitiesByQuarter, expectedCloseQuarter } = require("../../utils/opportunityQuarter");

function optionIndex(options, value) { const index = options.findIndex((item) => item.value === value); return index < 0 ? 0 : index; }
function dateText(value) {
  if (!value) return "待确认";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

Page({
  // This shared list card is read-only here; editing stays in the authorized detail/workbench routes.
  editOpportunity(){},
  data: {
    opportunityPeriods: opportunityPeriods.initial(),
    fdeScope: '', fdeMemberId: '', fdeCustomerId: '',
    role: "sales", canViewTeam:false, scope: "", loading: false, canCreate:false, opportunities: [], filtered: [], opportunityGroups: [], totalAmount: "—", total:0, hasMore:false, nextOffset:null, loadingMore:false, loadError:"", moreError:"", filterActive: false,
    ownerPickerOpen:false,ownerPickerMembers:[],ownerPickerTeams:[],ownerPickerDefaultTeamId:'',ownerPickerSelected:[],legacyOwnerName:'',
    teamOptions: [{ value: "all", label: "全部团队" }], teamIndex: 0,
    ownerOptions: [{ value: "all", label: "全部负责人" }], ownerIndex: 0,
    stageOptions: STAGES.map((item) => ({ value: item.code, label: item.text, selected: false })), selectedStages: [], stageLabel: "全部阶段", showStageFilter: false,
    closeOptions: [{ value: "all", label: "全部关单日期" }, { value: "month", label: "当月" }, { value: "quarter", label: "当季度" }, { value: "year", label: "当年" }], closeIndex: 0,
    activityWindowOptions: [{value:'today',label:'今天有跟进'}, {value:'3d',label:'近3天有跟进'}, {value:'week',label:'本周有跟进'}, {value:'month',label:'本月有跟进'}, {value:'all',label:'全部跟进'}], activityWindowIndex: 4,
    gradeOptions: [{ value: "all", label: "全部等级" }, ...OPPORTUNITY_GRADES.map((item) => ({ value: item.code, label: item.label }))], gradeIndex: 0,
  },
  onLoad(options = {}) {
    // The guard mounts FDE content, so its first request needs the route context already set.
    this.setData({fdeScope:options.scope||'',fdeMemberId:options.member_id||'',fdeCustomerId:options.customer_id||''});
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'opportunities', options)) return;
    this.teamFilter = decodeURIComponent(options.team_id || options.team || "all");
    this.routeTeamId = Boolean(options.team_id);
    this.memberFilter = decodeURIComponent(options.member || "all");
  },
  onShow() {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'opportunities')) return;
    if (access.fdeProjectView(getApp().globalData.session)) {
      this.setData({
        isFde: true
      });
      return;
    }
    this.setData({
      isFde: false
    });
    if (!getApp().ensureLogin()) return;
    return this.loadPage();
  },
  onHide() { this.setData({ownerPickerOpen:false}); },
  onUnload() {
    this.onHide();
    this.closed = true;
    this.loadSerial = (this.loadSerial || 0) + 1;
  },
  onReachBottom() {
    if (this.data.isFde) {
      const component = this.selectComponent('#fdeContent');
      if (component) component.prefetchMore();
    } else if (!this.data.moreError && !this.data.loadError) this.loadMore();
  },
  pageParams() {
    const owner = (this.data.ownerOptions[this.data.ownerIndex] || {}).value || this.memberFilter;
    return {
      ...opportunityPeriods.params(this.data.opportunityPeriods),
      pageSize: 20,
      includeClosed: true,
      order: 'quarter_stage',
      ...(access.can(getApp().globalData.session,'opportunity.shared_read')?{scope:'authorized'}:{}),
      teamId: this.data.canViewTeam ? (this.data.teamOptions[this.data.teamIndex] || {}).value || this.teamFilter : null,
      ownerId: this.data.canViewTeam && owner !== 'all' ? owner : null,
      owner: this.data.canViewTeam && owner === 'all' ? this.data.legacyOwnerName || null : null,
      stages: this.data.selectedStages,
      closePeriod: this.data.closeOptions[this.data.closeIndex].value,
      grade: this.data.gradeOptions[this.data.gradeIndex].value,
      activity_window: this.data.activityWindowOptions[this.data.activityWindowIndex].value
    };
  },
  changeOpportunityPeriods(e){const value=e.detail.value;opportunityPeriods.params(value);this.setData({opportunityPeriods:value},()=>this.applyFilters());},
  async loadPage() {
    const app = getApp(),
      session = app.globalData.session || {},
      role = app.globalData.role;
    const identity = access.identity(session),
      serial = this.loadSerial = (this.loadSerial || 0) + 1;
    if (this.identity !== undefined && this.identity !== identity) {
      this.teamFilter = 'all';
      this.memberFilter = 'all';
      this.setData({
        ...access.readStateReset('opportunities'),
        opportunityPeriods: opportunityPeriods.initial(),
        teamIndex: 0,
        ownerIndex: 0,ownerPickerOpen:false,ownerPickerMembers:[],ownerPickerTeams:[],ownerPickerDefaultTeamId:'',ownerPickerSelected:[],legacyOwnerName:'',
        teamOptions: [{
          value: 'all',
          label: '全部团队'
        }],
        ownerOptions: [{
          value: 'all',
          label: '全部负责人'
        }],
        selectedStages: [],
        stageOptions: this.data.stageOptions.map(r => ({
          ...r,
          selected: false
        })),
        stageLabel: '全部阶段',
        closeIndex: 0,
        gradeIndex: 0,
        activityWindowIndex: 4
      });
    }
    this.identity = identity;
    this.setData({
      role,
      canViewTeam:access.canViewTeam(session,'opportunity.read'),
      canCreate: access.can(session,'opportunity.create'),
      scope: access.can(session,'opportunity.shared_read')?'授权范围':access.scopeLabel(session,'opportunity.read'),
      loading: true,
      loadError: '',
      moreError: '',
      opportunities: [],
      filtered: [],
      opportunityGroups: [],
      total: 0,
      totalAmount: '—',
      hasMore: false,
      loadingMore: false
    });
    const params = this.pageParams();
    if (!this.loadedOnce) {
      if (this.teamFilter && this.teamFilter !== 'all' && this.data.canViewTeam) params[this.routeTeamId ? 'teamId' : 'team'] = this.teamFilter;
      if (this.memberFilter && this.memberFilter !== 'all' && this.data.canViewTeam) params.owner = this.memberFilter;
    }
    this.pageRequest = params;
    const current = () => !this.closed && serial === this.loadSerial && identity === access.identity(getApp().globalData.session);
    try {
      const response = await apiClient.listOpportunities({
        ...params,
        offset: 0
      });
      if (current()) {
        this.acceptPage(response, false);
        this.loadedOnce = true;
      }
    } catch (error) {
      if (current()) this.setData({
        loadError: error.message || '商机加载失败'
      });
    } finally {
      if (current()) this.setData({
        loading: false
      });
    }
  },
  acceptPage(response, append) {
    this.setData({stageOptions:STAGES.map(s=>({value:s.code,label:s.text,selected:this.data.selectedStages.includes(s.code)})),
      gradeOptions:[{value:'all',label:'全部等级'},...OPPORTUNITY_GRADES.map(g=>({value:g.code,label:g.label}))]});
    if (!response || !Array.isArray(response.items) || !response.summary || !Number.isInteger(response.summary.total) || typeof response.has_more !== 'boolean' || response.has_more && (!response.items.length || !Number.isInteger(response.next_offset))) throw new Error('商机分页数据不完整');
    const added = response.items.map(item => {
      const stage = stageOf(item);
      const probability = opportunityProbability(item);
      const grade = gradeOfAmount(item.amount);
      const quarter = !item.expected_close_date && expectedCloseQuarter(item);
      return {
        ...item,
        canEdit: false,
        owner: item.owner_name || "待分配",
        team: item.team_name || "待分配团队",
        ...cardFields(item),
        signal: opportunitySignal({
          ...item,
          probability
        }),
        probability,
        progressPercent: probability === null ? 0 : probability,
        probabilityText: probability === null ? "已关闭" : `${probability}%`,
        amountLabel: amountText(item.amount),
        closeLabel: quarter ? `${quarter.year} Q${quarter.quarter}` : dateText(item.expected_close_date),
        stageCode: stage.code,
        stageName: stage.label,
        stageLabel: stage.text,
        amountBandCode: grade ? grade.code : "",
        gradeCode: grade ? grade.code : "",
        gradeText: grade ? grade.label : "未分级"
      };
    });
    const opportunities = append ? this.data.opportunities.concat(added.filter(r => !this.data.opportunities.some(old => old.id === r.id))) : added;
    if (this.data.canViewTeam && !Array.isArray(response.team_options)) throw Error('团队目录暂不可用，请稍后重试');
    if (this.data.canViewTeam && !Array.isArray(response.owner_options)) throw Error('人员目录暂不可用，请确认后端版本并重试');
    if(this.data.canViewTeam)assertPersonDirectory(response.owner_options,response.team_options,response.defaults);
    const members=response.owner_options||[];
    const teamOptions = [{value:'all',label:'全部团队'},...(response.team_options||[]).map(team=>({value:team.id,label:team.name}))],
      ownerOptions = [{value:'all',label:this.data.role==='sales'?'全部负责人':'全部成员'},...members.map(row=>({value:row.id,label:row.name}))];
    const legacyMatches=this.pageRequest.owner?members.filter(row=>row.name===this.pageRequest.owner):[];
    const ownerId=this.pageRequest.ownerId || (legacyMatches.length===1?legacyMatches[0].id:'');
    if(ownerId&&!members.some(row=>row.id===ownerId))throw Error('所选人员已不在当前目录中，请重置筛选');

    const opportunityGroups = groupOpportunitiesByQuarter(opportunities);
    this.setData({
      opportunities,
      filtered: [].concat(...opportunityGroups.map(group => group.items)),
      opportunityGroups,
      total: response.summary.total,
      totalAmount: amountText(response.summary.open_amount),
      hasMore: response.has_more,
      nextOffset: response.next_offset,
      teamOptions,
      ownerOptions,ownerPickerMembers:members,ownerPickerTeams:response.team_options||[],ownerPickerDefaultTeamId:(response.defaults||{}).team_id||'',
      legacyOwnerName:ownerId?'':this.pageRequest.owner||'',
      teamIndex: optionIndex(teamOptions, this.pageRequest.teamId !== 'all' && this.pageRequest.teamId || (teamOptions.find(row=>row.label===this.pageRequest.team)||{}).value || 'all'),
      ownerIndex: optionIndex(ownerOptions, ownerId),
      moreError: ''
    });
  },
  async loadMore() {
    if (this.data.loading || this.data.loadingMore || !this.data.hasMore) return;
    const serial = this.loadSerial,
      identity = this.identity,
      current = () => !this.closed && serial === this.loadSerial && identity === access.identity(getApp().globalData.session);
    this.setData({
      loadingMore: true,
      moreError: ''
    });
    try {
      const response = await apiClient.listOpportunities({
        ...this.pageRequest,
        offset: this.data.nextOffset
      });
      if (current()) this.acceptPage(response, true);
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
  changeFilter(e) {
    const key = e.currentTarget.dataset.key;
    this.setData({ [`${key}Index`]: Number(e.detail.value) }, () => this.applyFilters());
  },
  openOwnerPicker() {
    if(!this.data.canViewTeam || this.data.loading || this.data.loadError)return;
    this.ownerPickerIdentity=access.identity(getApp().globalData.session);
    const id=(this.data.ownerOptions[this.data.ownerIndex]||{}).value;
    this.setData({ownerPickerOpen:true,ownerPickerSelected:id&&id!=='all'?[id]:[]});
  },
  closeOwnerPicker(){this.setData({ownerPickerOpen:false});},
  confirmOwnerPicker(e){
    if(!this.data.ownerPickerOpen || this.ownerPickerIdentity!==access.identity(getApp().globalData.session) || this.data.loading || this.data.loadError)return;
    const ids=e.detail.ids||[];
    if(ids.length>1)return;
    const member=ids.length?this.data.ownerPickerMembers.find(row=>row.id===ids[0]):null;
    if(ids.length&&!member)return;
    const team=(this.data.teamOptions[this.data.teamIndex]||{}).value;
    const teamIndex=member&&team!=='all'&&!(member.team_ids||[]).includes(team)?0:this.data.teamIndex;
    this.setData({ownerPickerOpen:false,ownerIndex:optionIndex(this.data.ownerOptions,ids[0]||'all'),teamIndex,legacyOwnerName:''});
    this.memberFilter='all';return this.applyFilters();
  },
  changeTeam(e) {this.setData({teamIndex:Number(e.detail.value),ownerIndex:0,legacyOwnerName:''},()=>this.applyFilters());},
  toggleStageFilter() { this.setData({ showStageFilter: !this.data.showStageFilter }); },
  toggleStage(e) {
    const value = e.currentTarget.dataset.value;
    const selectedStages = value === "all" ? [] : this.data.selectedStages.includes(value)
      ? this.data.selectedStages.filter((item) => item !== value)
      : [...this.data.selectedStages, value];
    this.setData({ selectedStages, stageLabel: selectedStages.length ? `已选${selectedStages.length}项` : "全部阶段", stageOptions: this.data.stageOptions.map((item) => ({ ...item, selected: selectedStages.includes(item.value) })) }, () => this.applyFilters());
  },
  resetFilters() {
    const ownerOptions = this.data.ownerOptions;
    this.memberFilter='all';this.setData({ opportunityPeriods: opportunityPeriods.initial(), teamIndex: 0, ownerOptions, ownerIndex: 0,legacyOwnerName:'', selectedStages: [], stageLabel: "全部阶段", showStageFilter: false, stageOptions: this.data.stageOptions.map((item) => ({ ...item, selected: false })), closeIndex: 0, gradeIndex: 0, activityWindowIndex: 4 }, () => this.applyFilters());
  },
  applyFilters() {
    const activity = this.data.activityWindowOptions[this.data.activityWindowIndex];
    this.setData({filterActive:opportunityPeriods.active(this.data.opportunityPeriods)||(this.data.canViewTeam&&this.data.teamIndex>0)||(this.data.canViewTeam&&this.data.ownerIndex>0)||this.data.selectedStages.length>0||this.data.closeIndex>0||this.data.gradeIndex>0||Boolean(activity && activity.value !== 'all')});
    return this.loadPage();
  },
  createOpportunity() { wx.navigateTo({url:"/pages/opportunity-create/index"}); },
  openCustomer(e) {
    const customerId = e.currentTarget.dataset.customerId;
    const opportunityId = e.currentTarget.dataset.opportunityId;
    if (!customerId || !opportunityId) return;
    wx.navigateTo({ url: `/pages/customer-assets/index?customer_id=${encodeURIComponent(customerId)}&opportunity_id=${encodeURIComponent(opportunityId)}&period=all&readonly=1` });
  },
});
