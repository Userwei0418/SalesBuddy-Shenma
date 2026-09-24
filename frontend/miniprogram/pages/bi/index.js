const access = require('../../utils/access');
const { defaultQuarterKeys, selectedQuarters, matchesSelectedQuarter } = require('../../utils/dashboardQuarters');
const { beijingDateParts } = require('../../utils/opportunityQuarter');
const apiClient = require("../../utils/apiClient");
const { opportunityProbability } = require("../../utils/customerDetail");
const { rankingDisplay } = require("../../utils/rankingDisplay");
const { recentVisits } = require("../../utils/followupRanking");

const stages = () => require('../../utils/opportunity').STAGES.filter(s => s.status === 'open').map((s, tone) => ({probability:s.probability,name:s.label,tone})).sort((a, b) => b.probability - a.probability);
const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];
function number(value) { return Number(value || 0); }
function money(value) { return `¥${Math.round(number(value)).toLocaleString("zh-CN")}`; }
function percent(value) { return Math.max(0, Math.min(100, Math.round(number(value)))); }
function dateValue(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}
function dateKey(value) {
  const date = dateValue(value);
  return date ? `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}` : "";
}
function quarterOf(value) {
  const date = beijingDateParts(value);
  if (!date) return null;
  return { key:`${date.year}-Q${date.quarter}`, label:`${date.year} Q${date.quarter}`, year:date.year, quarter:date.quarter };
}
function currentQuarter() { return quarterOf(new Date()); }
function quarterDates(item) {
  const startMonth = (item.quarter - 1) * 3;
  return { start: new Date(item.year, startMonth, 1), end: new Date(Date.UTC(item.year, startMonth + 3, 1) - 8 * 3600000) };
}
function quarterCountdown(item) {
  const end = quarterDates(item).end;
  const delta = end.getTime() - Date.now();
  if (delta <= 0) return { closed: true, days: "00", hours: "00", minutes: "00", sentence: `${item.label} 已结束` };
  const days = Math.floor(delta / 86400000);
  const hours = Math.floor(delta % 86400000 / 3600000);
  const minutes = Math.floor(delta % 3600000 / 60000);
  return {
    closed: false,
    days: String(days).padStart(2, "0"),
    hours: String(hours).padStart(2, "0"),
    minutes: String(minutes).padStart(2, "0"),
    sentence: `距离 ${item.label} 结束还剩 ${days} 天`,
  };
}
function sourceTime(value) {
  const date = dateValue(value);
  if (!date) return "当前实时";
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}
function buildQuarterOptions(opportunities, forecasts = []) {
  const now = currentQuarter();
  const map = {};
  [-1, 0, 1, 2].forEach((offset) => {
    let year = now.year;
    let quarter = now.quarter + offset;
    while (quarter < 1) { quarter += 4; year -= 1; }
    while (quarter > 4) { quarter -= 4; year += 1; }
    map[`${year}-Q${quarter}`] = { key: `${year}-Q${quarter}`, label: `${year} Q${quarter}`, year, quarter, count: 0 };
  });
  opportunities.forEach((item) => {
    const q = quarterOf(item.expected_close_date);
    if (!q) return;
    if (!map[q.key]) map[q.key] = { ...q, count: 0 };
    map[q.key].count += 1;
  });
  forecasts.forEach((forecast) => {
    const key = `${forecast.year}-Q${forecast.quarter}`;
    if (!map[key]) {
      map[key] = { key, label: `${forecast.year} Q${forecast.quarter}`, year: forecast.year, quarter: forecast.quarter, count: 0 };
    }
  });
  const years = new Set([now.year, ...Object.values(map).map(item=>Number(item.year))]);
  years.forEach(year=>[1,2,3,4].forEach(quarter=>{
    const key=`${year}-Q${quarter}`;
    if(!map[key])map[key]={key,label:`${year} Q${quarter}`,year,quarter,count:0};
  }));
  return Object.keys(map).map((key) => map[key]).sort((a, b) => a.year - b.year || a.quarter - b.quarter);
}
function sevenDayVisits(visits) {
  const rows = [];
  const counts = {};
  visits.forEach((item) => { const key = dateKey(item.interaction_at); if (key) counts[key] = (counts[key] || 0) + 1; });
  for (let offset = 6; offset >= 0; offset -= 1) {
    const date = new Date();
    date.setHours(0, 0, 0, 0);
    date.setDate(date.getDate() - offset);
    const key = dateKey(date);
    rows.push({ key, weekday: `周${WEEKDAYS[date.getDay()]}`, count: counts[key] || 0, isToday: offset === 0 });
  }
  const max = Math.max(...rows.map((item) => item.count), 1);
  return rows.map((item) => ({ ...item, height: `${Math.max(item.count ? 22 : 5, Math.round(item.count / max * 100))}%` }));
}
function quarterTimeline(opportunities, selected) {
  const rows = selected.quarters.flatMap(quarter => [0,1,2].map(offset => {
    const month=(quarter-1)*3+offset;
    const items=opportunities.filter(item=>{const date=beijingDateParts(item.expected_close_date);return date && date.year===selected.year && date.month===month+1;});
    const value=items.reduce((sum,item)=>sum+number(item.amount),0);
    return {month:`${month+1}月`,quarter:`Q${quarter}`,value,amount:money(value),count:items.length,status:['蓄水月','推进月','冲刺月'][offset]};
  }));
  const total=rows.reduce((sum,item)=>sum+item.value,0);
  return rows.map(item=>({...item,share:total?Math.round(item.value/total*100):0}));
}
// BACKEND-CONTRACT BI: /dashboard金额单位元；quarter_actuals按笔数区分未登记与0。
// quarter_forecasts 区分原始计划与 weighted_* 概率预测；页面只展示服务端计算的预测值。
function financialMetrics(raw,selected,opportunities) {
    const total = opportunities.reduce((sum, item) => sum + number(item.amount), 0);
    const weighted = opportunities.filter(item=>opportunityProbability(item)>=10 && item.status!=='lost').reduce((sum, item) => sum + number(item.amount) * opportunityProbability(item) / 100, 0);
    const highProbability = opportunities.filter((item) => opportunityProbability(item) >= 70).reduce((sum, item) => sum + number(item.amount), 0);
    const quarterRows = (raw.quarter_forecasts || []).filter(q => matchesSelectedQuarter(q,selected));
    const forecastMetric = (key,label,color) => {
      const filled=quarterRows.filter(q => q[key] !== null && q[key] !== undefined);
      return {kind:'value',label,value:filled.length ? money(filled.reduce((sum,q)=>sum+number(q[key]),0)) : '未填写',sub:filled.length ? `${filled.length} 条已填预测 · 非实际发生` : '在商机季度预测中填写',tone:color,compact:false};
    };
    const actual = (raw.quarter_actuals || []).filter(q=>matchesSelectedQuarter(q,selected)).reduce((sum,row)=>{['recognized_amount','collection_amount','recognized_count','collection_count'].forEach(key=>{sum[key]=(sum[key]||0)+number(row[key]);});return sum;},{});
    const actualMetric = (key, countKey, label, tone) => ({
      kind: "value", label, value: Number(actual[countKey]) > 0 ? money(actual[key]) : "未登记",
      sub: Number(actual[countKey]) > 0 ? `${actual[countKey]} 笔已确认记录` : "确认登记后计入所选季度", tone,
    });
    const kpis = [
      actualMetric("recognized_amount", "recognized_count", "已登记确收", "blue"),
      actualMetric("collection_amount", "collection_count", "已登记回款", "mint"),
      forecastMetric("weighted_recognized_amount","预测含税确收","orange"),
      forecastMetric("weighted_collection_amount","预测回款","mint"),
      { kind: "value", label: "总商机 ACV", value: money(total), sub: `${opportunities.length} 个预计关单的在推商机`, tone: "blue", compact: money(total).length > 11 },
      { kind: "value", label: "加权预测 ACV", value: money(weighted), sub: "在推商机金额 × 阶段概率（含10%）", tone: "violet", compact: money(weighted).length > 11 },
      { kind: "value", label: "高概率 ACV", value: money(highProbability), sub: "70% 及以上商机", tone: "amber", compact: money(highProbability).length > 11 },
      { kind: "value", label: "预计关单数", value: String(opportunities.length), sub: `${selected.label} 预计成交`, tone: "mint", compact: false },
    ];

    return {total,kpis,quarterRows,actual};
}

