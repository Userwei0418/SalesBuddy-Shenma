const api = require('../../utils/apiClient');
const {normalizeMembers, normalizeTeams, candidateTeam, assertPersonDirectory}=require('../../utils/personPicker');
const access = require('../../utils/access');
const present = require('../../utils/fdePresentation');
const finance = require('../../utils/financialDisplay');
const {companyRankings}=require('../../utils/fdeCompanyRankings');
const {visitDetailUrl} = require('../../utils/visitNavigation');

// This endpoint interprets empty quarters as the specified calendar year, never all history.
function periodQuarters(values = []) {
  const quarters=[...new Set(values.map(Number).filter(value=>[1,2,3,4].includes(value)))].sort();
  return quarters.length===4?[]:quarters;
}
function periodLabel(year, quarters) {
  const selected=periodQuarters(quarters);
  return year+'年 · '+(selected.length?selected.map(q=>'Q'+q).join('、'):'全年');
}
function draftPeriod(quarters) {
  const draftQuarters=periodQuarters(quarters);
  return {draftQuarters,draftFullYear:!draftQuarters.length,quarterOptions:[1,2,3,4].map(value=>({value,selected:draftQuarters.includes(value)}))};
}

Component({
  properties: {recordsOnly:Boolean, recordContext:Object, personal: Boolean, memberId: String, compact: Boolean},
  data: {
    scopeOptions:[{value:'team',label:'团队'},{value:'self',label:'个人'}], rhythmOptions:[{value:'day',label:'日进展'},{value:'week',label:'周进展'}], rankingPeriodOptions:[{value:'week',label:'本周'},{value:'month',label:'本月'},{value:'quarter',label:'本季度'}],
    loading: true, error: '', ready: false, scope: 'self', scopeLabel: '本人', memberLabel: '',
    rankingPeriod:'', rankingPeriodLabel:'', rankingPeriodLoading:false, rankingPeriodError:'',
    sortKey: 'visits', sortOptions: [{key: 'visits', name: '拜访记录'}, {key: 'completed_tasks', name: '完成任务'}, {key: 'opportunities', name: '参与商机'}, {key: 'overdue_tasks', name: '逾期任务'}], sortIndex: 0,
    memberOptions: [{id: '', name: '加载中'}], memberIndex: 0,
    memberPickerOpen:false,pickerMembers:[],pickerTeams:[],pickerDefaultTeamId:'',pickerLoading:false,pickerError:'',
    yearOptions: [], yearIndex: 0, year: 0, quarters: [], periodLabel: '',
    filterOpen: false, draftYearIndex: 0, draftQuarters: [], draftFullYear: true,
    quarterOptions: [1, 2, 3, 4].map(q => ({value: q, selected: false})),
    demoRankingReady:false,demoRanking:[],showAllDemos:false,companyRankingReady:false,companyRankingError:'',companyRankingTotal:0,followupRanking:[],recognizedRanking:[],showAllFollowups:false,showAllRecognized:false,
    companyCards:[],ownIds:[],pickerOptions:[],pickerSelected:[''],
    summary: null, ranking: [], recentVisits: [], stages: [], rhythm: [], rhythmSource:[], rhythmMode:'day', asOf: '',
    activityOpen: false, activity: [], activityLoading: false, activityError: '', activityMore: false, activityOffset: 0, activityTotal: 0,
  },
  lifetimes: {
    attached() {
      if(this.properties.recordsOnly) {
        const context=this.properties.recordContext||{};this.activityContext=context;
        this.setData({scope:context.scope||'self',year:context.year,quarters:context.quarters||[],periodLabel:context.periodLabel||'',scopeLabel:context.scopeLabel||'本人',memberLabel:context.memberLabel||'',activityOpen:true});
        this.loadActivity(); return;
      }
      const session = getApp().globalData.session || {}, year = new Date(Date.now() + 28800000).getUTCFullYear();
      this.setData({canViewTeam: access.canViewTeam(session, 'profile.fde_read'), scope: !this.properties.personal && session.role === 'fde_lead' ? 'team' : 'self', year, yearOptions: Array.from({length: 8}, (_, i) => year - 5 + i), yearIndex: 5, periodLabel: year + '年 · 全年'});
      this.load();
    },
    detached() { this.closeMemberPicker(); this.closed = true; this.serial = (this.serial || 0) + 1; this.activitySerial = (this.activitySerial || 0) + 1; },
  },
  pageLifetimes: {
    show() {
      const returning=this.closed;this.closed=false;
      if(this.properties.recordsOnly){
        if(returning){this.setData({activity:[],activityOffset:0,activityTotal:0,activityMore:false,activityLoading:false,activityError:''});return this.loadActivity();}
      } else if(this.data.year)return this.load();
    },
    hide(){
      this.closeMemberPicker();this.closed=true;
      this.serial=(this.serial||0)+1;this.activitySerial=(this.activitySerial||0)+1;this.rankingPeriodSerial=(this.rankingPeriodSerial||0)+1;
      this.setData({activityLoading:false,rankingPeriodLoading:false});
    },
  },
  methods: {
    onScopeSegment(e){return this.scope({currentTarget:{dataset:{scope:e.detail.value}}});},
    onRhythmSegment(e){return this.changeRhythmMode({currentTarget:{dataset:{mode:e.detail.value}}});},
    onRankingPeriodSegment(e){return this.changeRankingPeriod({currentTarget:{dataset:{period:e.detail.value}}});},
    params() {
      if(this.properties.recordsOnly&&this.activityContext)return this.activityContext;
      const memberId=this.properties.memberId || (this.data.scope==='self'?(this.data.memberOptions[this.data.memberIndex]||{}).id:'') || '';
      return {scope:memberId?'team':this.data.scope,member_id:memberId,year:this.data.year,quarters:periodQuarters(this.data.quarters)};
    },
    async load() {
      const session = getApp().globalData.session || {}, canViewTeam = access.canViewTeam(session, 'profile.fde_read');
      const identity = access.identity(session);
      if(this.dashboardIdentity!==undefined && this.dashboardIdentity!==identity){
        this.closeMemberPicker();
        const year=new Date(Date.now()+28800000).getUTCFullYear(),yearOptions=Array.from({length:8},(_,i)=>year-5+i);
        this.setData({year,yearOptions,yearIndex:5,quarters:[],periodLabel:periodLabel(year,[]),filterOpen:false,draftYearIndex:5,...draftPeriod([]),
          scope:!this.properties.personal&&session.role==='fde_lead'&&canViewTeam?'team':'self',scopeLabel:'本人',memberLabel:'',
          memberIndex:0,memberOptions:[{id:'',name:session.userName||session.account||'当前账号'}],pickerMembers:[],pickerTeams:[],pickerDefaultTeamId:'',pickerError:'',companyCards:[],ownIds:[],rankingSelection:null});
      }
      this.dashboardIdentity=identity;
      this.setData({canViewTeam,quarters:periodQuarters(this.data.quarters),periodLabel:periodLabel(this.data.year,this.data.quarters)});
      if(!canViewTeam || this.pickerIdentity && this.pickerIdentity!==access.identity(session))this.closeMemberPicker();
      if (this.properties.memberId) this.setData({scope: canViewTeam ? 'team' : 'self', memberIndex: 0});
      else if (!canViewTeam && this.data.scope === 'team') this.setData({scope: 'self', memberIndex: 0});
      const serial = this.serial = (this.serial || 0) + 1;
      this.activitySerial = (this.activitySerial || 0) + 1;
      this.rankingPeriodSerial=(this.rankingPeriodSerial||0)+1;
      this.setData({rankingPeriod:'',rankingPeriodLabel:'',rankingPeriodLoading:false,rankingPeriodError:'',demoRankingReady:false,demoRanking:[],showAllDemos:false,companyRankingReady:false,companyRankingError:'',followupRanking:[],recognizedRanking:[],loading: true, error: '', ready: false, summary: null, ranking: [], recentVisits: [], activityOpen: false, activity: [], activityLoading: false});
      try {
        const query=this.params();
        const response = await api.getFdeDashboard(query);
        const members=response.members || [];
        if (this.closed || serial !== this.serial || identity !== access.identity(getApp().globalData.session)) return;
        if (response.data_source !== 'database' || !response.summary || !Array.isArray(response.ranking) || !Array.isArray(response.recent_visits)) throw Error('看板暂未加载完成，请重试');
        const recognizedFinancial=finance.aggregate(response.summary,'recognized'),collectionFinancial=finance.aggregate(response.summary,'collection');
        const summary = {...response.summary, openAcvText: present.money(response.summary.open_acv), recognizedText:recognizedFinancial.amountText,collectionText:collectionFinancial.amountText,recognizedFinancial,collectionFinancial,financialHints:[...new Set([...recognizedFinancial.qualityHints,...collectionFinancial.qualityHints])]};
        const memberOptions = [{id: '', name: session.userName || session.account || '当前账号'}, ...members.filter(row => row.is_active!==false && String(row.id) !== String(session.userId))], selectedId = this.params().member_id;
        this.setData({summary, scopeLabel: response.scope_label || '本人协作', asOf: present.beijingTime(response.as_of),
          periodLabel: periodLabel(query.year,query.quarters),
          memberLabel: selectedId ? (memberOptions.find(row => row.id === selectedId) || {}).name || '' : '',
          memberOptions, memberIndex: Math.max(0, memberOptions.findIndex(row => row.id === selectedId)),
          ranking: response.ranking.map(row => ({...row, openAcvText: present.money(row.open_acv)})),
          recentVisits: response.recent_visits.map(present.visitItem), stages: present.visibleStageBars(response.stages || []), rhythmSource:response.rhythm || [],rhythmWeeks:response.rhythm_weeks||[], rhythm: present.progressBars(this.data.rhythmMode==='week'?(response.rhythm_weeks||[]):(response.rhythm||[]),this.data.rhythmMode), loading: false, ready: true});
        this.sortRanking();this.updateMemberPicker();
        if(session.permissions&&!access.can(session,'dashboard.ranking')){this.setData({companyCards:[],companyRankingReady:false});return;}
        try {
          const source=response.company_rankings,selection=source&&source.selection;
          const personal=!!query.member_id||query.scope==='self',target=query.member_id||session.userId;
          if(!source||source.contract_version!==2||!selection||selection.personal!==personal||personal&&selection.member_id!==target)throw Error('排名对象与当前选择不一致');
          this.setData({...companyRankings(source,query.year,query.quarters),rankingSelection:selection});
        }
        catch (error) { this.setData({demoRankingReady:false,demoRanking:[],showAllDemos:false,companyRankingReady:false,companyRankingError:error.message,followupRanking:[],recognizedRanking:[]}); }
        this.rebuildCompanyCards();
      } catch (error) {
        if (!this.closed && serial === this.serial && identity === access.identity(getApp().globalData.session)) this.setData({loading: false, error: error.message || '看板加载失败，请重试', ready: false});
      }
    },
    scope(e) { if (this.properties.memberId || this.properties.personal) return;this.closeMemberPicker(); const scope = e.currentTarget.dataset.scope; if (scope === 'team' && !this.data.canViewTeam) return; this.setData({scope, memberIndex: 0}); this.load(); },
    updateMemberPicker(){this.setData({pickerOptions:this.data.memberOptions.map(row=>({...row,group:row.team_name||row.team||'授权成员'})),pickerSelected:[(this.data.memberOptions[this.data.memberIndex]||{}).id||getApp().globalData.session.userId]});},
    openMemberPicker(){
      if(!access.canViewTeam(getApp().globalData.session,'profile.fde_read')||this.properties.personal||this.properties.memberId)return;
      this.pickerIdentity=access.identity(getApp().globalData.session);
      this.updateMemberPicker();this.setData({memberPickerOpen:true,pickerLoading:true,pickerError:''});this.triggerEvent('pickervisibilitychange',{open:true});return this.loadPickerDirectory();
    },
    closeMemberPicker(){if(!this.data.memberPickerOpen)return;this.pickerSerial=(this.pickerSerial||0)+1;this.setData({memberPickerOpen:false,pickerLoading:false});this.triggerEvent('pickervisibilitychange',{open:false});},
    async loadPickerDirectory(){
      const identity=access.identity(getApp().globalData.session),serial=this.pickerSerial=(this.pickerSerial||0)+1;
      this.setData({pickerLoading:true,pickerError:''});
      try{
        const result=await api.getFdeScopeOptions();
        if(this.closed||!this.data.memberPickerOpen||serial!==this.pickerSerial||identity!==access.identity(getApp().globalData.session))return;
        if(result.data_source!=='database'||!Array.isArray(result.members)||!Array.isArray(result.teams))throw Error('成员范围加载失败');
        assertPersonDirectory(result.members,result.teams,result.defaults);
        const pickerTeams=normalizeTeams(result.teams),self=getApp().globalData.session.userId;
        const pickerMembers=normalizeMembers(result.members,pickerTeams).map(row=>({...row,name:row.name+(row.id===self?'（本人）':'')}));
        this.setData({pickerMembers,pickerTeams,pickerDefaultTeamId:candidateTeam(pickerTeams,(result.defaults||{}).team_id),pickerLoading:false});
      }catch(error){if(serial===this.pickerSerial&&identity===access.identity(getApp().globalData.session))this.setData({pickerLoading:false,pickerError:error.message||'成员范围加载失败'});}
    },
    selectMember(event){
      const ids=event.detail.ids,session=getApp().globalData.session;
      if(!this.data.memberPickerOpen||this.pickerIdentity!==access.identity(session)||!access.canViewTeam(session,'profile.fde_read')||this.data.pickerLoading||this.data.pickerError||!Array.isArray(ids)||ids.length!==1)return;
      const selected=this.data.pickerMembers.find(row=>row.id===ids[0]);if(!selected)return;
      const id=selected.id===session.userId?'':selected.id;
      const memberOptions=this.data.memberOptions.slice();let index=memberOptions.findIndex(row=>row.id===id);
      if(index<0){memberOptions.push({...selected,id});index=memberOptions.length-1;}
      this.setData({memberOptions});this.closeMemberPicker();this.member({detail:{value:index}});
    },
    rebuildCompanyCards(){
      const selfId=getApp().globalData.session.userId,selection=this.data.rankingSelection||{};
      const personal=selection.personal!==false,ids=personal?[selection.member_id||selfId]:selection.team_ids||[];
      const cohort=personal?(selection.cohort_role==='fde_lead'?'部门全部同级 FDE 负责人':'部门全部同级 FDE'):'公司全部 FDE 团队';
      const specs=[['followupRanking','FDE 跟进数量排名','跟','blue','条'],['recognizedRanking','FDE 确收排名','收','mint','万元'],['demoRanking','FDE Demo 场景数量排名','D','amber','个']];
      this.setData({ownIds:ids,rankingCohort:cohort,companyCards:specs.map(([key,title,symbol,tone,unit])=>({key,title:personal?title:title.replace('FDE ','FDE 团队'),symbol,tone,
        error:key==='demoRanking'&&!this.data.demoRankingReady?'当前暂无完整 Demo 排名':this.data.companyRankingError,
        rows:(this.data[key]||[]).map(row=>({...row,id:row.user_id,isSelf:row.user_id===selfId,isSelected:ids.includes(row.user_id),meta:[personal?row.team_name||'未分组':'团队汇总',...(row.financialHints||[])].join(' · '),displayValue:key==='recognizedRanking'||row.value==='未登记'?row.value:row.value+' '+unit,width:row.width+'%'})),
      }))});
    },
    member(e) { this.setData({memberIndex: Number(e.detail.value)}); this.load(); },
    toggleFilter() { this.setData({filterOpen: !this.data.filterOpen, draftYearIndex: this.data.yearIndex, ...draftPeriod(this.data.quarters)}); },
    year(e) { const index=Number(e.detail.value);if(!Number.isInteger(this.data.yearOptions[index]))return;this.setData({draftYearIndex:index,...draftPeriod([])}); },
    quarter(e) { const q=Number(e.currentTarget.dataset.q);if(![0,1,2,3,4].includes(q))return;const selected=this.data.draftQuarters;this.setData(draftPeriod(q===0?[]:selected.includes(q)?selected.filter(value=>value!==q):selected.concat(q))); },
    applyPeriod() { const year=this.data.yearOptions[this.data.draftYearIndex];if(!Number.isInteger(year))return;const quarters=periodQuarters(this.data.draftQuarters);this.setData({yearIndex:this.data.draftYearIndex,year,quarters,periodLabel:periodLabel(year,quarters),filterOpen:false});return this.load(); },
    changeRhythmMode(e){const mode=e.currentTarget.dataset.mode;if(!['day','week'].includes(mode))return;this.setData({rhythmMode:mode,rhythm:present.progressBars(mode==='week'?this.data.rhythmWeeks:this.data.rhythmSource,mode)});},

    sortRanking(e) { if (e) { const index = Number(e.detail.value); this.setData({sortIndex: index, sortKey: this.data.sortOptions[index].key}); } this.setData({ranking: present.rankedRows(this.data.ranking, this.data.sortKey)}); },
    openProjects() { const p = this.params(); wx.navigateTo({url: '/pages/opportunities/index?scope=' + p.scope + '&member_id=' + encodeURIComponent(p.member_id)}); },
    openTasks(e) { const status = e.currentTarget.dataset.status || 'pending'; wx.navigateTo({url: '/pages/tasks/index?tab=' + status + '&scope=' + this.params().scope + '&member_id=' + encodeURIComponent(this.params().member_id) + (status === 'completed' ? '&year=' + this.data.year + '&quarters=' + this.data.quarters.join(',') : '')}); },
    openMember(e) { if (this.data.canViewTeam) wx.navigateTo({url: '/pages/member-growth/index?member_id=' + encodeURIComponent(e.currentTarget.dataset.id)}); },
    openVisit(e) { const row = [...this.data.recentVisits, ...this.data.activity].find(r => String(r.id) === String(e.currentTarget.dataset.id)); if (!row) return; if (!row.can_read_detail) { wx.showToast({title: '当前仅可查看这条历史摘要', icon: 'none'}); return; } wx.navigateTo({url: visitDetailUrl(row)}); },
    async changeRankingPeriod(e) {
      const period=e.currentTarget.dataset.period;
      if(!['week','month','quarter'].includes(period))return;
      const now=new Date(Date.now()+28800000),year=now.getUTCFullYear(),month=now.getUTCMonth();
      const start=new Date(Date.UTC(year,month,now.getUTCDate()));
      if(period==='week')start.setUTCDate(start.getUTCDate()-(start.getUTCDay()+6)%7);
      if(period==='month')start.setUTCDate(1);
      if(period==='quarter'){start.setUTCMonth(Math.floor(month/3)*3,1);}
      const end=new Date(start);
      if(period==='week')end.setUTCDate(end.getUTCDate()+7);
      if(period==='month')end.setUTCMonth(end.getUTCMonth()+1);
      if(period==='quarter')end.setUTCMonth(end.getUTCMonth()+3);
      const from=start.toISOString().slice(0,10),to=new Date(end-86400000).toISOString().slice(0,10);
      const serial=this.rankingPeriodSerial=(this.rankingPeriodSerial||0)+1,identity=access.identity(getApp().globalData.session);
      this.setData({rankingPeriod:period,rankingPeriodLabel:from+' ～ '+to,rankingPeriodLoading:true,rankingPeriodError:'',ranking:[]});
      try{
        const result=await api.getFdeDashboard({...this.params(),year,quarters:period==='quarter'?[Math.floor(month/3)+1]:[],date_from:from,date_to:to});
        if(this.closed||serial!==this.rankingPeriodSerial||identity!==access.identity(getApp().globalData.session))return;
        if(period!=='quarter' && (!result.filters || result.filters.date_from!==from || result.filters.date_to!==to))throw Error('该周期统计尚未接通，请选择季度查看');
        if(result.data_source!=='database'||!Array.isArray(result.ranking))throw Error('团队统计暂未加载完成');
        this.setData({ranking:result.ranking.map(row=>({...row,openAcvText:present.money(row.open_acv)})),rankingPeriodLoading:false});
        this.sortRanking();this.updateMemberPicker();
      }catch(error){if(!this.closed&&serial===this.rankingPeriodSerial&&identity===access.identity(getApp().globalData.session))this.setData({rankingPeriodLoading:false,rankingPeriodError:error.message||'统计加载失败'});}
    },

    openActivity() {
      const context={...this.params(),periodLabel:this.data.periodLabel,scopeLabel:this.data.scopeLabel,memberLabel:this.data.memberLabel};
      wx.navigateTo({url:'/pages/fde-records/index?context='+encodeURIComponent(JSON.stringify(context))});
    },
    closeActivity() { this.activitySerial = (this.activitySerial || 0) + 1; this.setData({activityOpen: false, activityLoading: false}); },
    async loadActivity() {
      if (this.data.activityLoading) return;
      const serial = this.activitySerial = (this.activitySerial || 0) + 1, identity = access.identity(getApp().globalData.session);
      this.setData({activityLoading: true, activityError: ''});
      try {
        const response = await api.getFdeActivity({...this.params(), offset: this.data.activityOffset, limit: 50});
        if (this.closed || serial !== this.activitySerial || identity !== access.identity(getApp().globalData.session)) return;
        if (!Array.isArray(response.items) || !Number.isInteger(response.total)) throw Error('记录暂未加载完成');
        this.setData({activity: this.data.activity.concat(response.items.map(present.visitItem)), activityMore: response.has_more, activityOffset: response.next_offset, activityTotal: response.total, activityLoading: false});
      } catch (error) { if (!this.closed && serial === this.activitySerial && identity === access.identity(getApp().globalData.session)) this.setData({activityLoading: false, activityError: error.message || '记录加载失败'}); }
    },
  },
});
