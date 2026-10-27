// RISK001: advice decisions do not close risks. Both writes require current object authority and version.
const uiColors = require('../../utils/uiColors');
const apiClient = require('../../utils/apiClient');
const access = require('../../utils/access');
const {normalizeRisk,canWrite,versionValid} = require('../../utils/riskDisplay');
const writes = require('../../utils/pageWriteContext').createPageWriteContext(() => getApp().globalData.session);

Page({
  data:{riskId:'',risk:null,resolutionNote:'',noteCount:0,submitting:false,loading:false,loadError:'',
    decisionChoice:'',decisionNote:'',decisionSubmitting:false,decisionError:'',refreshRequired:false},
  onLoad(options={}) {
    if(getApp().guardPage && !getApp().guardPage(this,'risk-detail',options))return;
    this.setData({riskId:decodeURIComponent(options.id || '')});
  },
  onShow() {
    this._hidden=false;this.writeHidden=false;
    if(getApp().guardPage && !getApp().guardPage(this,'risk-detail'))return;
    if(!getApp().ensureLogin())return;
    return this.loadRisk();
  },
  busy(){return Boolean(this.pageWrites && Object.values(this.pageWrites).some(operation=>operation.current()));},
  async loadRisk(options={}) {
    clearTimeout(this._backTimer);
    const serial=this._loadSerial=(this._loadSerial || 0)+1, id=this.data.riskId;
    const identity=access.identity(getApp().globalData.session);
    const current=()=>!this._hidden && serial===this._loadSerial && id===this.data.riskId && identity===access.identity(getApp().globalData.session);
    if(this._detailIdentity && (this._detailIdentity!==identity || this._detailTarget!==id)) {
      this.setData(access.readStateReset('risk-detail'));
      this._decisionIntent=null;this._newDecisionIntent=false;
    }
    this._detailIdentity=identity;this._detailTarget=id;this._loadedIdentity=null;
    if(!/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(id)) {this.showMissing();return false;}
    this.setData({loading:true,loadError:'',risk:null,
      submitting:Boolean(this.pageWrites && this.pageWrites.resolve && this.pageWrites.resolve.current()),
      decisionSubmitting:Boolean(this.pageWrites && this.pageWrites.decision && this.pageWrites.decision.current())});
    try {
      const risk=await apiClient.getRisk(id,{fresh:options.explicit === true});
      if(!current())return false;
      if(!risk || risk.id!==id)throw Error('风险详情与当前记录不一致');
      if(options.explicit && !versionValid(risk))throw Error('风险版本缺失，请更新配套后端后重试');
      this._loadedIdentity=identity;
      this.setData({risk:normalizeRisk(risk,getApp().globalData.session)});
      if(options.explicit) {
        this._decisionIntent=null;this._newDecisionIntent=true;
        this.setData({refreshRequired:false,decisionError:''});
      }
      return true;
    } catch(error) {
      if(current()) {
        this.setData({loadError:error.message || '风险读取失败，请重试'});
        if(error.statusCode===404)this.showMissing();
      }
      return false;
    } finally {if(current())this.setData({loading:false});}
  },
  refreshRisk(){if(this.busy() || this.data.loading)return;return this.loadRisk({explicit:true});},
  onHide(){this._hidden=true;this.writeHidden=true;this._loadSerial=(this._loadSerial || 0)+1;clearTimeout(this._backTimer);},
  onUnload(){this.unloaded=true;this.onHide();},
  backSoon(){
    clearTimeout(this._backTimer);
    const serial=this._loadSerial,id=this.data.riskId,identity=access.identity(getApp().globalData.session);
    this._backTimer=setTimeout(()=>{if(!this._hidden && serial===this._loadSerial && id===this.data.riskId && identity===access.identity(getApp().globalData.session))wx.navigateBack();},700);
  },
  showMissing(){wx.showToast({title:'风险不存在或已不可见',icon:'none'});this.backSoon();},
  canSubmit(operation){
    return !this._hidden && !this.unloaded && !this.data.loading && !this.data.refreshRequired && !this.busy() &&
      this._loadedIdentity===access.identity(getApp().globalData.session) && this.data.risk && this.data.risk.id===this.data.riskId &&
      canWrite(getApp().globalData.session,this.data.risk,operation);
  },
  inputResolutionNote(e){if(this.busy())return;const resolutionNote=String(e.detail.value || '');this.setData({resolutionNote,noteCount:resolutionNote.trim().length});},
  chooseDecision(e){
    if(this.busy() || !['adopt','defer'].includes(e.detail.value))return;
    if(e.detail.value!==this.data.decisionChoice){this._newDecisionIntent=true;this._decisionIntent=null;}
    this.setData({decisionChoice:e.detail.value});
  },
  chooseDecisionOption(e){return this.chooseDecision({detail:{value:e.currentTarget.dataset.value}});},
  inputDecisionNote(e){
    if(this.busy())return;
    const note=String(e.detail.value || '');
    if(note!==this.data.decisionNote){this._newDecisionIntent=true;this._decisionIntent=null;}
    this.setData({decisionNote:note});
  },
  async confirmDecision(){
    if(!this.canSubmit('decide'))return;
    const risk=this.data.risk,note=this.data.decisionNote.trim(),decision=this.data.decisionChoice;
    if(!['adopt','defer'].includes(decision) || !note || note.length>2000){this.setData({decisionError:'请选择是否采纳并填写1–2000字说明'});return;}
    const write=writes.begin(this,'decision',()=>this.data.riskId);if(!write)return;
    const serial=this._loadSerial;
    const current=()=>write.visible() && serial===this._loadSerial;
    const payload=this._decisionIntent || {decision,note,version_no:risk.version_no};
    this._decisionIntent=payload;
    const freshIntent=this._newDecisionIntent===true;this._newDecisionIntent=false;
    this.setData({decisionSubmitting:true,decisionError:''});
    let applied=false,conflict=false;
    try {
      const response=await apiClient.decideRisk(risk.id,payload,{freshIntent});
      if(!current())return;
      if(!response || !response.risk || response.risk.id!==risk.id || !versionValid(response.risk))throw Error('确认结果不完整，请刷新风险核对');
      applied=true;this._decisionIntent=null;
      this.setData({risk:normalizeRisk(response.risk,getApp().globalData.session),decisionChoice:'',decisionNote:''});
      wx.showToast({title:'建议确认已记录',icon:'success'});
    } catch(error) {
      if(!write.current())return;
      conflict=error.statusCode===409;
      if(conflict)this.setData({refreshRequired:true,decisionError:'风险已更新或提交内容冲突，请刷新后重新确认'});
      else if(current())this.setData({decisionError:error.message || '提交未确认，可使用相同内容重试'});
    } finally {
      write.finish('decisionSubmitting');
      // A visible return after a late write reads the result; conflict remains locked until explicit refresh.
      if(!applied && !conflict && write.settledVisible() && serial!==this._loadSerial)this.loadRisk();
    }
  },
  confirmResolve(){
    if(!this.canSubmit('resolve'))return;
    const risk=this.data.risk,note=String(this.data.resolutionNote || '').trim();
    if(note.length<5){wx.showToast({title:'请填写风险解除依据',icon:'none'});return;}
    const serial=this._loadSerial,identity=access.identity(getApp().globalData.session);
    const current=()=>!this._hidden && serial===this._loadSerial && risk===this.data.risk && identity===access.identity(getApp().globalData.session);
    wx.showModal({title:'确认解除该风险？',content:'解除后将保留处理人、处理时间和解除依据，经营摘要会同步更新。',confirmText:'确认解除',confirmColor:uiColors.success,
      success:result=>{
        if(!result.confirm || !current() || !this.canSubmit('resolve'))return;
        const write=writes.begin(this,'resolve',()=>this.data.riskId);if(!write)return;
        let applied=false,conflict=false;
        this.setData({submitting:true});
        apiClient.resolveRisk(risk.id,note,risk.version_no).then(resolved=>{
          if(!current())return;
          if(!resolved || resolved.id!==risk.id || resolved.status!=='resolved' || !versionValid(resolved))throw Error('解除结果不完整，请刷新核对');
          applied=true;this.setData({risk:normalizeRisk(resolved,getApp().globalData.session)});
          wx.setStorageSync('lastResolvedRiskId',risk.id);wx.showToast({title:'风险已解除',icon:'success'});wx.vibrateShort({type:'light'});this.backSoon();
        }).catch(error=>{
          if(!write.current())return;
          conflict=error.statusCode===409;
          if(conflict)this.setData({refreshRequired:true,decisionError:'风险已更新，请刷新后核对再解除'});
          if(current())wx.showToast({title:error.message || '解除未确认，正在重新读取状态',icon:'none'});
        }).finally(()=>{
          write.finish('submitting');
          if(!applied && !conflict && write.settledVisible())this.loadRisk();
        });
      }});
  },
});
