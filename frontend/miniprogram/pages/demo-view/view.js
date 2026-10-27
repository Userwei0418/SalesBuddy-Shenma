const auth=require('../../utils/restrictedSession');
const names={customers:'客户',opportunities:'商机','follow-ups':'跟进',tasks:'任务'};
const moduleViews={customers:{title:'客户经营',note:'查看客户档案',icon:'tab-customers'},opportunities:{title:'商机管理',note:'掌握业务进展',icon:'business'},'follow-ups':{title:'跟进记录',note:'查看沟通记录',icon:'tab-assistant'},tasks:{title:'任务管理',note:'查看任务动态',icon:'tab-workbench'}};
const pageTitles={customers:'客户经营',opportunities:'商机管理','follow-ups':'跟进记录',tasks:'任务管理'};
function navigation(modules){
  return [{key:'home',label:'总览',icon:'home'},...['customers','opportunities','tasks'].filter(key=>modules.includes(key)).map(key=>({key,label:names[key],icon:moduleViews[key].icon})),{key:'account',label:'我的',icon:'tab-profile'}]
    .map(item=>({...item,icon:'/images/icons/'+item.icon+'.png',selectedIcon:'/images/icons/'+item.icon+'-active.png'}));
}
function cardDetails(kind,item){
  const status=kind==='tasks'?taskState(item):kind==='opportunities'?labels(item.stage):kind==='customers'?customerLabel('stage',item.stage):(interactionModes[item.type]||'跟进记录');
  return {initial:String((kind==='tasks'?item.title:kind==='follow-ups'?item.customer?.name:item.name)||names[kind]).slice(0,1),badge:status,
    tone:['completed','won'].includes(item.status||item.stage)?'good':['needs_adjustment','deferred','pending_review','paused'].includes(item.status)?'watch':['cancelled','lost','rejected'].includes(item.status||item.stage)?'neutral':'primary',
    context:kind==='customers'?customerLabel('industry',item.industry):kind==='follow-ups'?(item.opportunity?.name||'客户跟进'):(item.customer?.name||''),
    secondary:kind==='customers'?(item.level?'客户等级 '+item.level:''):kind==='tasks'&&item.execution_mode!=='execution_v3'?progress(item.progress_percent):'',
    preview:kind==='follow-ups'?item.display_text||'':kind==='tasks'?item.description||'':'',
    footer:kind==='follow-ups'?'跟进日期 '+date(item.follow_up_date):kind==='tasks'?'截止时间 '+date(item.due_at):'更新于 '+date(item.updated_at),
    amountText:kind==='opportunities'?amount(item.amount):''};
}
// Pure display labels mirror domain/opportunities.py and customerDetail.js.
// No ordinary metadata/service dependency is loaded in this subpackage.
const stages={identified:'意向沟通－10%',qualified:'商机确认－30%',solution:'方案沟通－50%',proposal:'商务谈判－70%',negotiation:'客户签约－90%',won:'已赢单',lost:'已丢单'};
const customerStages={lead:'待分配',prospect:'潜在客户',opportunity:'商机客户',active:'持续跟进',dormant:'暂缓跟进',won:'已成交',lost:'已流失',archived:'已归档'};
const interactionModes={offline_meeting:'线下会议',online_meeting:'线上会议',phone_voice:'电话/语音',social_meal:'饭局/聚会'};
const industries={manufacturing:'制造业',smart_manufacturing:'智能制造',software:'企业软件',enterprise_software:'企业软件',new_energy:'新能源',healthcare:'医药健康',transportation:'交通物流',logistics:'交通物流',artificial_intelligence:'人工智能',finance:'金融',internet:'互联网',other:'其他'};
const customerLabel=(field,value)=>({industry:industries,stage:customerStages}[field]?.[value])||value||'未填写';
const statuses={pending_confirm:'待接受',pending_execution:'待执行',in_progress:'进行中',pending_review:'待验收',completed:'已完成',cancelled:'已取消',rejected:'已拒绝',paused:'已暂停',deferred:'已延期',needs_adjustment:'需调整'};
const labels=value=>stages[value]||statuses[value]||value||'未填写';
const date=value=>{
  if(!value)return '未填写';
  if(/^\d{4}-\d{2}-\d{2}$/.test(String(value)))return value;
  const parsed=new Date(value);return Number.isNaN(parsed.getTime())?'未填写':new Date(parsed.getTime()+8*3600000).toISOString().replace('T',' ').slice(0,16);
};
function progress(value){return typeof value==='number'&&Number.isFinite(value)&&value>=0&&value<=100?value+'%':'暂无进度';}
function taskState(item){const reason=item.status==='needs_adjustment'?({adjustment_request:'调整请求',paused:'已暂停',awaiting_review:'待验收'}[item.processing_reason]||''):'';return [labels(item.status),reason].filter(Boolean).join(' · ');}
function amount(value){if(value===null||value===undefined||value===''||!Number.isFinite(Number(value)))return '未填写';return Number(value)/10000+' 万元';}
function present(kind,item){
  return {...item,...cardDetails(kind,item),title:kind==='tasks'?item.title:kind==='follow-ups'?(item.customer?.name||'跟进记录'):item.name,
    subtitle:kind==='customers'?[item.industry&&customerLabel('industry',item.industry),item.level,item.stage&&customerLabel('stage',item.stage)].filter(Boolean).join(' · '):
      kind==='opportunities'?[item.customer?.name,labels(item.stage),amount(item.amount)].filter(Boolean).join(' · '):
      kind==='tasks'?[taskState(item),item.execution_mode==='execution_v3'?'':progress(item.progress_percent)].filter(Boolean).join(' · '):date(item.follow_up_date),
    dateText:date(item.updated_at)};
}
function fields(kind,item){
  const entries=kind==='customers'?[['客户名称',item.name],['行业',customerLabel('industry',item.industry)],['等级',item.level],['阶段',customerLabel('stage',item.stage)],['业务介绍',item.description]]:
    kind==='opportunities'?[['商机名称',item.name],['所属客户',item.customer?.name],['产品',item.product],['阶段',labels(item.stage)],['金额',amount(item.amount)],['预计关单日期',date(item.expected_close_date)]]:
    kind==='follow-ups'?[['所属客户',item.customer?.name],['关联商机',item.opportunity?.name],['跟进日期',date(item.follow_up_date)],['类型',interactionModes[item.type]||item.type],['跟进内容',item.display_text]]:
    [['任务名称',item.title],['任务状态',taskState(item)],['截止日期',date(item.due_at)],...(item.execution_mode==='execution_v3'?[]:[['实际进度',progress(item.progress_percent)]]),['任务描述',item.description],['所属客户',item.customer?.name],['关联商机',item.opportunity?.name]];
  return entries.map(([label,value])=>({label,value:value===null||value===undefined||value===''?'暂无信息':String(value)}));
}
const lifecycle={
  onHide(){this._visible=false;this._loadSerial=(this._loadSerial||0)+1;},
  onUnload(){this.onHide();},
  openNavigation(event){const key=event.currentTarget.dataset.key;if(key==='home')return this.openHome();if(key==='account')return this.openAccount();return this.openModule({currentTarget:{dataset:{module:key}}});},
  openRecord(event){const {module,id}=event.currentTarget.dataset;if(!names[module]||!id)return;wx.navigateTo({url:'/pages/demo-view/detail/index?kind='+module+'&id='+encodeURIComponent(id)});},
  openModule(event){const module=event.currentTarget.dataset.module;if(!names[module])return;wx.redirectTo({url:'/pages/demo-view/list/index?kind='+module});},
  openHome(){wx.redirectTo({url:auth.HOME});},
  openAccount(){wx.redirectTo({url:'/pages/demo-view/account/index'});},
  openPrivacy(){if(typeof wx.openPrivacyContract==='function')wx.openPrivacyContract({fail:()=>wx.showToast({title:'隐私指引暂不可用',icon:'none'})});},
  async checked(load){
    if(typeof wx.hideShareMenu==='function')wx.hideShareMenu({menus:['shareAppMessage','shareTimeline']});
    const keepExisting=this._visible && this.data.ready;
    this._visible=true;const serial=this._loadSerial=(this._loadSerial||0)+1;
    if(!auth.active()){wx.reLaunch({url:'/pages/login/index'});return;}
    const identity=auth.snapshot();
    const current=()=>this._visible && serial===this._loadSerial && auth.isCurrent(identity);
    this.setData({loading:true,error:'',...(!keepExisting?{ready:false,items:[],record:null,rows:[]}: {})});
    try{
      const session=await auth.verify();if(!current())return;
      this.setData({companyName:session.companyName,account:session.account,modules:session.modules.filter(key=>names[key]).map(key=>({key,label:names[key],...moduleViews[key],icon:'/images/icons/'+moduleViews[key].icon+'-active.png'})),navItems:navigation(session.modules)});
      await load(current);
      if(current())this.setData({ready:true});
    }catch(error){if(current())this.setData({error:error.message||'暂时无法加载，请重试'});}
    finally{if(current())this.setData({loading:false});}
  }
};
module.exports={auth,names,pageTitles,navigation,present,fields,date,progress,lifecycle};
