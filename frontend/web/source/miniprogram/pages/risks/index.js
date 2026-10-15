// RISK001: the server owns UUID scope, ordering, pagination and complete summary.
const apiClient = require('../../utils/apiClient');
const access = require('../../utils/access');
const {normalizeRisk} = require('../../utils/riskDisplay');
const {assertPersonDirectory, normalizeTeams, normalizeMembers, candidateTeam} = require('../../utils/personPicker');
const uuid = value => /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value || '');
const count = value => Number.isInteger(value) && value >= 0;

Page({
  data:{
    tabs:[{key:'open',label:'待解除'},{key:'resolved',label:'已解除'},{key:'all',label:'全部'}], activeTab:'open',
    risks:[], filteredRisks:[], summary:null, total:0, openCount:null, resolvedCount:null, scope:'',
    offset:0, pageSize:50, page:1, pageCount:1, hasMore:false, loading:false, loadError:'', opportunityOnly:false,
    scopeMode:'all', scopeOptions:[{value:'all',label:'授权范围'},{value:'self',label:'本人'},{value:'team',label:'团队'},{value:'person',label:'成员'}],
    selectedTeamId:'', selectedMemberId:'', teamOptions:[], memberOptions:[], defaultTeamId:'',
    memberPickerOpen:false, memberSelected:[], memberLabel:'选择成员', teamLabel:'请选择团队', directoryLoading:false, directoryError:'', teamIndex:0,
  },
  onLoad(options={}) {
    if (getApp().guardPage && !getApp().guardPage(this,'risks',options)) return;
    const memberId=options.member_id || '', teamId=options.team_id || '';
    this._legacyFilter=Boolean((options.member && options.member !== 'all') || (options.team && options.team !== 'all') ||
      (memberId && !uuid(memberId)) || (teamId && !uuid(teamId)));
    this.setData({opportunityOnly:options.opportunity === '1',
      scopeMode:uuid(memberId) ? 'person' : uuid(teamId) ? 'team' : 'all',
      selectedMemberId:uuid(memberId) ? memberId : '', selectedTeamId:uuid(teamId) ? teamId : ''});
    if (this.data.opportunityOnly) wx.setNavigationBarTitle({title:'商机风险'});
  },
  onShow() {
    this._hidden=false;
    if (getApp().guardPage && !getApp().guardPage(this,'risks')) return;
    if (!getApp().ensureLogin()) return;
    this.checkIdentity();
    return Promise.all([this.loadDirectory(),this.loadRisks()]);
  },
  checkIdentity() {
    const identity=access.identity(getApp().globalData.session);
    if (this._riskIdentity && this._riskIdentity !== identity) {
      this.setData({...access.readStateReset('risks'),memberSelected:[],memberLabel:'选择成员',teamLabel:'请选择团队',defaultTeamId:'',directoryError:''});
      this._legacyFilter=false;
    }
    this._riskIdentity=identity;
    return identity;
  },
  async loadDirectory() {
    const identity=this.checkIdentity(), serial=this._directorySerial=(this._directorySerial || 0)+1;
    const current=()=>!this._hidden && serial===this._directorySerial && identity===access.identity(getApp().globalData.session);
    this.setData({directoryLoading:true,directoryError:''});
    try {
      const result=await apiClient.getDirectoryMembers({permission:'risk.read'});
      if (!current()) return;
      assertPersonDirectory(result.items,result.teams,result.defaults);
      const teams=normalizeTeams(result.teams), members=normalizeMembers(result.items,teams);
      if (teams.some(row=>!uuid(row.id)) || members.some(row=>!uuid(row.id))) throw Error('风险目录缺少有效成员或团队标识');
      this.setData({teamOptions:teams,memberOptions:members,defaultTeamId:candidateTeam(teams,result.defaults.team_id),
        teamLabel:(teams.find(row=>row.id===this.data.selectedTeamId) || {}).name || '请选择团队',
        memberLabel:(members.find(row=>row.id===this.data.selectedMemberId) || {}).name || '选择成员',
        teamIndex:Math.max(0,teams.findIndex(row=>row.id===this.data.selectedTeamId))});
    } catch(error) {
      if(current())this.setData({teamOptions:[],memberOptions:[],directoryError:error.message || '目录加载失败'});
    } finally { if(current())this.setData({directoryLoading:false}); }
  },
  query() {
    if(this._legacyFilter)throw Error('此筛选链接已过期，请重新选择范围');
    const {scopeMode,selectedTeamId,selectedMemberId,activeTab,offset,pageSize,opportunityOnly}=this.data;
    if(scopeMode==='team' && !uuid(selectedTeamId))throw Error('请选择团队后查看风险');
    if(scopeMode==='person' && !uuid(selectedMemberId))throw Error('请选择成员后查看风险');
    return {scope:scopeMode,status:activeTab==='all' ? undefined : activeTab,
      team_id:scopeMode==='team' ? selectedTeamId : undefined, member_id:scopeMode==='person' ? selectedMemberId : undefined,
      opportunity_only:opportunityOnly,page_size:pageSize,offset};
  },
  async loadRisks() {
    const identity=this.checkIdentity(), serial=this._loadSerial=(this._loadSerial || 0)+1;
    const current=()=>!this._hidden && serial===this._loadSerial && identity===access.identity(getApp().globalData.session);
    this.setData({loading:true,loadError:'',risks:[],filteredRisks:[],summary:null,openCount:null,resolvedCount:null,total:0,hasMore:false,
      scope:access.scopeLabel(getApp().globalData.session,'risk.read')});
    try {
      const query=this.query(), response=await apiClient.listRisks(query);
      if(!current())return;
      const summary=response.summary;
      if(!Array.isArray(response.items) || !count(response.total) || !summary ||
        !['total','open_count','resolved_count','overdue_count'].every(key=>count(summary[key])) ||
        summary.open_count+summary.resolved_count!==summary.total || summary.overdue_count>summary.open_count ||
        response.offset!==query.offset || response.page_size!==query.page_size || typeof response.has_more!=='boolean' ||
        response.items.length>query.page_size || response.items.length>response.total ||
        response.has_more!==(query.offset+response.items.length<response.total) ||
        response.total!==(query.status==='open' ? summary.open_count : query.status==='resolved' ? summary.resolved_count : summary.total) ||
        (response.has_more ? response.next_offset!==query.offset+query.page_size : response.next_offset!==null))throw Error('风险统计或分页字段不完整，请更新配套后端后重试');
      if(query.offset>0 && query.offset>=response.total) {
        this.setData({offset:Math.max(0,Math.ceil(response.total/query.page_size)-1)*query.page_size});
        return this.loadRisks();
      }
      const risks=response.items.map(item=>normalizeRisk(item,getApp().globalData.session));
      this.setData({risks,filteredRisks:risks,summary,total:response.total,openCount:summary.open_count,resolvedCount:summary.resolved_count,
        scope:query.scope==='self' ? '本人' : query.scope==='person' ? `成员 · ${this.data.memberLabel}` : query.scope==='team' ? `团队 · ${this.data.teamLabel}` : access.scopeLabel(getApp().globalData.session,'risk.read'),
        hasMore:response.has_more,page:Math.floor(query.offset/query.page_size)+1,pageCount:Math.max(1,Math.ceil(response.total/query.page_size))});
    } catch(error) { if(current())this.setData({loadError:error.message || '风险加载失败'}); }
    finally { if(current())this.setData({loading:false}); }
  },
  onHide(){this._hidden=true;this._loadSerial=(this._loadSerial || 0)+1;this._directorySerial=(this._directorySerial || 0)+1;this.closeMemberPicker();},
  onUnload(){this.onHide();},
  onTabChange(e){return this.selectTab({currentTarget:{dataset:{key:e.detail.key}}});},
  selectTab(e){const key=e.currentTarget.dataset.key;if(!['all','open','resolved'].includes(key))return;this.setData({activeTab:key,offset:0});return this.loadRisks();},
  changeScope(e){
    const mode=e.detail.value;if(!this.data.scopeOptions.some(row=>row.value===mode))return;
    this._legacyFilter=false;
    const team=mode==='team' ? candidateTeam(this.data.teamOptions,this.data.selectedTeamId || this.data.defaultTeamId) : this.data.selectedTeamId;
    this.setData({scopeMode:mode,offset:0,selectedTeamId:team,teamLabel:(this.data.teamOptions.find(row=>row.id===team) || {}).name || '请选择团队',teamIndex:Math.max(0,this.data.teamOptions.findIndex(row=>row.id===team))});
    return this.loadRisks();
  },
  chooseScope(e){return this.changeScope({detail:{value:e.currentTarget.dataset.value}});},
  selectRiskTeam(e){const id=String(e.detail.value || ''),team=this.data.teamOptions.find(row=>row.id===id);if(!team)return;this._legacyFilter=false;this.setData({selectedTeamId:id,teamLabel:team.name,teamIndex:this.data.teamOptions.indexOf(team),scopeMode:'team',offset:0});return this.loadRisks();},
  chooseTeam(e){const team=this.data.teamOptions[Number(e.detail.value)];if(team){this.setData({teamIndex:Number(e.detail.value)});return this.selectRiskTeam({detail:{value:team.id}});}},
  selectRiskMember(e){
    const id=String(e.detail.value || ''), member=this.data.memberOptions.find(row=>row.id===id);if(!member)return;
    this._legacyFilter=false;this.setData({selectedMemberId:id,memberSelected:[id],memberLabel:member.name,scopeMode:'person',offset:0,memberPickerOpen:false});return this.loadRisks();
  },
  openMemberPicker(){this.setData({memberPickerOpen:true});},
  closeMemberPicker(){this.setData({memberPickerOpen:false});},
  confirmMember(e){return this.selectRiskMember({detail:{value:(e.detail.ids || [])[0]}});},
  changePage(e){const page=Number(e.detail.current);if(!Number.isInteger(page) || page<1 || page>this.data.pageCount || this.data.loading)return;this.setData({offset:(page-1)*this.data.pageSize});return this.loadRisks();},
  previousPage(){return this.changePage({detail:{current:this.data.page-1}});},
  nextPage(){if(this.data.hasMore)return this.changePage({detail:{current:this.data.page+1}});},
  openRisk(e){wx.navigateTo({url:`/pages/risk-detail/index?id=${encodeURIComponent(e.currentTarget.dataset.id)}`});},
  openOpportunity(e){
    const {customerId,opportunityId}=e.currentTarget.dataset;if(!customerId || !opportunityId)return;
    wx.setStorageSync('pendingOpenCustomerId',customerId);wx.setStorageSync('pendingOpenOpportunityId',opportunityId);wx.switchTab({url:'/pages/customers/index'});
  },
});
