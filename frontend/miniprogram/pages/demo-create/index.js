const {beijingTime}=require('../../utils/fdePresentation');
const api=require('../../utils/apiClient');
const access=require('../../utils/access');
Page({
  data:{viewing:false,editing:false,saving:false,voiceId:null,voiceState:"",eligible:false,accessMessage:'正在加载关联商机…',opportunity:null,demoScenes:[{id:1,name:'',description:''}],demoError:''},
  async onLoad(options={}){
    if(getApp().guardPage&&!getApp().guardPage(this,'demo-create',options))return;
    if(!getApp().ensureLogin())return;
    const session=getApp().globalData.session;
    this.owner=access.identity(session);
    try{
      options={...options,demo_id:decodeURIComponent(options.demo_id||''),customer_id:decodeURIComponent(options.customer_id||''),opportunity_id:decodeURIComponent(options.opportunity_id||'')};
    }catch(error){this.setData({accessMessage:'场景链接无效，请返回商机重新进入'});return;}
    this.setData({viewing:options.view==='1'});this.editId=options.demo_id||'';this.setData({editing:!!this.editId});if(this.editId)wx.setNavigationBarTitle({title:this.data.viewing?'Demo 场景详情':'编辑 Demo 场景'});
    this.customerId=options.customer_id;this.opportunityId=options.opportunity_id;
    if(!this.customerId||!this.opportunityId){this.setData({accessMessage:'请从关联商机进入'});return;}
    try{
      const header=await api.getOpportunityDetailHeader(this.opportunityId);
      if(this.closed||this.owner!==access.identity(getApp().globalData.session))return;
      if(!header||String(header.id)!==String(this.customerId))throw Error('商机不属于当前客户');
      const opportunity=(header.opportunities||[]).find(row=>String(row.id)===String(this.opportunityId)&&String(row.customer_id)===String(this.customerId));
      if(!opportunity)throw Error('商机不存在或不在当前权限范围');
      if(opportunity&&this.editId){
        const scene=await api.getDemoScene(this.editId);
        if(this.closed||this.owner!==access.identity(getApp().globalData.session))return;
        if(!scene)throw Error('该场景已不存在，请返回刷新');
        if(String(scene.id)!==String(this.editId)||String(scene.opportunity_id)!==String(this.opportunityId))throw Error('场景不属于当前商机');
        this.setData({viewing:this.data.viewing||scene.can_edit!==true,sceneDetail:{...scene,creatorName:scene.creator_name,createdLabel:beijingTime(scene.created_at)},demoScenes:[{id:1,name:scene.name,description:scene.description}]});
        if(this.data.viewing)wx.setNavigationBarTitle({title:'Demo 场景详情'});
      }
      if(!this.editId){
        const permission=await api.listDemoScenes(this.opportunityId,{limit:1,offset:0});
        if(this.closed||this.owner!==access.identity(getApp().globalData.session))return;
        this.canCreate=access.can(session,'demo_scene.create')&&permission&&permission.editable===true;
        if(!this.canCreate)throw Error('当前账号不可创建 Demo 场景');
      }
      this.setData({eligible:true,opportunity,accessMessage:''});
    }catch(error){if(!this.closed)this.setData({accessMessage:error.message||'商机加载失败，请返回重试'});}
  },
  canWriteScene(allowViewing=false){return !this.closed&&this.data.eligible&&(allowViewing||!this.data.viewing)&&this.owner===access.identity(getApp().globalData.session)&&(this.editId?!!this.data.sceneDetail&&this.data.sceneDetail.can_edit===true&&(!getApp().globalData.session.permissions||access.can(getApp().globalData.session,'demo_scene.update')):this.canCreate===true);},
  startEditing(){if(!this.editId||this.data.saving||!this.canWriteScene(true))return;this.setData({viewing:false});wx.setNavigationBarTitle({title:'编辑 Demo 场景'});},
  canDeleteScene(){return !this.closed&&this.owner===access.identity(getApp().globalData.session)&&access.can(getApp().globalData.session,'demo_scene.delete')&&this.data.sceneDetail&&this.data.sceneDetail.can_delete===true;},
  deleteScene(){
    if(!this.editId||this.data.saving||!this.canDeleteScene())return;
    wx.showModal({title:'删除 Demo 场景',content:'确定删除该场景？删除后无法恢复。',confirmText:'删除',confirmColor:'#d75555',success:async r=>{
      if(!r.confirm||this.data.saving||!this.canDeleteScene())return;
      try{this.setData({saving:true});await api.deleteDemoScene(this.editId,this.data.sceneDetail.version_no);}
      catch(error){this.setData({saving:false});wx.showToast({title:error.message||'删除失败',icon:'none'});return;}
      const channel=this.getOpenerEventChannel();if(channel&&channel.emit)channel.emit('demoSaved');wx.navigateBack();
    }});
  },
  onShow(){this.hidden=false;},
  onHide(){this.hidden=true;this.cancelVoice();},
  onUnload(){this.closed=true;this.cancelVoice();if(this.recorder){this.recorder.offStart(this.voiceStart);this.recorder.offStop(this.voiceStop);this.recorder.offError(this.voiceError);}},
  cancelVoice(){this.voiceSerial=(this.voiceSerial||0)+1;if(this.recorder && ['recording','starting'].includes(this.data.voiceState))this.recorder.stop();this.setData({voiceId:null,voiceState:''});},
  async toggleVoice(e){
    if(!this.canWriteScene())return;
    const session=getApp().globalData.session;if(session.permissions&&!access.can(session,'visit.transcribe'))return;
    const id=Number(e.currentTarget.dataset.id);
    if(this.data.voiceState==='recording' && this.data.voiceId===id){this.setData({voiceState:'stopping'});this.recorder.stop();return;}
    if(this.data.voiceState)return;
    const serial=this.voiceSerial=(this.voiceSerial||0)+1;
    const current=()=>!this.closed&&!this.hidden&&serial===this.voiceSerial&&this.owner===access.identity(getApp().globalData.session);
    this.setData({voiceId:id,voiceState:'starting',demoError:''});
    if(!this.recorder){
      this.recorder=wx.getRecorderManager();
      this.voiceStart=()=>{if(this.closed||this.hidden){this.recorder.stop();return;}this.setData({voiceState:'recording'});};
      this.voiceError=()=>{if(!this.closed)this.setData({voiceId:null,voiceState:'',demoError:'录音失败，请检查麦克风权限后重试'});};
      this.voiceStop=async result=>{
        const recordingSerial=this.voiceSerial,target=this.data.voiceId;
        const valid=()=>!this.closed&&!this.hidden&&recordingSerial===this.voiceSerial&&this.owner===access.identity(getApp().globalData.session);
        if(!valid()||target===null)return;
        if(!result.tempFilePath||result.duration<800){this.setData({voiceId:null,voiceState:'',demoError:'录音太短，请重新录入'});return;}
        this.setData({voiceState:'transcribing'});
        try{
          const resultText=await api.transcribeAudio(result.tempFilePath,'demo_scene');
          if(!valid())return;
          const text=String(resultText.text||'').trim();
          if(!text)throw Error('未识别到文字，请重新录入');
          const scene=this.data.demoScenes.find(row=>row.id===target);
          if(!scene)return;
          const description=scene.description+(scene.description?'\n':'')+text;
          if(description.length>2000)throw Error('转写后超过2000字，请缩短已有描述后重新录入');
          this.setData({demoScenes:this.data.demoScenes.map(row=>row.id===target?{...row,description}:row)});
        }catch(error){if(valid())this.setData({demoError:error.message||'语音识别失败，请重试'});}
        finally{if(valid())this.setData({voiceId:null,voiceState:''});}
      };
      this.recorder.onStart(this.voiceStart);this.recorder.onStop(this.voiceStop);this.recorder.onError(this.voiceError);
    }
    wx.authorize({scope:'scope.record',success:()=>{if(current())this.recorder.start({duration:600000,sampleRate:16000,numberOfChannels:1,encodeBitRate:48000,format:'mp3'});},fail:()=>{if(current()){this.setData({voiceId:null,voiceState:'',demoError:'请允许麦克风权限后重试'});wx.showModal({title:'需要麦克风权限',content:'请在设置中允许录音权限，用于转写场景描述。',confirmText:'去设置',success:r=>{if(r.confirm&&current())wx.openSetting({});}});}}});
  },
  addDemoScene(){if(!this.canWriteScene()||this.data.editing||this.data.demoScenes.length>=20)return;this.nextId=(this.nextId||1)+1;this.setData({demoScenes:this.data.demoScenes.concat({id:this.nextId,name:'',description:''}),demoError:''});},
  removeDemoScene(e){if(!this.canWriteScene())return;if(this.data.voiceId===Number(e.currentTarget.dataset.id))this.cancelVoice();if(this.data.demoScenes.length<=1)return;this.setData({demoScenes:this.data.demoScenes.filter(row=>String(row.id)!==String(e.currentTarget.dataset.id))});},
  editDemoScene(e){if(!this.canWriteScene())return;const field=e.currentTarget.dataset.field;if(!['name','description'].includes(field))return;const index=this.data.demoScenes.findIndex(row=>String(row.id)===String(e.currentTarget.dataset.id));if(index<0)return;this.setData({['demoScenes['+index+'].'+field]:e.detail.value,demoError:''});},
  async submitDemoScenes(){
    if(this.data.saving||this.data.voiceState||!this.canWriteScene())return;
    if(this.data.demoScenes.some(row=>!row.name.trim()||!row.description.trim())){this.setData({demoError:'请填写每个场景的名称和描述'});return;}
    this.setData({saving:true,demoError:''});
    try{
      if(this.editId)await api.updateDemoScene(this.editId,{name:this.data.demoScenes[0].name,description:this.data.demoScenes[0].description,version_no:this.data.sceneDetail.version_no});
      else await api.createDemoScenes(this.opportunityId,this.data.demoScenes.map(({name,description})=>({name,description})));
      if(this.closed||this.owner!==access.identity(getApp().globalData.session))return;
    }catch(error){this.setData({saving:false,demoError:error.message||'保存失败，请重试'});return;}
    const channel=this.getOpenerEventChannel();if(channel&&channel.emit)channel.emit('demoSaved');
    wx.showToast({title:'场景已登记',icon:'success'});
    wx.navigateBack({fail:()=>{this.setData({demoError:'已保存，可返回商机查看 Demo 场景。'});}});
  }
});
