const { scoreLight } = require('../../utils/statusLight');
const apiClient = require("../../utils/apiClient");
const profileMetrics = require("../../utils/profileMetrics");
const access = require("../../utils/access");
const {memberRanking} = require("../../utils/fdeEfficiency");
const profileScores = require("../../utils/profileScores");

Page({
  data: {
    fdeScopeModes:[{value:"team",label:"团队"},{value:"person",label:"个人"}],fdeScopeMembers:[],fdeScopeTeams:[],fdeTeamId:"",fdeTeamLabel:"",ownUserId:"",fdeProfileScope:"self", fdeMemberOptions:[{id:"",name:"本人"}],fdeMemberIndex:0,fdeMemberId:"",fdeMemberName:"",fdeMembersError:"",
    logoutBusy:false, logoutNavigating:false,viewerIdentity:"",
    role: "sales", roleName: "一线销售", scope: "仅本人", userName: "", account: "", team: "", initial: "",
    growthLoading: false, growthReady: false, growthStatusText: "正在准备今日复盘",
    maturityScoreInfo: {}, efficiencyScoreInfo: {}, profileScoreInfo: {},
    overallScore: "--", reviewDate: "", reviewSummary: "",
    dimensions: [], growthOptions: [], selectedGrowthCode: "overall", selectedGrowthName: "综合评分",
    history: [], visitCount: 0, aiAdvice: [],
    activeProfileTab: "maturity",
    scopePickerOpen: false,
    profileScopeOptions: [], profileScopeIndex: 0, profileScopeMode: "self", profileScopeLabel: "本人",
    profileTeamOptions: [], profileTeamIndex: 0,
    profileMemberOptions: [], profileMemberIndex: 0,
    profileSelectedMemberName: "", profileSelectedMemberAccount: "", profileSelectedMemberId:"", profileTeamId:"", scopeMembers:[], scopeTeams:[],
    targetYear:0,targetQuarter:0,targetQuarterLabel:"",targetAnchor:"",targetQueryScope:"self",targetQueryMemberId:"",targetContextKey:"",directoryLoading:false,directoryError:"",
    structurePeriod:"current",followupPeriod:"week",efficiencyRatio:{},efficiencyWindowLabel:"",profileNotApplicable:false,
    profileTabs: [
      { key: "maturity", label: "营销成熟度" },
      { key: "efficiency", label: "营销效率" },
      { key: "profile", label: "销售画像" },
    ],
    maturityScore: "--", maturityLevel: "分析中", maturityDimensions: [],
    canEditSalesTarget: false, performanceLoading: false, performanceError: "",
    targetScopeLabel: "个人销售目标",
    performanceBoard: [profileMetrics.performanceMetric({}, "collection", null), profileMetrics.performanceMetric({}, "recognized", null)],
    efficiencyScore: "--", efficiencyDimensions: [], weeklyVisits: "0.0",
    organizationLoading: false, organizationReady: false,
    maturityScopeLabel: "个人评价", maturityOverall: {}, maturityTeams: [], maturityMembers: [],
    expandedMaturityMemberId: "",
    efficiencyMetric: "customers", efficiencyPeriod: "current", efficiencyRanking: [],
    efficiencyTotal: 0, efficiencyLeader: "--", efficiencyUnit: "个", efficiencyAggregateLabel: "本人统计",
    efficiencySecondaryValue: "--", efficiencySecondaryLabel: "客户评分平均分",
    efficiencySupplementals: { followup: null, customers: null, opportunities: null },
    efficiencyMetrics: [
      { key: "customers", label: "客户" },
      { key: "opportunities", label: "商机" },
      { key: "followup", label: "跟进记录" },
    ],
    efficiencyPeriods: [
      { key: "current", label: "当前总量" },
      { key: "year", label: "本年新增" },
    ],
  },
  identity(){return typeof getApp==='function'?access.identity(getApp().globalData.session):this.data.account;},
  onShow(){
    if(getApp().guardPage&&!getApp().guardPage(this,'profile'))return;
    if(!getApp().ensureLogin())return;
    this.visible=true;
    const role=this.sync(),session=getApp().globalData.session;
    const options=access.presentationOptions(session,'profile');
    const selected=options.find(row=>row.value===this.data.presentation)||options.find(row=>row.value===(access.isFde(role)?'fde':'sales'))||options[0];
    this.setData({presentationOptions:options,presentation:selected&&selected.value,canViewFdeTeam:access.canViewTeam(session,'profile.fde_read')});
    if(selected&&selected.value==='fde'){
      this.setData({isFde:true,isFdeLead:role==='fde_lead',efficiencyPeriods:[{key:'week',label:'本周'},{key:'month',label:'本月'},{key:'quarter',label:'本季度'},{key:'year',label:'本年'}],efficiencyPeriod:'week',efficiencyMetric:'opportunities',efficiencyMetrics:[{key:'opportunities',label:'商机'},{key:'followup',label:'跟进记录'},{key:'demo',label:'Demo 场景'}],profileTabs:[{key:'maturity',label:'项目成效'},{key:'efficiency',label:'协作效率'},{key:'profile',label:'FDE 画像'}]});
      this.loadFdeEfficiency();if(this.data.canViewFdeTeam)this.loadFdeMembers();return;
    }
    this.setData({isFde:false,efficiencyMetrics:[{key:'customers',label:'客户'},{key:'opportunities',label:'商机'},{key:'followup',label:'跟进记录'}],profileTabs:[{key:'maturity',label:'营销成熟度'},{key:'efficiency',label:'营销效率'},{key:'profile',label:'销售画像'}]});
    this.loadProfileDirectory();
  },
  changePresentation(e){const choice=e.currentTarget.dataset.kind;if(!this.data.presentationOptions.some(row=>row.value===choice))return;this.clearSubjectData();this.setData({presentation:choice});return this.onShow();},
  pause(){this.visible=false;if(this.growthTimer)clearTimeout(this.growthTimer);['growthRequestId','performanceRequestId','directoryRequestId','fdeEfficiencySerial'].forEach(key=>{this[key]=(this[key]||0)+1;});},
  onHide(){this.pause();},onUnload(){this.pause();},
  clearSubjectData(message='正在确认当前查看范围'){
    if(this.growthTimer)clearTimeout(this.growthTimer);this.growthTimer=null;
    ['growthRequestId','performanceRequestId','fdeEfficiencySerial'].forEach(key=>{this[key]=(this[key]||0)+1;});
    this.profilePerformance=null;this.performanceActuals={};this.marketingAnalytics=null;
    this.setData({growthLoading:false,growthReady:false,growthStatusText:message,profileNotApplicable:false,
      overallScore:'--',profileScoreInfo:{},dimensions:[],history:[],growthOptions:[],selectedGrowthCode:'overall',selectedGrowthName:'综合评分',reviewDate:'',reviewSummary:'',visitCount:0,aiAdvice:[],
      organizationLoading:false,organizationReady:false,organizationError:'',performanceLoading:false,performanceError:'',canEditSalesTarget:false,performanceBoard:this.buildPerformanceBoard(),maturityFacts:[],maturityOverall:{},maturityTeams:[],maturityMembers:[],expandedMaturityMemberId:'',
      maturityScore:'--',maturityScoreInfo:{},maturityDimensions:[],efficiencyScore:'--',efficiencyScoreInfo:{},efficiencyRatio:{},efficiencyRanking:[],efficiencyDimensions:[],efficiencyTotal:'—',efficiencySecondaryValue:'待加载',efficiencyWindowLabel:'',efficiencySupplementals:{followup:null,customers:null,opportunities:null}});
  },
  sync(){
    const session=getApp().globalData.session,identity=this.identity(),changed=this.profileIdentity!==identity;
    if(changed){this.clearSubjectData();this.directoryRequestId=(this.directoryRequestId||0)+1;this.fdeDirectorySerial=(this.fdeDirectorySerial||0)+1;this.profilePeople=[];}
    this.profileIdentity=identity;this.profileWorkspaceId=session.workspaceId||'default';
    const now=new Date(Date.now()+28800000),targetYear=now.getUTCFullYear(),targetQuarter=Math.floor(now.getUTCMonth()/3)+1;
    const profileScopeOptions=session.role==='supervisor'?[{value:'team',label:'团队'},{value:'person',label:'个人'}]:session.role==='manager'?[{value:'department',label:'部门'},{value:'team',label:'团队'},{value:'person',label:'个人'}]:[];
    this.setData({viewerIdentity:identity,ownUserId:session.userId,role:session.role,roleName:session.roleName,scope:session.scope,userName:session.userName,account:session.account,team:session.team,initial:String(session.userName||'').substring(0,1),targetYear,targetQuarter,targetQuarterLabel:targetYear+' Q'+targetQuarter,targetAnchor:targetYear+'-'+String((targetQuarter-1)*3+1).padStart(2,'0')+'-01',profileScopeOptions,
      ...(changed?{profileScopeMode:profileScopeOptions.length?profileScopeOptions[0].value:'self',profileScopeLabel:profileScopeOptions.length?profileScopeOptions[0].label:'本人',profileSelectedMemberId:session.userId||'',profileTeamId:'',fdeMemberId:'',fdeMemberName:'',fdeTeamId:'',fdeTeamLabel:'',fdeScopeTeams:[],fdeScopeMembers:[],fdeMemberOptions:[],fdeMemberIndex:0,fdeMembersError:'',fdeProfileScope:'self',scopeMembers:[],scopeTeams:[],profileMemberOptions:[],profileSelectedMemberName:'',profileSelectedMemberAccount:'',profileTeamOptions:[],profileTeamIndex:0,targetQueryScope:'self',targetQueryMemberId:'',targetContextKey:identity,structurePeriod:'current',followupPeriod:'week',efficiencyMetric:'customers',efficiencyPeriod:'current',efficiencyPeriods:[{key:'current',label:'当前总量'},{key:'year',label:'本年新增'}],organizationReady:false,canEditSalesTarget:false}:{} )});
    return session.role;
  },
  async loadProfileDirectory(){
    const identity=this.identity(),serial=this.directoryRequestId=(this.directoryRequestId||0)+1;
    this.clearSubjectData();
    this.setData({directoryLoading:true,directoryError:'',canEditSalesTarget:false});
    try{
      const result=await apiClient.getProfileScopeOptions();
      if(serial!==this.directoryRequestId||identity!==this.identity()||this.visible===false)return;
      if(result.data_source!=='database'||!Array.isArray(result.members)||!Array.isArray(result.teams))throw Error('查看范围加载失败');
      const session=getApp().globalData.session;
      const teams=result.teams.map(row=>({id:row.id,name:row.name}));
      const members=result.members.map(row=>({...row,id:row.user_id||row.id,name:(row.display_name||row.name)+(String(row.user_id||row.id)===String(session.userId)?'（本人）':''),initial:String(row.display_name||row.name||'人').slice(0,1),teamLabel:teams.filter(team=>(row.team_ids||[]).includes(team.id)).map(team=>team.name).join(' · ')}));
      const memberId=members.some(row=>row.id===this.data.profileSelectedMemberId)?this.data.profileSelectedMemberId:(result.defaults||{}).member_id||session.userId;
      const defaultTeamId=(result.defaults||{}).team_id;
      const teamId=teams.some(row=>row.id===this.data.profileTeamId)?this.data.profileTeamId:teams.some(row=>row.id===defaultTeamId)?defaultTeamId:(teams[0]||{}).id||'';
      const mode=(result.allowed_scopes||[]).includes(this.data.profileScopeMode)?this.data.profileScopeMode:(result.defaults||{}).scope||'self';
      this.profilePeople=members;
      this.setData({profileScopeOptions:session.permissions?[{value:'department',label:'部门'},{value:'team',label:'团队'},{value:'person',label:'个人'}].filter(row=>(result.allowed_scopes||[]).includes(row.value)):this.data.profileScopeOptions,directoryLoading:false,scopeMembers:members,scopeTeams:teams,profileSelectedMemberId:memberId,profileTeamId:teamId,profileScopeMode:mode});
      this.refreshProfileScope();
    }catch(error){if(serial===this.directoryRequestId&&identity===this.identity()&&this.visible!==false){this.clearSubjectData('查看范围加载失败，重试后再查看画像');this.profilePeople=[];this.setData({directoryLoading:false,directoryError:error.message||'查看范围加载失败，请重试',scopeMembers:[],scopeTeams:[],organizationReady:false});}}
  },
  selectProfileScope(e){const mode=e.detail?e.detail.mode:(this.data.profileScopeOptions[Number(e.currentTarget.dataset.index)]||{}).value;if(!this.data.profileScopeOptions.some(row=>row.value===mode))return;this.setData({profileScopeMode:mode});this.refreshProfileScope();},
  changeProfileSubject(e){const {kind,id}=e.detail,rows=kind==='team'?this.data.scopeTeams:this.data.scopeMembers;if(!rows.some(row=>row.id===id))return;this.setData(kind==='team'?{profileTeamId:id}:{profileSelectedMemberId:id});this.refreshProfileScope();},
  refreshProfileScope(){
    if(this.growthTimer)clearTimeout(this.growthTimer);
    const context=this.targetContext(),member=this.data.scopeMembers.find(row=>row.id===this.data.profileSelectedMemberId),team=this.data.scopeTeams.find(row=>row.id===this.data.profileTeamId);
    const label=context.query.scope==='self'?this.data.userName+'（本人）':context.query.scope==='person'?(member||{}).name||'请选择成员':context.query.scope==='team'?(team||{}).name||'请选择团队':'部门汇总';
    this.setData({profileScopeLabel:label,profileSelectedMemberName:(member||{}).name||'',profileSelectedMemberAccount:(member||{}).account_code||'',targetQueryScope:context.query.scope,targetQueryMemberId:context.query.member_id||'',targetContextKey:context.key,profileNotApplicable:this.data.role==='manager'&&(context.query.scope==='self'||(member&&member.role==='manager'&&context.query.scope==='person')),growthReady:false,history:[],dimensions:[],aiAdvice:[],overallScore:'--'});
    this.updateTargetContext();
    if(context.query.scope==='self')this.loadSalesGrowth();else this.loadScopedGrowth();
  },
  loadMarketingAnalytics(){return this.loadProfilePerformance();},
  // BACKEND-CONTRACT GROWTH: 进入我的页会POST复盘请求，再轮询GET /profile/sales-growth。
  // 必须后端幂等生成当日快照；页面触发不代表已完成AI分析。
  loadSalesGrowth(){
    const identity=this.identity(),serial=this.growthRequestId=(this.growthRequestId||0)+1;
    this.setData({ overallScore:"--", profileScoreInfo:{}, growthReady:false, growthLoading: true, growthStatusText: "正在整理近期拜访复盘" });
    const session=getApp().globalData.session;
    if(session.permissions&&!access.can(session,'profile.sales_review'))return this.pollGrowth(60,serial,identity);
    apiClient.ensureSalesGrowthReview()
      .then(() => {if(identity===this.identity()&&serial===this.growthRequestId&&this.visible!==false)this.pollGrowth(0,serial,identity);})
      .catch((error) => {
        if(identity!==this.identity()||serial!==this.growthRequestId)return;
        this.setData({ growthLoading: false, growthStatusText: error.message || "能力复盘加载失败" });
      });
  },
  pollGrowth(attempt,serial=this.growthRequestId,identity=this.identity()){
    apiClient.getSalesGrowth(30).then((payload) => {
      if(identity!==this.identity()||serial!==this.growthRequestId||this.visible===false)return;
      if (payload.today_status === "succeeded" && payload.latest) {
        this.applyGrowth(payload);
        return;
      }
      if (payload.today_status === "failed" || attempt >= 60) {
        this.setData({ growthLoading: false, growthStatusText: "今日复盘暂未完成，请稍后重试" });
        return;
      }
      this.growthTimer = setTimeout(() => this.pollGrowth(attempt + 1,serial,identity), 1000);
    }).catch((error) => {
      if(identity===this.identity()&&serial===this.growthRequestId)this.setData({ growthLoading: false, growthStatusText: error.message || "能力数据加载失败" });
    });
  },
  loadScopedGrowth(){
    if(this.data.profileNotApplicable){this.setData({growthLoading:false,growthReady:false,growthStatusText:'该岗位暂不适用销售六维画像'});return;}
    const context = this.targetContext();
    const requestId = this.growthRequestId = (this.growthRequestId || 0) + 1;
    this.setData({overallScore:'--', profileScoreInfo:{}, growthReady:false, growthLoading:false});
    if (context.query.scope === 'person' && (!context.query.member_id || context.query.member_id === 'all')) {
      this.setData({growthStatusText:'请选择要查看的人员'}); return;
    }
    this.setData({growthLoading:true, growthStatusText:'正在加载所选范围的销售画像'});
    return apiClient.getScopedSalesGrowth({...context.query,days:30}).then(payload => {
      if (requestId !== this.growthRequestId || context.key !== this.targetContext().key) return;
      if (!payload || payload.data_source !== 'database') throw new Error('销售画像数据格式异常');
      if (payload.applicable===false) {this.setData({profileNotApplicable:true,growthLoading:false,growthReady:false,growthStatusText:payload.reason||'该岗位暂不适用销售六维画像'});return;}
      if (!payload.latest) {
        this.setData({growthLoading:false,growthReady:false,growthStatusText:'所选范围暂未形成销售画像'}); return;
      }
      this.applyGrowth(payload, false);
    }).catch(error => {
      if (requestId === this.growthRequestId && context.key===this.targetContext().key && this.visible!==false) this.setData({overallScore:'--',profileScoreInfo:{},growthLoading:false,growthReady:false,growthStatusText:error.message || '销售画像加载失败，请稍后重试'});
    });
  },
  applyGrowth(payload, reloadAnalytics = true){
    const frameworkDimensions = (payload.framework && payload.framework.dimensions) || [];
    const scores = payload.latest.dimension_scores || {};
    const profileScoreInfo = profileScores.displayScore(payload.latest.score_summary);
    const dimensions = frameworkDimensions.map((definition) => {
      const value = scores[definition.code] || {};
      return {
        signal:scoreLight(value.score),
        code: definition.code,
        name: definition.name,
        shortName: definition.short_name || definition.name,
        score: profileScores.numeric(value.score) === null ? "--" : Math.round(Math.min(100, Math.max(0, Number(value.score)))),
        assessment: value.assessment || "暂无判断",
        coachingAction: value.coaching_action || "继续积累拜访证据",
      };
    });
    const growthOptions = [{ code: "overall", name: "综合" }].concat(dimensions.map((item) => ({ code: item.code, name: item.shortName })));
    const improvements = Array.isArray(payload.latest.improvements) ? payload.latest.improvements.filter(Boolean) : [];
    const aiAdvice = improvements.length ? improvements.slice(0, 6) : dimensions.map((item) => `${item.name}：${item.coachingAction}`).filter(Boolean).slice(0, 6);
    const visitCount = Number((payload.latest.input_snapshot || {}).visit_count || 0);
    this.setData({
      growthLoading: false,
      growthReady: true,
      growthStatusText: payload.projection === "current_group" ? `有效复盘 ${payload.coverage.reviewed_members} / ${payload.coverage.eligible_members} 人 · 当前汇总` : "能力复盘 · 数据已同步",
      overallScore: profileScoreInfo.text,
      profileScoreInfo,
      reviewDate: String(payload.latest.review_date || ""),
      reviewSummary: payload.latest.summary || "已完成今日六维能力复盘",
      dimensions,
      growthOptions,
      history: payload.history || [],
      visitCount,
      aiAdvice,
    }, () => {
      this.drawGrowthCharts();
      if (reloadAnalytics) this.loadMarketingAnalytics();
    });
  },
  showScoreRule(e){
    const kind = e.currentTarget.dataset.kind;
    const config = {maturity:[this.data.isFde?'项目成效总分':'营销成熟度总分',this.data.maturityScoreInfo],efficiency:[this.data.isFde?'协作效率总分':'营销效率总分',this.data.efficiencyScoreInfo],profile:['销售画像总分',this.data.profileScoreInfo]}[kind];
    if (!config) return;
    const info=config[1] || {};
    wx.showModal({title:config[0], showCancel:false, confirmText:'知道了',
      content:`${info.explanation || '暂无可计算数据'}\n\n状态：80分及以上为绿色健康，60–79分为黄色提醒，低于60分为红色告警，缺失评分为灰色待评估。\n\n总分为有效指标的加权平均，满分100分，超额达成按100分计。缺失项剔除后按剩余权重折算；全部缺失时显示“--”。${info.count ? '\n已纳入 ' + info.count + '/' + info.total + ' 项指标，覆盖权重 ' + info.coverage_percent + '%。' : ''}${info.rule ? '\n公司评分规则版本：' + info.rule.version : ''}`});
  },
  selectProfileTab(e){
    const activeProfileTab = e.currentTarget.dataset.tab;
    this.setData({ activeProfileTab }, () => {
      if (activeProfileTab === "profile" && this.data.growthReady) wx.nextTick(() => this.drawGrowthCharts());
    });
  },
  selectGrowthDimension(e){
    const code = e.currentTarget.dataset.code;
    const option = this.data.growthOptions.find((item) => item.code === code);
    this.setData({ selectedGrowthCode: code, selectedGrowthName: option ? option.name : "综合评分" }, () => this.drawGrowthLine());
  },
  toggleMaturityMember(e){
    const userId = e.currentTarget.dataset.id;
    const expandedMaturityMemberId = this.data.expandedMaturityMemberId === userId ? "" : userId;
    this.setData({
      expandedMaturityMemberId,
      maturityMembers: this.data.maturityMembers.map((item) => ({
        ...item,
        expanded: item.user_id === expandedMaturityMemberId,
      })),
    });
  },
  selectEfficiencyMetric(e){
    const metric=e.currentTarget.dataset.metric;if(!this.data.efficiencyMetrics.some(row=>row.key===metric))return;
    const periods=this.data.isFde?[{key:'week',label:'本周'},{key:'month',label:'本月'},{key:'quarter',label:'本季度'},{key:'year',label:'本年'}]:metric==='followup'?[{key:'week',label:'本周'},{key:'quarter',label:'本季度'},{key:'year',label:'本年'}]:[{key:'current',label:'当前总量'},{key:'year',label:'本年新增'}];
    const period=this.data.isFde?this.data.efficiencyPeriod:metric==='followup'?this.data.followupPeriod:this.data.structurePeriod;
    this.setData({efficiencyMetric:metric,efficiencyPeriod:period,efficiencyPeriods:periods});
    if(this.data.isFde)this.loadFdeEfficiency();else this.applyEfficiencySupplemental();
  },
  selectEfficiencyPeriod(e){const period=e.currentTarget.dataset.period;if(!this.data.efficiencyPeriods.some(row=>row.key===period))return;this.setData({efficiencyPeriod:period,...(!this.data.isFde?{[this.data.efficiencyMetric==='followup'?'followupPeriod':'structurePeriod']:period}:{})});return this.data.isFde?this.loadFdeEfficiency():this.loadProfilePerformance();},
  async loadFdeMembers(){
    const session=getApp().globalData.session,identity=this.identity(),serial=this.fdeDirectorySerial=(this.fdeDirectorySerial||0)+1;
    if(!access.canViewTeam(session,'profile.fde_read'))return;
    try{const result=await apiClient.getFdeScopeOptions();if(identity!==this.identity()||serial!==this.fdeDirectorySerial||this.visible===false)return;
      if(result.data_source!=='database'||!Array.isArray(result.members)||!Array.isArray(result.teams))throw Error('成员范围加载失败');
      const teams=result.teams,members=result.members.map(row=>{const id=row.user_id||row.id,name=row.display_name||row.name;return {...row,id,name:name+(id===session.userId?'（本人）':''),initial:String(name||'人').slice(0,1),teamLabel:teams.filter(team=>(row.team_ids||[]).includes(team.id)).map(team=>team.name).join(' · ')};});
      const options=members.map(row=>({...row,id:row.id===session.userId?'':row.id})),index=Math.max(0,options.findIndex(row=>row.id===this.data.fdeMemberId)),member=options[index]||{id:'',name:this.data.userName};
      const team=teams.find(row=>row.id===this.data.fdeTeamId)||teams.find(row=>row.id===(result.defaults||{}).team_id)||teams[0]||{};
      this.setData({fdeScopeMembers:members,fdeScopeTeams:teams,fdeTeamId:team.id||'',fdeTeamLabel:team.name||'',fdeMemberOptions:options,fdeMemberIndex:index,fdeMemberId:member.id,fdeMemberName:member.id?member.name:'',fdeMembersError:''});
      if(this.data.fdeProfileScope==='team')this.loadFdeEfficiency();
    }catch(error){if(identity===this.identity()&&serial===this.fdeDirectorySerial)this.setData({fdeMembersError:error.message||'成员列表加载失败，点击重试'});}
  },
  changeFdeScopeMode(e){return this.changeFdeProfileScope({currentTarget:{dataset:{scope:e.detail.mode==='team'?'team':'self'}}});},
  changeFdeScopeSubject(e){if(e.detail.kind==='team'){const team=this.data.fdeScopeTeams.find(row=>row.id===e.detail.id);if(team){this.setData({fdeTeamId:team.id,fdeTeamLabel:team.name});this.loadFdeEfficiency();}return;}const id=e.detail.id,own=getApp().globalData.session.userId,index=this.data.fdeMemberOptions.findIndex(row=>(row.id||own)===id);if(index>=0)this.changeFdeMember({detail:{value:index}});},
  changeFdeMember(e) {
    if(!access.canViewTeam(getApp().globalData.session,'profile.fde_read'))return;
    const index=Number(e.detail.value),row=this.data.fdeMemberOptions[index];if(!row)return;
    this.setData({fdeMemberIndex:index,fdeMemberId:row.id,fdeMemberName:row.id?row.name:''});
    this.loadFdeEfficiency();
  },
  changeFdeProfileScope(e) {
    const scope=e.currentTarget.dataset.scope;
    if(!['self','team'].includes(scope)||!access.canViewTeam(getApp().globalData.session,'profile.fde_read'))return;
    if(scope==='team'&&!access.canViewTeam(getApp().globalData.session,'profile.fde_read')){wx.showToast({title:'当前账号未开放团队权限',icon:'none'});return;}
    if(scope===this.data.fdeProfileScope)return;
    if(scope==='team'&&!this.data.fdeTeamId){wx.showToast({title:'请等待团队范围加载完成',icon:'none'});return;}
    this.setData({fdeProfileScope:scope});
    this.loadFdeEfficiency();
  },
  async loadFdeEfficiency() {
    const serial=this.fdeEfficiencySerial=(this.fdeEfficiencySerial||0)+1;
    const identity=this.identity(), account=this.data.account, metric=this.data.efficiencyMetric, period=this.data.efficiencyPeriod;
    this.setData({organizationLoading:true,organizationReady:false,organizationError:'',efficiencyTotal:'—',efficiencyRanking:[],efficiencyScore:'—'});

    try {
      const date=new Date(Date.now()+28800000), year=date.getUTCFullYear();
      const quarters=period==='quarter'?[Math.floor(date.getUTCMonth()/3)+1]:[];
      const result=await apiClient.getFdeDashboard({scope:this.data.fdeProfileScope==='team'||this.data.fdeMemberId?'team':'self',member_id:this.data.fdeProfileScope==='self'?this.data.fdeMemberId||undefined:undefined,year,quarters,period,team_id:this.data.fdeProfileScope==='team'?this.data.fdeTeamId||undefined:undefined});
      if(serial!==this.fdeEfficiencySerial || !this.data.isFde || account!==this.data.account || identity!==this.identity() || this.visible===false)return;
      if(result.data_source!=='database'||!result.summary)throw Error('协作效率数据暂未加载完成');
      const summary=result.summary;
      const value=summary[{followup:'period_visits',opportunities:'period_opportunities',demo:'own_demo_scene_count'}[metric]];
      const session=getApp().globalData.session,canRank=!session.permissions||access.can(session,'dashboard.ranking');
      const company=canRank?result.company_rankings:{data_source:'database',items:[]};
      if(!company||company.data_source!=='database'||!Array.isArray(company.items))throw Error('同级排名暂未加载完成');
      const teamRanking=company.scope==='company_fde_teams';
      const selectedId=teamRanking?this.data.fdeTeamId:this.data.fdeMemberId||(getApp().globalData.session||{}).userId;
      const ranking=memberRanking(company.items,metric,period,selectedId,teamRanking?'team':'person');
      this.setData({organizationLoading:false,organizationReady:true,efficiencyTotal:value==null?'—':value,
        efficiencyUnit:metric==='followup'?'次':'个',efficiencyAggregateLabel:this.data.fdeProfileScope==='team'?this.data.fdeTeamLabel||'团队汇总':this.data.fdeMemberName||'本人统计',efficiencyRanking:ranking,
        fdeEfficiencyNote:!canRank?'当前查看范围的工作统计。':teamRanking?'公司同类团队按所选周期排名，同值并列。商机按期间跟进项目去重，同一商机在团队内仅计一次；Demo 按团队成员登记记录统计。':'同级成员按所选周期排名，同值并列。商机按期间跟进项目去重，Demo 按本人登记记录统计。'});

    } catch(error) {if(serial===this.fdeEfficiencySerial && this.data.isFde && account===this.data.account && identity===this.identity() && this.visible!==false)this.setData({organizationLoading:false,organizationReady:false,organizationError:error.message||'协作效率加载失败'});}
  },
  retryEfficiency() {return this.data.isFde?this.loadFdeEfficiency():this.loadMarketingAnalytics();},
  applyEfficiencyRanking(){return this.applyEfficiencySupplemental();},
  buildPerformanceBoard(){const targets=(this.profilePerformance||{}).targets||{},actuals=this.performanceActuals||{};return ['collection','recognized'].map(kind=>({...profileMetrics.performanceMetric({targets},kind,actuals[kind]),targetLabel:this.data.targetScopeLabel}));},
  targetContext(){
    const role=this.data.role,mode=this.data.profileScopeMode;
    const query=role==='sales'&&!getApp().globalData.session.permissions?{scope:'self'}:mode==='department'?{scope:'department'}:mode==='team'?{scope:'team',team_id:this.data.profileTeamId||undefined}:mode==='person'?{scope:'person',member_id:this.data.profileSelectedMemberId||undefined}:{scope:'self'};
    return {query,key:JSON.stringify([this.identity(),query,this.data.targetYear,this.data.targetQuarter]),label:query.scope==='department'?'部门目标':query.scope==='team'?'团队目标':'个人目标'};
  },
  readCurrentTargets(){return (this.profilePerformance||{}).targets||{};},
  updateTargetContext(){this.profilePerformance=null;this.performanceActuals={};this.setData({canEditSalesTarget:false,maturityFacts:[],performanceBoard:this.buildPerformanceBoard(),efficiencyRatio:{},efficiencyRanking:[],organizationReady:false});return this.loadProfilePerformance();},
  async loadProfilePerformance(){
    if(this.data.isFde||!apiClient.getProfilePerformance)return;
    const context=this.targetContext(),requestId=this.performanceRequestId=(this.performanceRequestId||0)+1;
    if((context.query.scope==='person'&&!context.query.member_id)||(context.query.scope==='team'&&!context.query.team_id)){this.setData({performanceLoading:false,performanceError:'请选择查看对象'});return;}
    const current=()=>this.visible!==false&&requestId===this.performanceRequestId&&context.key===this.targetContext().key;
    this.setData({performanceLoading:true,performanceError:'',canEditSalesTarget:false,maturityScore:'--',efficiencyScore:'--',maturityScoreInfo:{},efficiencyScoreInfo:{},efficiencyRatio:{},efficiencyRanking:[]});
    try{
      const result=await apiClient.getProfilePerformance({...context.query,year:this.data.targetYear||undefined,quarter:this.data.targetQuarter||undefined,structure_period:this.data.structurePeriod,period:this.data.followupPeriod});
      if(!current())return;
      if(result.data_source!=='database'||!result.actuals||!result.targets||!result.supplementals)throw Error('经营数据格式异常');
      this.profilePerformance=result;this.performanceActuals=result.actuals;
      const retention=result.retention||{},score=profileScores.displayScore((result.scores||{}).maturity),period=result.target_period||{};
      const self=context.query.scope==='self'||(context.query.scope==='person'&&context.query.member_id===getApp().globalData.session.userId);
      this.setData({organizationReady:true,organizationLoading:false,maturityScopeLabel:'经营事实',performanceLoading:false,performanceError:'',canEditSalesTarget:result.editable===true&&(getApp().globalData.session.permissions?access.can(getApp().globalData.session,'target.submit'):self),maturityScore:score.text,maturityScoreInfo:score,targetScopeLabel:(period.year||this.data.targetYear)+' Q'+(period.quarter||this.data.targetQuarter)+' '+context.label,
        maturityFacts:[{key:'active',name:'在推商机 ACV',value:profileMetrics.money(result.active_opportunity_amount),detail:'当前进行中的商机金额'},{key:'won',name:'已赢单金额',value:profileMetrics.money(result.won_amount),detail:'已赢单商机累计金额，非确收'},{key:'retention',name:'客户保有率',value:retention.rate==null?'待计算':Number(retention.rate).toFixed(1)+'%',detail:retention.rate==null?'去年暂无已登记收入，暂不能计算':retention.customer_count+' 家去年有收入客户：今年 '+profileMetrics.money(retention.current)+' ÷ 去年 '+profileMetrics.money(retention.previous)}]});
      this.setData({performanceBoard:this.buildPerformanceBoard()});this.applyEfficiencySupplemental();
    }catch(error){if(!current())return;this.profilePerformance=null;this.performanceActuals={};this.setData({organizationReady:false,performanceLoading:false,performanceError:error.message||'经营数据加载失败',canEditSalesTarget:false,performanceBoard:this.buildPerformanceBoard(),maturityFacts:[],efficiencyRatio:{}});}
  },
  editPerformanceTarget(){const component=this.selectComponent&&this.selectComponent('#salesQuarterTarget');if(component)component.show();},
  targetsSaved(){return this.loadProfilePerformance();},
  applyEfficiencySupplemental(){
    const result=this.profilePerformance||{},values=result.supplementals||{},metric=this.data.efficiencyMetric,row=(result.efficiency||{})[metric]||{},score=profileScores.displayScore((result.scores||{}).efficiency);
    const followup=metric==='followup',rate=row.rate==null?null:Number(row.rate),count=row.denominator,window=(result.windows||{})[followup?'followup':'structure']||{};
    const ratio={label:metric==='customers'?'高潜客户占比':'高质量商机占比',value:row.status==='empty'?'暂无数据':rate==null?'待评估':rate.toFixed(1)+'%',progress:rate==null?0:Math.min(100,Math.max(0,rate)),detail:count==null?'数据暂未加载':(metric==='customers'?'第一、二象限 ':'A、B 级 ')+(row.numerator||0)+' / '+count+(metric==='customers'?' 家客户':' 个商机'),coverage:row.pending_count?('待评估 '+row.pending_count+(metric==='customers'?' 家客户':' 个商机')):'',status:row.status||'pending'};
    this.setData({efficiencyScore:score.text,efficiencyScoreInfo:score,efficiencySupplementals:values,efficiencyRatio:ratio,efficiencyTotal:row.count==null?'—':row.count,efficiencySecondaryValue:values[metric]==null?'待计算':Number(values[metric]).toFixed(1)+(followup?'分':'%'),efficiencySecondaryLabel:followup?'跟进记录平均分':ratio.label,efficiencyAggregateLabel:this.data.profileScopeLabel,efficiencyUnit:'次',efficiencyRanking:[],efficiencyWindowLabel:window.start?window.start+' — '+window.end:followup?'已确认及归档记录':this.data.structurePeriod==='year'?'本年创建 · 当前结构':'当前负责的客户与商机'});
  },
  profilePickerVisibility(e){
    const scopePickerOpen = Boolean(e.detail.open);
    this.setData({scopePickerOpen}, () => {
      if (!scopePickerOpen && !this.data.isFde) {
        const redraw = () => {
          if (!this.data.scopePickerOpen && this.visible !== false && this.data.activeProfileTab === 'profile' && this.data.growthReady) this.drawGrowthCharts();
        };
        if (wx.nextTick) wx.nextTick(redraw); else redraw();
      }
    });
  },
  drawGrowthCharts(){
    this.drawRadar();
    this.drawGrowthLine();
  },
  drawRadar(){
    if (this.data.scopePickerOpen) return;
    const dimensions = this.data.dimensions;
    if (dimensions.length !== 6) return;
    const query = this.createSelectorQuery();
    query.select(".radar-canvas").boundingClientRect((rect) => {
      if (!rect) return;
      if (this.data.scopePickerOpen || this.visible === false) return;
      const ctx = wx.createCanvasContext("abilityRadar", this);
      const width = rect.width, height = rect.height;
      const cx = width / 2, cy = height / 2 + 3, radius = Math.min(width, height) * 0.31;
      const point = (index, scale) => {
        const angle = -Math.PI / 2 + index * Math.PI / 3;
        return [cx + Math.cos(angle) * radius * scale, cy + Math.sin(angle) * radius * scale];
      };
      ctx.setLineWidth(1);
      for (let level = 1; level <= 5; level += 1) {
        ctx.beginPath();
        for (let i = 0; i < 6; i += 1) {
          const p = point(i, level / 5);
          if (i === 0) ctx.moveTo(p[0], p[1]); else ctx.lineTo(p[0], p[1]);
        }
        ctx.closePath(); ctx.setStrokeStyle("rgba(61,100,146,.18)"); ctx.stroke();
      }
      for (let i = 0; i < 6; i += 1) {
        const p = point(i, 1); ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(p[0], p[1]); ctx.setStrokeStyle("rgba(61,100,146,.13)"); ctx.stroke();
      }
      ctx.beginPath();
      dimensions.forEach((item, index) => {
        const p = point(index, (profileScores.numeric(item.score) || 0) / 100);
        if (index === 0) ctx.moveTo(p[0], p[1]); else ctx.lineTo(p[0], p[1]);
      });
      ctx.closePath(); ctx.setFillStyle("rgba(22,119,255,.22)"); ctx.fill(); ctx.setLineWidth(2); ctx.setStrokeStyle("#1677ff"); ctx.stroke();
      dimensions.forEach((item, index) => {
        const p = point(index, (profileScores.numeric(item.score) || 0) / 100); ctx.beginPath(); ctx.arc(p[0], p[1], 3, 0, Math.PI * 2); ctx.setFillStyle("#1677ff"); ctx.fill();
        const label = point(index, 1.28); ctx.setFillStyle("#53657a"); ctx.setFontSize(11); ctx.setTextAlign(label[0] < cx - 5 ? "right" : label[0] > cx + 5 ? "left" : "center"); ctx.fillText(`${item.shortName} ${item.score}`, label[0], label[1] + 4);
      });
      ctx.draw();
    }).exec();
  },
  drawGrowthLine(){
    if (this.data.scopePickerOpen) return;
    const history = this.data.history;
    if (!history.length) return;
    const code = this.data.selectedGrowthCode;
    const query = this.createSelectorQuery();
    query.select(".growth-canvas").boundingClientRect((rect) => {
      if (!rect) return;
      if (this.data.scopePickerOpen || this.visible === false) return;
      const ctx = wx.createCanvasContext("growthLine", this);
      const width = rect.width, height = rect.height, left = 34, right = 12, top = 18, bottom = 28;
      const valueOf = (item) => code === "overall" ? Number(item.overall_score || 0) : Number(((item.dimension_scores || {})[code] || {}).score || 0);
      [0, 25, 50, 75, 100].forEach((value) => {
        const y = top + (100 - value) / 100 * (height - top - bottom);
        ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(width - right, y); ctx.setStrokeStyle("rgba(61,100,146,.12)"); ctx.stroke();
        ctx.setFillStyle("#91a0b2"); ctx.setFontSize(9); ctx.setTextAlign("right"); ctx.fillText(String(value), left - 6, y + 3);
      });
      ctx.beginPath();
      history.forEach((item, index) => {
        const x = history.length === 1 ? (left + width - right) / 2 : left + index / (history.length - 1) * (width - left - right);
        const y = top + (100 - valueOf(item)) / 100 * (height - top - bottom);
        if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.setLineWidth(2.5); ctx.setStrokeStyle("#1677ff"); ctx.stroke();
      history.forEach((item, index) => {
        const x = history.length === 1 ? (left + width - right) / 2 : left + index / (history.length - 1) * (width - left - right);
        const y = top + (100 - valueOf(item)) / 100 * (height - top - bottom);
        ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.setFillStyle("#1677ff"); ctx.fill();
        if (index === 0 || index === history.length - 1) { ctx.setFillStyle("#728196"); ctx.setFontSize(9); ctx.setTextAlign(index === 0 ? "left" : "right"); ctx.fillText(String(item.review_date).slice(5), x, height - 7); }
      });
      ctx.draw();
    }).exec();
  },
  logout(){
    if (this.data.logoutBusy) return;
    this.setData({logoutBusy:true});
    // A previous navigation may have failed after the local session was cleared.
    if (!getApp().globalData.session) { this.finishLogout(); return; }
    wx.showModal({
      title:"退出当前账号？",
      content:"退出后需要重新选择身份并登录。",
      confirmText:"退出登录",
      confirmColor:"#d85d68",
      success:(res)=>{
        if(!res.confirm) { this.setData({logoutBusy:false}); return; }
        this.finishLogout();
      },
      fail:()=>{
        this.setData({logoutBusy:false,logoutNavigating:false});
        wx.showToast({title:'退出确认未打开，请重试',icon:'none'});
      }
    });
  },
  finishLogout(){
    this.setData({logoutNavigating:true});
    // Logout clears local identity immediately; server revocation must not block navigation.
    try { if (getApp().globalData.session) getApp().logout(); }
    catch (_) {
      this.setData({logoutBusy:false,logoutNavigating:false});
      wx.showToast({title:'退出未完成，请重试',icon:'none'});
      return;
    }
    // Wait for the confirmation dialog's current event to finish before replacing the page stack.
    wx.nextTick(()=>wx.reLaunch({
      url:"/pages/login/index",
      fail:()=>{
        this.setData({logoutBusy:false,logoutNavigating:false});
        wx.showToast({title:'已退出，请再次点击进入登录页',icon:'none'});
      }
    }));
  },
});
