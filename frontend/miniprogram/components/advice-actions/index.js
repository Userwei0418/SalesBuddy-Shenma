const api = require('../../utils/apiClient');
const access = require('../../utils/access');
Component({
  properties:{analysisId:String,suggestion:Object},
  data:{saving:false,canDecide:false,isFde:false},
  lifetimes:{attached(){this.refreshPermissions();},detached(){this._detached=true;}},
  pageLifetimes:{show(){this.refreshPermissions();if(this._returnFromTask){this._returnFromTask=false;this.triggerEvent('changed');}}},
  methods:{
    refreshPermissions(){const app=getApp(),session=(app.globalData||{}).session;this.setData({isFde:access.isFde(session&&session.role||(app.globalData||{}).role),canDecide:app.can?app.can('advice.decide'):access.can(session,'advice.decide')});},
    adopt(){
      const s=this.data.suggestion;
      this.refreshPermissions();if(!this.data.canDecide)return;
      if(!s || s.decision!=='pending' || this.data.saving)return;
      this._returnFromTask=true;
      wx.navigateTo({url:`/pages/management-task-create/index?adviceId=${encodeURIComponent(this.data.analysisId)}&suggestionId=${encodeURIComponent(s.id)}`});
    },
    dismiss(){
      const s=this.data.suggestion;
      this.refreshPermissions();if(!this.data.canDecide)return;
      if(!s || s.decision!=='pending' || this.data.saving)return;
      wx.showModal({title:this.data.isFde?'不采纳这条建议？':'这条建议无需待办？',content:'保存处理决定，不会创建任务。',editable:true,placeholderText:'可填写原因',confirmText:'确认',
        success:async result=>{
          if(!result.confirm || this._detached || this.data.saving)return;
          this.setData({saving:true});
          try{await api.decideSuggestion(s.id,{decision:'no_task',version_no:s.version_no,note:result.content||''});if(!this._detached)this.triggerEvent('changed');}
          catch(error){wx.showToast({title:error.message||'保存失败，请重试',icon:'none'});}
          finally{if(!this._detached)this.setData({saving:false});}
        }
      });
    },
    openTask(){const s=this.data.suggestion;if(s&&s.task_id)wx.navigateTo({url:`/pages/task-detail/index?id=${encodeURIComponent(s.task_id)}`});}
  }
});
