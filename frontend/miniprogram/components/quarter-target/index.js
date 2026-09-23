const api=require('../../utils/apiClient');
const access=require('../../utils/access');
const kinds=['collection','recognized'];
const money=value=>{const cents=value==null?null:centsFromDecimal(value,2);if(cents===null)return '未设置';const [integer,fraction]=decimalFromCents(cents,2).split('.');return '¥'+integer.replace(/\B(?=(\d{3})+(?!\d))/g,',')+(fraction?'.'+fraction:'');};
// Keep currency conversion in decimal digits: 1 yuan = 100 cents, 1 wan = 1,000,000 cents.
// Floating point division can add trailing digits to a valid six-decimal wan input.
function centsFromDecimal(value, scale) {
 const match=String(value).trim().match(new RegExp('^(\\d+)(?:\\.(\\d{1,'+scale+'}))?$'));
 if(!match)return null;
 const digits=(match[1]+(match[2]||'').padEnd(scale,'0')).replace(/^0+(?=\d)/,'');
 const cents=Number(digits);
 return Number.isSafeInteger(cents)&&cents>0?digits:null;
}
function decimalFromCents(digits,scale) {
 const padded=digits.padStart(scale+1,'0'),integer=padded.slice(0,-scale),fraction=padded.slice(-scale).replace(/0+$/,'');
 return integer+(fraction?'.'+fraction:'');
}
function amountInput(amount) {
 if(amount==null)return '';
 const cents=centsFromDecimal(amount,2);
 return cents===null?'':decimalFromCents(cents,6);
}
Component({
 properties:{scope:{type:String,value:'self'},subjectId:String,teamId:String,subjectLabel:String,year:Number,quarter:Number,editable:Boolean,contextKey:String},
 data:{open:false,loading:false,saving:false,error:'',notice:'',rows:[],pending:[],decision:null,canEdit:false,collection:'',recognized:'',reason:'',isChange:false,quarterLabel:''},
 observers:{'scope,subjectId,teamId,year,quarter,editable,contextKey':function(){this.reset();this.load();}},
 lifetimes:{attached(){this.active=true;this.load();},detached(){this.active=false;this.serial=(this.serial||0)+1;}},
 pageLifetimes:{hide(){this.active=false;this.reset();},show(){this.active=true;this.load();}},
 methods:{
  noop(){},
  key(){return JSON.stringify([access.identity(getApp().globalData.session),this.properties.scope,this.properties.subjectId,this.properties.teamId,this.properties.year,this.properties.quarter,this.properties.contextKey]);},
  reset(){this.serial=(this.serial||0)+1;this.setData({open:false,saving:false,error:'',rows:[],pending:[],decision:null,canEdit:false,notice:''});},
  query(){const p=this.properties;return {scope:p.scope,period_type:'quarter',anchor_date:p.year+'-'+String((p.quarter-1)*3+1).padStart(2,'0')+'-01',...(p.scope==='person'?{user_id:p.subjectId}:{}),...(p.scope==='team'?{team_id:p.teamId}:{})};},
  current(serial,key){return this.active!==false&&serial===this.serial&&key===this.key();},
  async load(){
   const p=this.properties;if(!p.year||!p.quarter||(p.scope==='person'&&!p.subjectId)||(p.scope==='team'&&!p.teamId))return;
   const serial=this.serial=(this.serial||0)+1,key=this.key();this.setData({loading:true,error:'',quarterLabel:p.year+' Q'+p.quarter});
   try{const result=await api.getTargets(this.query());if(!this.current(serial,key))return;
    if(!Array.isArray(result.items))throw Error('目标数据暂未加载完成');
    const pending=[...(result.pending_batches||[]).flatMap(batch=>(batch.items||[]).map(row=>({...row,reason:batch.reason||batch.request_reason||'',status:'pending'}))),...(result.pending_requests||[])].filter(row=>kinds.includes(row.kind));
    const rows=kinds.map(kind=>{const item=result.items.find(row=>row.kind===kind),change=pending.find(row=>row.kind===kind),amount=item?(item.amount_text??item.amount):null;return {kind,name:kind==='collection'?'回款':'确收',amount:item?Number(amount):null,inputAmount:item?amountInput(amount):'',version_no:item?item.version_no:null,currentText:money(amount),proposedText:change?money(change.proposed_amount):'',pending:!!change};});
    const own=p.scope==='self'||(p.scope==='person'&&p.subjectId===getApp().globalData.session.userId);
    const now=new Date(Date.now()+28800000),currentQuarter=now.getUTCFullYear()===p.year&&Math.floor(now.getUTCMonth()/3)+1===p.quarter;
    const latest=(result.recent_batches||[]).find(row=>['approved','rejected','cancelled'].includes(row.status));
    const decision=latest?{label:{approved:'已通过',rejected:'已驳回',cancelled:'已关闭'}[latest.status],status:latest.status,reason:latest.decision_reason||'',reviewer:latest.reviewer_name||'运营',date:String(latest.reviewed_at||latest.decided_at||latest.updated_at||'').slice(0,10)}:null;
    this.setData({loading:false,rows,pending,decision,canEdit:p.editable&&result.editable===true&&own&&currentQuarter,notice:pending.length?'目标变更待运营审批，完成率仍按当前生效值计算。':''});
   }catch(error){if(this.current(serial,key))this.setData({loading:false,error:error.message||'目标加载失败，请重试'});}
  },
  show(){if(!this.data.canEdit||this.data.loading||this.data.pending.length)return;this.formKey=this.key();const values={};this.data.rows.forEach(row=>{values[row.kind]=row.inputAmount||amountInput(row.amount);});this.setData({...values,open:true,reason:'',error:'',isChange:this.data.rows.some(row=>row.amount!=null)});},
  close(){if(!this.data.saving)this.setData({open:false,error:''});},
  input(e){const field=e.currentTarget.dataset.field;if([...kinds,'reason'].includes(field))this.setData({[field]:e.detail.value,error:''});},
  async save(){
   if(this.data.saving||!this.data.canEdit||!this.data.open||this.formKey!==this.key())return;
   const items=[];
   for(const kind of kinds){const cents=centsFromDecimal(this.data[kind]||'',6);if(cents===null){this.setData({error:'请填写有效的正数目标金额，最多保留 6 位小数（万元）'});return;}const old=this.data.rows.find(row=>row.kind===kind);items.push({kind,amount:decimalFromCents(cents,2),version_no:old?old.version_no:null});}
   const reason=String(this.data.reason||'').trim();if(!reason){this.setData({error:'请填写与负责人商定的依据或本次调整原因'});return;}
   const serial=this.serial,key=this.key();this.setData({saving:true,error:''});
   try{const result=await api.saveTargetBatch({...this.query(),items,reason});if(!this.current(serial,key))return;this.setData({saving:false,open:false});wx.showToast({title:result.status==='pending'?'已提交运营审批':result.status==='unchanged'?'目标未发生变化':'季度目标已生效',icon:'success'});await this.load();this.triggerEvent('saved',{status:result.status});}
   catch(error){if(this.current(serial,key))this.setData({saving:false,error:error.message||'提交失败，请重试'});}
  }
 }
});