Page({
  data: {
    loading:true,loadError:'',role:'sales',viewMode:'personal',isFde:false,
    selectedMemberId:'',selectedTeamGroups:[],selectedTeamChoice:'all',teamPickerSelected:['all'],
    memberOptions:[],teamOptions:[],optionsLoading:false,optionsError:'',memberLabel:'本人',teamLabel:'部门合计',
    quarterIndex:0,quarterOptions:[],selectedQuarterKeys:[],quarterFilterDirty:false,showQuarterFilter:false,
    quarterYears:[],quarterYearIndex:0,quarterChoices:[],selectedQuarter:{label:'--'},
    countdown:{days:'--',hours:'--',minutes:'--',sentence:'季度计划'},
    kpis:[],totalAcv:'—',opportunityCount:0,activeOpportunityCount:null,quarterEmpty:false,
    funnel:[],visitDays:[],weeklyVisitCount:0,timeline:[],sourceDate:'--',dataModeLabel:'实时数据',
    rankingLoading:false,rankingMessage:'',rankingCards:[],ownIds:[],ownRegionCodes:[],cohortLabel:'',
  },
  identityKey(){return access.identity(getApp().globalData.session);},
  onUnload(){this._loadId=(this._loadId||0)+1;this._rankingLoadId=(this._rankingLoadId||0)+1;this._optionsLoadId=(this._optionsLoadId||0)+1;},
  onShow(){
    const app=getApp();
    if(app.guardPage&&!app.guardPage(this,'bi'))return;
    const options=access.presentationOptions(app.globalData.session,'bi');
    const selected=options.find(row=>row.value===this.data.presentation)||options.find(row=>row.value===(access.isFde(app.globalData.role)?'fde':'sales'))||options[0];
    this.setData({presentationOptions:options,presentation:selected&&selected.value});
    if(selected&&selected.value==='fde'){this.onUnload();this.setData({isFde:true});return;}
    if(!app.ensureLogin())return;
    this.setData({isFde:false});return this.loadData();
  },
  changePresentation(e){const choice=e.currentTarget.dataset.kind;if(!this.data.presentationOptions.some(row=>row.value===choice))return;this.setData({presentation:choice});return this.onShow();},
  loadData(){
    const app=getApp(),role=app.globalData.role,context=this.identityKey();
    this.setData({canViewTeam:access.canViewTeam(app.globalData.session,'dashboard.read')});
    if(context!==this._context){
      this.onUnload();this._context=context;
      this.setData({role,viewMode:access.canViewTeam(app.globalData.session,'dashboard.read')&&role==='manager'?'team':'personal',selectedMemberId:'',selectedTeamGroups:[],selectedTeamChoice:'all',teamPickerSelected:['all'],
        memberOptions:[],teamOptions:[],memberLabel:app.globalData.session.userName||'本人',teamLabel:'部门合计',
        selectedQuarterKeys:defaultQuarterKeys(),quarterFilterDirty:false,showQuarterFilter:false,rankingCards:[]});
    }
    this._nativeRankings=null;this._nativeRankingKey=null;this._rankingPending=null;this._rankingPendingKey=null;
    this._rankingLoadId=(this._rankingLoadId||0)+1;
    this.setData({quarterOptions:buildQuarterOptions([],this.data.quarterOptions)});this.syncQuarterFilter();
    if(!this.data.canViewTeam)return Promise.all([this.loadFacts(),this.loadRankingData()]);
    const options=this.loadOptions(false),optionsSerial=this._optionsLoadId;
    return options.then(()=>{
      if(optionsSerial!==this._optionsLoadId||context!==this.identityKey())return;
      return Promise.all([this.loadFacts(),this.loadRankingData()]);
    });
  },
  factsQuery(){return {personal:this.data.viewMode==='personal',member_id:this.data.viewMode==='personal'?this.data.selectedMemberId:'',
    team_groups:this.data.viewMode==='team'&&this.data.canViewTeam?this.data.selectedTeamGroups.slice().sort():[]};},
  loadFacts(){
    const query=this.factsQuery(),key=JSON.stringify(query),context=this.identityKey(),serial=this._loadId=(this._loadId||0)+1;
    this._raw=null;this.setData({loading:true,loadError:'',kpis:[],totalAcv:'—',sourceDate:'--',funnel:[],visitDays:[],timeline:[]});
    this.updateActiveCount();
    wx.showNavigationBarLoading();
    return apiClient.getDashboard(query.personal,{member_id:query.member_id,team_groups:query.team_groups}).then(response=>{
      if(serial!==this._loadId||context!==this.identityKey()||key!==JSON.stringify(this.factsQuery()))return;
      if(!response||response.data_source!=='database'||!Array.isArray(response.opportunities)||!Array.isArray(response.quarter_forecasts)||!Array.isArray(response.quarter_actuals))throw Error('看板数据格式异常，请重试');
      const selfId=String(getApp().globalData.session.userId||''),target=query.member_id||selfId;
      if(!selfId)throw Error('登录身份缺失，请重新登录');
      const selected=response.selection;
      if(query.personal){
        if(![target===selfId?'self':'member'].includes(response.scope))throw Error('未取得所选成员范围的数据');
        if(query.member_id&&(!selected||selected.member_id!==target))throw Error('返回成员与当前选择不一致');
      }else{
        const dynamicTeams=query.team_groups.length>0&&query.team_groups.every(code=>code.startsWith('team:'));
        const allowed=this.data.role==='manager'&&query.team_groups.length!==1&&!dynamicTeams?['workspace','all','department']:['workspace','all','department','team'];
        if(!allowed.includes(response.scope))throw Error('未取得所选团队范围的数据');
        if(query.team_groups.length&&(!selected||JSON.stringify((selected.team_groups||[]).slice().sort())!==JSON.stringify(query.team_groups)))throw Error('返回团队与当前选择不一致');
      }
      // Defense in depth for personal facts; aggregate peer ranks are handled separately.
      const owned=(rows,field)=>query.personal?rows.filter(row=>!row[field]||String(row[field])===target):rows;
      this._raw={...response,opportunities:owned(response.opportunities,'owner_id'),quarter_forecasts:owned(response.quarter_forecasts,'owner_id'),recent_visits:owned(response.recent_visits||[],'recorder_id')};
      this.setData({loading:false,quarterOptions:buildQuarterOptions(this._raw.opportunities,[...this._raw.quarter_forecasts,...this._raw.quarter_actuals,...this.data.quarterOptions])});
      this.syncQuarterFilter();this.rebuildFacts();
    }).catch(error=>{
      if(serial!==this._loadId||context!==this.identityKey())return;
      this.setData({loading:false,loadError:error.message||'看板加载失败'});
    }).finally(()=>{if(serial===this._loadId)wx.hideNavigationBarLoading();});
  },
  loadOptions(reload=true){
    if(!this.data.canViewTeam)return Promise.resolve();
    const serial=this._optionsLoadId=(this._optionsLoadId||0)+1,context=this.identityKey();
    this.setData({optionsLoading:true,optionsError:''});
    return apiClient.getDashboardOptions().then(response=>{
      if(serial!==this._optionsLoadId||context!==this.identityKey())return;
      if(!response||!Array.isArray(response.members)||!Array.isArray(response.team_groups))throw Error('可选范围加载失败');
      const selfId=getApp().globalData.session.userId;
      const memberOptions=response.members.map(row=>({...row,group:row.team||'未分组',name:row.name+(row.id===selfId?'（本人）':''),meta:row.account_code||''}));
      const teams=response.team_groups.map(row=>({id:row.code,name:row.name,kind:row.kind||''}));
      const teamOptions=[{id:'all',name:'全部团队'},...teams];
      const previousTeams=this.data.selectedTeamGroups.slice();
      const selectedTeamChoice=teams.some(row=>row.id===this.data.selectedTeamChoice)?this.data.selectedTeamChoice:'all';
      const nextTeams=selectedTeamChoice==='all'?teams.map(row=>row.id):[selectedTeamChoice];
      this.setData({optionsLoading:false,memberOptions,teamOptions,selectedTeamChoice,selectedTeamGroups:nextTeams});this.updateSelectionLabels();
      if(reload&&this.data.canViewTeam&&this.data.viewMode==='team'&&JSON.stringify(nextTeams)!==JSON.stringify(previousTeams)){
        return Promise.all([this.loadFacts(),this.loadRankingData()]);
      }
    }).catch(error=>{if(serial===this._optionsLoadId&&context===this.identityKey())this.setData({optionsLoading:false,optionsError:error.message||'可选范围加载失败'});});
  },
  updateSelectionLabels(){
    const id=this.data.selectedMemberId||getApp().globalData.session.userId;
    const member=this.data.memberOptions.find(row=>row.id===id);
    this.setData({memberLabel:member?member.name:this.data.selectedMemberId?'所选成员':'本人',
      memberSelected:[id],teamPickerSelected:[this.data.selectedTeamChoice],teamLabel:this.data.selectedTeamChoice==='all'?'部门合计 · 全部团队':(this.data.teamOptions.find(row=>row.id===this.data.selectedTeamChoice)||{}).name||'所选团队'});
  },
  selectMember(event){
    const id=event.detail.ids[0];if(!this.data.memberOptions.some(row=>row.id===id))return;
    this.setData({selectedMemberId:id===getApp().globalData.session.userId?'':id});this.updateSelectionLabels();return Promise.all([this.loadFacts(),this.loadRankingData()]);
  },
  selectTeams(event){
    const ids=event.detail.ids;
    if(!this.data.canViewTeam||!Array.isArray(ids)||ids.length!==1||!this.data.teamOptions.some(row=>row.id===ids[0]))return;
    const selectedTeamChoice=ids[0];
    const selectedTeamGroups=selectedTeamChoice==='all'?this.data.teamOptions.filter(row=>row.id!=='all').map(row=>row.id):[selectedTeamChoice];
    this.setData({selectedTeamChoice,selectedTeamGroups:selectedTeamGroups.sort()});this.updateSelectionLabels();return Promise.all([this.loadFacts(),this.loadRankingData()]);
  },
  changeView(event){
    const mode=event.currentTarget.dataset.mode;
    if(!['personal','team'].includes(mode)||mode===this.data.viewMode||!this.data.canViewTeam)return;
    this.setData({viewMode:mode});this.updateSelectionLabels();return Promise.all([this.loadFacts(),this.loadRankingData()]);
  },
  rankingQuery(){const quarter=selectedQuarters(this.data.quarterOptions,this.data.selectedQuarterKeys);return quarter?{year:quarter.year,quarters:quarter.quarters,...this.factsQuery()}:null;},
  reloadRankings(){return this.loadRankingData(true);},
  loadRankingData(force=false){
    const session=getApp().globalData.session;
    if(session.permissions&&!access.can(session,'dashboard.ranking')){this._nativeRankings=null;this._rankingLoadId=(this._rankingLoadId||0)+1;this.setData({rankingLoading:false,rankingMessage:'',rankingCards:[],activeOpportunityCount:null});return Promise.resolve();}
    const query=this.rankingQuery();if(!query)return Promise.resolve();
    const selectedMember=this.data.memberOptions.find(row=>row.id===(query.member_id||''));
    if(query.personal&&selectedMember&&['fde','fde_lead'].includes(selectedMember.role)){
      this._rankingLoadId=(this._rankingLoadId||0)+1;this._rankingPending=null;this._rankingPendingKey=null;
      this._nativeRankings=null;this._nativeRankingKey=null;this.setData({rankingLoading:false,rankingMessage:'FDE暂不提供销售排名'});this.rebuildRankingCards();return Promise.resolve();
    }
    const key=JSON.stringify(query),context=this.identityKey();
    if(!force&&this._nativeRankings&&key===this._nativeRankingKey){this.rebuildRankingCards();return Promise.resolve();}
    if(!force&&key===this._rankingPendingKey&&this._rankingPending)return this._rankingPending;
    const serial=this._rankingLoadId=(this._rankingLoadId||0)+1;
    this._nativeRankings=null;this._rankingPendingKey=key;
    this.setData({rankingLoading:true,rankingMessage:'',activeOpportunityCount:null});this.rebuildRankingCards();
    this._rankingPending=apiClient.getDashboardRankings(query).then(payload=>{
      if(serial!==this._rankingLoadId||context!==this.identityKey()||key!==JSON.stringify(this.rankingQuery()))return;
      const selected=payload&&payload.selection,target=query.member_id||getApp().globalData.session.userId;
      if(!payload||payload.contract_version!==2||payload.data_source!=='database'||payload.scope!==(query.personal?'peer':'company_teams')||!selected||selected.personal!==query.personal)throw Error('排名范围与当前选择不一致');
      if(query.personal&&selected.member_id!==target)throw Error('排名成员与当前选择不一致');
      if(!query.personal&&query.team_groups.length&&JSON.stringify(selected.team_groups.slice().sort())!==JSON.stringify(query.team_groups))throw Error('排名团队与当前选择不一致');
      if(!query.personal&&(!payload.followup||payload.followup.calculation!=='team_followup_per_capita_v1'))throw Error('当前后端尚未提供人均跟进排名，请在配套版本上线后重试');
      rankingDisplay(payload.opportunity_acv);rankingDisplay(payload.followup);rankingDisplay(payload.region);
      this._nativeRankings=payload;this._nativeRankingKey=key;
      this.setData({rankingLoading:false,rankingMessage:''});this.rebuildRankingCards();
    }).catch(error=>{
      if(serial!==this._rankingLoadId||context!==this.identityKey())return;
      this.setData({rankingLoading:false,rankingMessage:error.message||'排名加载失败'});this.rebuildRankingCards();
    }).finally(()=>{if(serial===this._rankingLoadId){this._rankingPending=null;this._rankingPendingKey=null;}});
    return this._rankingPending;
  },
  rebuildRankingCards(){
    const payload=this._nativeRankings,selfId=getApp().globalData.session.userId;
    const personal=this.data.viewMode==='personal',target=this.data.selectedMemberId||selfId;
    const selected=payload&&payload.selection,regions=selected&&selected.team_groups||[];
    const roleLabel={sales:'销售',supervisor:'主管',manager:'总经理'};
    const cohort=personal?'部门全部同级'+(roleLabel[selected&&selected.cohort_role]||'人员'):'公司全部销售团队';
    const summaryIds=personal?[target]:regions;
    const rows=(key,moneyValue)=>payload?rankingDisplay(payload[key],false,moneyValue).rows.map(row=>({
      ...row,id:row.key,isSelf:row.user_id===selfId,isSelected:(key==='region'?regions:summaryIds).includes(row.key),
      meta:key==='followup'?(personal?`跟进 ${row.customerCount} 家客户${row.team?' · '+row.team:''}`:`共 ${row.count} 次 · ${row.member_count} 名有效成员 · ${row.customerCount} 家客户`):`${row.count} 个在推商机${personal&&row.team?' · '+row.team:''}`,
      displayValue:moneyValue?row.amount:personal?row.value+' 次':row.member_count?Number(row.average).toFixed(2).replace(/\.?0+$/,'')+' 次/人':'—',
      rank:row.rank==null?'—':row.rank,
      members:key==='followup'&&!personal?row.members:undefined,
      memberSummary:key==='followup'&&!personal?(row.member_count?`总次数 ${row.count} ÷ 有效成员 ${row.member_count} 人 = ${Number(row.average).toFixed(2)} 次/人`:'暂无有效业务成员，不参与人均排名'):'',
    })):[];
    const cards=[
      {key:'acv',title:personal?'商机 ACV 排名':'团队商机 ACV 排名',symbol:'商',tone:'blue',subtitle:cohort,period:this.data.selectedQuarter.label+' · 在推商机',rows:rows('opportunity_acv',true),summaryIds,emptySummary:'所选团队不参与销售团队排名，点击查看完整榜单'},
      {key:'followup',title:personal?'跟进排名':'团队人均跟进排名',symbol:'跟',tone:'mint',subtitle:cohort,period:'近7天（含今天）· 已确认跟进',rows:rows('followup',false),summaryIds,emptySummary:'所选团队不参与销售团队排名，点击查看完整榜单'},
    ];
    // Personal region comparison uses the server's region labels, separate from team selection.
    if(personal)cards.push({key:'region',title:'区域排名',symbol:'区',tone:'amber',subtitle:((payload&&payload.region&&payload.region.rows)||[]).map(row=>row.name||row.label||row.team).filter(Boolean).join(' / ')||'所属区域',period:this.data.selectedQuarter.label+' · 在推商机 ACV',rows:rows('region',true),summaryIds:regions,emptySummary:'所选成员暂无所属区域，点击查看完整榜单'});
    this.setData({ownIds:summaryIds,ownRegionCodes:regions,cohortLabel:cohort,rankingCards:cards,
      rankingSubjectLabel:personal?this.data.memberLabel+' · 同级排名及所属区域名次':'所选团队 · 公司同类团队排名'});
    this.updateActiveCount();
  },
  updateActiveCount(){
    const payload=this._nativeRankings,active=payload&&payload.active_opportunities;
    if(!active||!Array.isArray(active.rows)){this.setData({activeOpportunityCount:null});return;}
    let rows;
    if(this.data.viewMode==='personal'){const id=this.data.selectedMemberId||getApp().globalData.session.userId;rows=active.rows.filter(row=>row.user_id===id);}
    else if(this.data.canViewTeam&&this.data.selectedTeamGroups.length)rows=(active.groups||[]).filter(row=>this.data.selectedTeamGroups.includes(row.code));
    else rows=active.rows;
    this.setData({activeOpportunityCount:rows.reduce((sum,row)=>sum+Number(row.value),0)});
  },
  rebuild(){this.rebuildFacts();return this.loadRankingData();},
  rebuildFacts(){
    const raw=this._raw,selected=selectedQuarters(this.data.quarterOptions,this.data.selectedQuarterKeys);
    if(!raw||!selected||this.data.loading||this.data.loadError)return;
    const opportunities=raw.opportunities.filter(item=>!['won','lost'].includes(item.status)&&matchesSelectedQuarter(quarterOf(item.expected_close_date),selected));
    const {total,kpis,quarterRows,actual}=financialMetrics(raw,selected,opportunities);
    const funnel=stages().map(stage=>{const items=opportunities.filter(item=>opportunityProbability(item)===stage.probability),value=items.reduce((sum,item)=>sum+number(item.amount),0);return {...stage,value,amount:money(value),count:items.length};});
    const max=Math.max(1,...funnel.map(row=>row.value));
    funnel.forEach(row=>{row.width=`${Math.max(row.value?36:24,Math.round(row.value/max*88))}%`;});
    const visits=recentVisits(raw.recent_visits||[]);
    this.setData({kpis,totalAcv:money(total),opportunityCount:opportunities.length,
      quarterEmpty:!opportunities.length&&!quarterRows.length&&!actual.recognized_count&&!actual.collection_count,
      funnel,visitDays:sevenDayVisits(visits),weeklyVisitCount:visits.length,timeline:quarterTimeline(opportunities,selected),
      sourceDate:sourceTime((raw.summary||{}).source_date),dataModeLabel:'数据库实时统计'});
  },
  syncQuarterFilter() {
    const selected=selectedQuarters(this.data.quarterOptions,this.data.selectedQuarterKeys);
    if(!selected)return;
    const years=[...new Set(this.data.quarterOptions.map(item=>item.year))].sort((a,b)=>b-a);
    this.setData({selectedQuarter:selected,countdown:quarterCountdown({...selected,label:`${selected.year} Q${selected.quarter}`}),quarterIndex:this.data.quarterOptions.findIndex(item=>item.key===selected.key),
      quarterYears:years,quarterYearIndex:years.indexOf(selected.year),
      quarterChoices:this.data.quarterOptions.filter(item=>item.year===selected.year).map(item=>({...item,selected:this.data.selectedQuarterKeys.includes(item.key)}))});
  },
  toggleQuarterFilter(){this.setData({showQuarterFilter:!this.data.showQuarterFilter});},
  closeQuarterFilter(){this.setData({showQuarterFilter:false});},
  toggleQuarter(event){
    const key=event.currentTarget.dataset.key;
    if(!this.data.quarterChoices.some(item=>item.key===key))return;
    const keys=this.data.selectedQuarterKeys.slice();const index=keys.indexOf(key);
    if(index>=0){if(keys.length===1){wx.showToast({title:'请至少选择一个季度',icon:'none'});return;}keys.splice(index,1);}else keys.push(key);
    this.setData({selectedQuarterKeys:keys,quarterFilterDirty:true});this.syncQuarterFilter();this.rebuild();
  },
  changeQuarterYear(event){
    const year=this.data.quarterYears[Number(event.detail.value)];if(!year)return;
    const current=currentQuarter();
    const keys=year===current.year?defaultQuarterKeys():[1,2,3,4].map(q=>`${year}-Q${q}`);
    this.setData({selectedQuarterKeys:keys,quarterFilterDirty:true});this.syncQuarterFilter();this.rebuild();
  },
  resetQuarters(){this.setData({selectedQuarterKeys:defaultQuarterKeys(),quarterFilterDirty:false});this.syncQuarterFilter();this.rebuild();},
  changeQuarter(event) {
    const option=this.data.quarterOptions[Number(event.detail.value||0)];if(!option)return;
    this.setData({selectedQuarterKeys:[option.key],quarterFilterDirty:true});this.syncQuarterFilter();this.rebuild();
  },

});
