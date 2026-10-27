const presentation=require('../../utils/taskExecutionPresentation');
const feedback=require('../../utils/taskFeedback');
// Local component props/events, not a wire response. The page adapter must supply
// authenticated execution capabilities only after the backend READY contract.
Component({
 options:{addGlobalClass:true},
 properties:{enabled:Boolean,contextKey:String,taskVersions:Object,progressValue:Object,protocolVersion:Number,currentUserId:String,
  checklist:{type:Array,value:[]},feedback:{type:Array,value:[]},pauseView:Object,
  executorLabel:String,reviewerLabel:String,lastSyncedLabel:String,offline:Boolean,busy:Boolean,
  allowFeedback:Boolean,allowProgress:Boolean,allowPause:Boolean,allowResume:Boolean},
 data:{progressView:{available:false,percent:null,label:'进度暂不可用'},note:'',nextStep:'',blocker:'',evidenceUrls:'',feedbackKind:'progress',selectedItemId:'',replyTo:null,corrects:null,reference:'',itemOptions:[],feedbackKinds:['执行进展','评论 / 协作说明'],error:'',stale:false,pending:false,showAllFeedback:false},
 observers:{
  'checklist':function(items){this.setData({itemOptions:[{item_id:'',title:'不关联推进项'},...(items||[])]});},
  'progressValue':function(value){this.setData({progressView:presentation.progress(value)});},
  'contextKey':function(){
   // Polling can notify observers even when the identity value is unchanged.
   const key=this.properties.contextKey;if(this._observedContextKey===key)return;
   this._observedContextKey=key;this._draftContext=null;this._pendingContext=null;
   this.setData({note:'',nextStep:'',blocker:'',evidenceUrls:'',feedbackKind:'progress',selectedItemId:'',replyTo:null,corrects:null,reference:'',error:'',stale:false,pending:false,showAllFeedback:false});
  },
  'taskVersions':function(){if(this._draftContext && this._draftContext!==this.context()){this._pendingContext=null;this.setData({pending:false,stale:true,error:'任务已更新，请核对后再提交；反馈草稿已保留'});}},
 },
 methods:{
  context(){try{return JSON.stringify([this.properties.contextKey,presentation.versions(this.properties.taskVersions)]);}catch(_){return null;}},
  inputFeedback(e){if(this.data.pending||this.properties.busy)return;const context=this.context();if(!this._draftContext)this._draftContext=context;const key=e.currentTarget&&e.currentTarget.dataset.key||'note';if(['note','nextStep','blocker','evidenceUrls'].includes(key))this.setData({[key]:e.detail.value,error:''});},
  chooseFeedbackKind(e){if(this.data.pending||this.properties.busy)return;this.captureDraft();this.setData({feedbackKind:Number(e.detail.value)===1?'comment':'progress'});},
  chooseFeedbackItem(e){if(this.data.pending||this.properties.busy)return;this.captureDraft();this.setData({selectedItemId:(this.data.itemOptions[Number(e.detail.value)]||{}).item_id||''});},
  captureDraft(){if(!this._draftContext)this._draftContext=this.context();},
  referenceFeedback(e){if(this.data.pending||this.properties.busy)return;const row=this.properties.feedback.find(item=>item.id===e.currentTarget.dataset.id),correct=e.currentTarget.dataset.action==='correct';if(!row||correct&&row.actorUserId!==this.properties.currentUserId)return;this.captureDraft();this.setData({replyTo:correct?null:row.id,corrects:correct?row.id:null,reference:row.text,...(correct&&!this.data.note?{note:row.text,nextStep:row.nextStep||'',blocker:row.blocker||'',evidenceUrls:(row.evidenceUrls||[]).join('\n'),selectedItemId:row.itemId||'',feedbackKind:row.kind||'progress'}:{})});},
  clearFeedbackReference(){if(!this.data.pending&&!this.properties.busy)this.setData({replyTo:null,corrects:null,reference:''});},
  copyEvidence(e){const row=this.properties.feedback.find(item=>item.id===e.currentTarget.dataset.id),url=row&&(row.evidenceUrls||[])[Number(e.currentTarget.dataset.index)];if(url&&/^https?:\/\//i.test(url))wx.setClipboardData({data:url});},
  refresh(){this.triggerEvent('refresh');},
  confirmLatest(){if(this.properties.busy||this.properties.offline)return;const context=this.context();if(!context)return;this._draftContext=context;this.setData({stale:false,error:''});},
  submitFeedback(){
   if(!this.properties.enabled||!this.properties.contextKey||this.properties.offline||this.properties.busy||this.data.pending||!this.properties.allowFeedback)return;
   const context=this.context(),text=String(this.data.note||'').trim();
   if(!context){this.setData({error:'任务版本信息不完整，请刷新'});return;}
   if(this.data.stale||(this._draftContext&&this._draftContext!==context)){this.setData({stale:true,error:'任务已更新，请核对后再提交；反馈草稿已保留'});return;}
   let command;try{command=feedback.prepare({text,next_step:this.data.nextStep,blocker:this.data.blocker,evidence_urls:this.data.evidenceUrls,feedback_kind:this.data.feedbackKind,item_id:this.data.selectedItemId,reply_to_event_id:this.data.replyTo,corrects_event_id:this.data.corrects},presentation.versions(this.properties.taskVersions));}catch(error){this.setData({error:error.message});return;}
   this._draftContext=context;this._feedbackSequence=(this._feedbackSequence||0)+1;
   this._pendingContext=JSON.stringify([context,this._feedbackSequence]);this.setData({pending:true,error:''});
   this.triggerEvent('feedbackintent',{contextKey:this.properties.contextKey,context:this._pendingContext,
    command});
  },
  // Parent explicitly acknowledges its result; stale responses never clear another draft.
  settleFeedback({context,success,error}={}){
   if(!this._pendingContext||context!==this._pendingContext||this._draftContext!==this.context())return;
   this._pendingContext=null;this.setData({pending:false,error:success?'':error||'反馈未保存，请重试'});
   if(success){this._draftContext=null;this.setData({note:'',nextStep:'',blocker:'',evidenceUrls:'',feedbackKind:'progress',selectedItemId:'',replyTo:null,corrects:null,reference:'',stale:false});}
  },
  requestAction(e){
   const action=e.currentTarget.dataset.action,permission={progress:'allowProgress',pause:'allowPause',resume:'allowResume'}[action];
   if(!permission||!this.properties.enabled||!this.properties.contextKey||this.properties.offline||this.properties.busy||this.data.pending||!this.properties[permission])return;
   if(action==='progress'&&!this.properties.checklist.some(item=>item.id===e.currentTarget.dataset.id&&item.editable))return;
   if(action==='resume'&&!(this.properties.pauseView&&this.properties.pauseView.active))return;
   if(action==='pause'&&this.properties.pauseView&&this.properties.pauseView.active)return;
   const context=this.context();if(!context){this.setData({error:'任务版本信息不完整，请刷新'});return;}
   this.triggerEvent('actionintent',{action,itemId:e.currentTarget.dataset.id||null,contextKey:this.properties.contextKey,context,
    versions:presentation.versions(this.properties.taskVersions)});
  },
  toggleFeedback(){this.setData({showAllFeedback:!this.data.showAllFeedback});},
 },
});
