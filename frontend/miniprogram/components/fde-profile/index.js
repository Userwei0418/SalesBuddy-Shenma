const api = require('../../utils/apiClient');
const access = require('../../utils/access');
const present = require('../../utils/fdePresentation');
const salesMetrics = require('../../utils/profileMetrics');
const radar = require('../../utils/fdeRadar');
const {visitDetailUrl} = require('../../utils/visitNavigation');

Component({
  observers: {'chartsHidden': function(hidden) { if (!hidden) { this.drawRadar(); this.selectHistory(); } }},
  properties: {chartsHidden:Boolean,identityKey:{type:String,value:"",observer(){if(this.visible){this.load();this.loadMetrics();}}},selectedTeamId:{type:String,value:'',observer(){if(this.visible){this.load();this.loadMetrics();}}},selectedMemberId:{type:String,value:'',observer(){if(this.visible){this.load();this.loadMetrics();}}},selectedMemberName:String, profileScope:{type:String,value:'self',observer(){if(this.visible){this.load();this.loadMetrics();}}}, section: {type:String,value:"profile",observer(value) { this.setData({activeTab:value}); if(value==="profile") {this.drawRadar();this.selectHistory();} }}, embedded: Boolean, userName: String, roleName: String, team: String, account: String, initial: String, logoutBusy: Boolean, logoutNavigating: Boolean},
  data: {
    canReview:false,history:[],historyCode:'',historyName:'',historyPoints:[],canEditOwnTargets:false,targetNotice:'',metricsLoading:false, metricsError:'', projectMetrics:[], performanceBoard:[], maturityFacts:[], collaborationMetrics:[], stages:[], rhythm:[], rhythmSource:[], rhythmMode:'day', metricYear:0,metricQuarter:0,targetContextKey:"",
    activeTab: 'profile', tabs: [{key: 'profile', label: '工作画像'}, {key: 'tasks', label: '任务执行'}, {key: 'records', label: '跟进记录'}],
    portraitScore:'—', portraitScoreLabel:'待评估 · 总分 / 100', loading: false, ready: false, error: '', dimensions: [], sampleCount: 0, summary: '', updatedText: '', periodText: '近30天',
    reviewStatus: 'missing', reviewBusy: false, reviewError: '', reviewMessage: '', advice: [], pollPaused: false,
    tasks: [], taskSummary: null, tasksLoading: false, tasksError: '', taskAdvice: [], recordAdvice: [],
    records: [], recordsReady: false, recordsLoading: false, recordsError: '', recordsMore: false, recordsOffset: 0, recordsTotal: 0,
  },
  lifetimes: {
    attached() { this.visible = true; this.load(); if(this.properties.embedded) this.loadMetrics(); },
    detached() { this.closed = true; this.pause(); },
  },
  pageLifetimes: {
    show() { this.visible = true; this.load(); if(this.properties.embedded) this.loadMetrics(); },
    hide() { this.pause(); },
  },
  methods: {
    pause() { this.visible = false; clearTimeout(this.reviewTimer); this.serial = (this.serial || 0) + 1; this.recordSerial = (this.recordSerial || 0) + 1; this.taskSerial = (this.taskSerial || 0) + 1; this.metricSerial = (this.metricSerial || 0) + 1; this.setData({tasksLoading:false}); },
    profileParams(){return {days:30,team_id:this.properties.profileScope==='team'?this.properties.selectedTeamId||undefined:undefined,scope:this.properties.profileScope==='team'?'team':'self',member_id:this.properties.profileScope==='team'?undefined:this.properties.selectedMemberId||undefined};},
    activityParams(){return {team_id:this.properties.profileScope==='team'?this.properties.selectedTeamId||undefined:undefined,scope:this.properties.profileScope==='team'||this.properties.selectedMemberId?'team':'self',member_id:this.properties.profileScope==='team'?undefined:this.properties.selectedMemberId||undefined};},
    valid(serial, identity) { return !this.closed && this.visible !== false && serial === this.serial && identity === access.identity(getApp().globalData.session); },
    async load(polling = false) {
      if(this.properties.profileScope==='team'&&!this.properties.selectedTeamId){this.setData({loading:false,ready:false,error:'请选择要查看的团队'});return;}
      clearTimeout(this.reviewTimer);
      const identity = access.identity(getApp().globalData.session), serial = this.serial = (this.serial || 0) + 1;
      if (polling !== true) {
        this.pollCount = 0;
        this.recordSerial = (this.recordSerial || 0) + 1;
        this.setData({canReview:false,portraitScore:"—",portraitScoreLabel:"待评估 · 总分 / 100",history:[],historyPoints:[],summary:"",updatedText:"",loading: true, ready: false, error: '', dimensions: [], advice: [], taskAdvice: [], recordAdvice: [], reviewError: '', pollPaused: false, records: [], recordsReady: false, recordsLoading: false, recordsOffset: 0});
        if (this.data.activeTab === 'records') this.loadRecords();
        if (this.data.activeTab === 'tasks') this.loadTasks();
      }
      try {
        const response = await api.getFdeProfile(this.profileParams());
        if (!this.valid(serial, identity)) return;
        const definitions = (response.framework || {}).dimensions, latest = response.latest;
        if (response.data_source !== 'database' || !Array.isArray(definitions) || definitions.length !== 6 || !latest || !Array.isArray(latest.dimensions)) throw Error('画像暂未加载完成，请重试');
        const dimensions = definitions.map(definition => {
          const fact = latest.dimensions.find(item => item.code === definition.code) || {}, score = present.numeric(fact.score);
          return {...definition, shortName: definition.short_name || definition.name, score, scoreText: score === null ? '未评估' : String(Math.round(score * 10) / 10), width: score === null ? 0 : Math.max(0, Math.min(100, score)), assessment: fact.assessment || '暂无可评估记录', evidenceCount: present.numeric(fact.evidence_count)};
        });
        const serverScore=present.numeric(latest.overall_score);
        const validScore=value=>value!==null && value>=0 && value<=100;
        const score=validScore(serverScore)?serverScore:null;
        this.setData({portraitScore:score===null?'—':String(Math.round(score*10)/10),portraitScoreLabel:score===null?'待评估 · 总分 / 100':'画像得分 / 100'});
        const status = response.review_status || 'missing', reviewBusy = ['queued', 'running'].includes(status);
        const advice = status === 'succeeded' && Array.isArray(latest.advice) ? latest.advice.map((item, index) => typeof item === 'string' ? {title: '建议 ' + (index + 1), content: item} : {title: item.title || '建议 ' + (index + 1), content: item.content || item.action || ''}).filter(item => item.content) : [];
        this.setData({canReview:response.can_review!==false&&this.ownTargetScope(),history:response.history||[],loading: false, ready: true, error: '', dimensions, sampleCount: present.numeric(response.sample_count) || 0, summary: latest.summary || '', updatedText: present.beijingTime(response.as_of), reviewStatus: status, reviewBusy, advice, taskAdvice: advice.filter(item => /任务|待办|交付|截止|逾期|优先/.test(item.title + item.content)), recordAdvice: advice.filter(item => /拜访|跟进|沟通|记录|验证|材料/.test(item.title + item.content)),
          reviewMessage: reviewBusy ? '正在整理近期协作建议…' : status === 'failed' ? '本次建议未生成，请稍后重试' : status === 'empty' ? '当前暂无可用于建议的拜访资料或待办' : status === 'succeeded' ? (advice.length ? '建议已根据近期工作记录生成' : '已分析，当前没有需要额外补充的协作建议') : '根据近期拜访与任务，生成个人协作建议'});
        if (this.data.activeTab === 'profile') {this.drawRadar();this.selectHistory();}
        if (this.data.activeTab === 'records' && !this.data.recordsReady && !this.data.recordsLoading) this.loadRecords();
        if (reviewBusy) this.schedulePoll();
      } catch (error) {
        if (this.valid(serial, identity)) this.setData({loading: false, ready: false, error: error.message || '画像加载失败，请重试', advice: [], dimensions: [], reviewBusy: false});
      }
    },
    schedulePoll() {
      if (this.closed || this.visible === false) return;
      this.pollCount = (this.pollCount || 0) + 1;
      if (this.pollCount > 40) { this.setData({pollPaused: true, reviewMessage: '仍在后台生成，可稍后刷新查看'}); return; }
      this.reviewTimer = setTimeout(() => this.load(true), 1500);
    },
    async generateAdvice() {
      if (!this.data.canReview || this.data.reviewBusy || !this.data.ready || this.data.reviewStatus === 'empty') return;
      const identity = access.identity(getApp().globalData.session), serial = this.serial;
      this.setData({reviewBusy: true, reviewStatus: 'queued', advice: [], taskAdvice: [], recordAdvice: [], summary: '', reviewError: '', reviewMessage: '正在提交复盘…'});
      try {
        await api.reviewFdeProfile(this.profileParams());
        if (!this.valid(serial, identity)) return;
        this.pollCount = 0;
        await this.load(true);
      } catch (error) { if (this.valid(serial, identity)) this.setData({reviewBusy: false, reviewStatus: 'failed', reviewError: error.message || '建议生成失败，请重试'}); }
    },
    tab(e) { const activeTab = e.currentTarget.dataset.tab; if (!this.data.tabs.some(item => item.key === activeTab)) return; this.setData({activeTab}); if (activeTab === 'profile') this.drawRadar(); if (activeTab === 'tasks') this.loadTasks(); if (activeTab === 'records' && !this.data.recordsReady) this.loadRecords(); },
    drawRadar() {
      if (this.properties.chartsHidden) return;
      if (!this.data.ready || this.data.dimensions.length !== 6 || this.data.activeTab !== 'profile' || typeof this.createSelectorQuery !== 'function') return;
      const draw = () => this.createSelectorQuery().select('.radar-canvas').boundingClientRect(rect => {
        if (!rect || this.closed || this.visible === false || this.properties.chartsHidden || this.data.activeTab !== 'profile') return;
        radar.drawRadar(wx.createCanvasContext('fdeAbilityRadar', this), rect.width, rect.height, this.data.dimensions);
      }).exec();
      if (wx.nextTick) wx.nextTick(draw); else draw();
    },
    async loadRecords() {
      if (this.data.recordsLoading) return;
      const identity = access.identity(getApp().globalData.session), serial = this.recordSerial = (this.recordSerial || 0) + 1;
      this.setData({recordsLoading: true, recordsError: ''});
      try {
        const response = await api.getFdeActivity({...this.activityParams(), period: 'all', limit: 30, offset: this.data.recordsOffset});
        if (this.closed || this.visible === false || serial !== this.recordSerial || identity !== access.identity(getApp().globalData.session)) return;
        if (!Array.isArray(response.items) || !Number.isInteger(response.total)) throw Error('记录暂未加载完成');
        this.setData({records: this.data.records.concat(response.items.map(present.visitItem)), recordsReady: true, recordsLoading: false, recordsMore: response.has_more, recordsOffset: response.next_offset, recordsTotal: response.total});
      } catch (error) { if (!this.closed && serial === this.recordSerial && identity === access.identity(getApp().globalData.session)) this.setData({recordsLoading: false, recordsError: error.message || '拜访记录加载失败'}); }
    },
    ownTargetScope(){return this.properties.profileScope!=='team'&&!this.properties.selectedMemberId;},
    buildTargetBoard(row,year,response){
      const targets={},pending=[...(response.pending_requests||[]),...(response.pending_batches||[])];
      (response.items||[]).forEach(item=>{targets[item.kind]=Number(item.amount);});
      this._targetRows=response.items||[];
      this.setData({canEditOwnTargets:this.ownTargetScope()&&response.editable===true,targetNotice:response.notice||''});
      return ['collection','recognized'].map(kind=>{const target=this._targetRows.find(item=>item.kind===kind);return {...salesMetrics.performanceMetric({targets},kind,row[kind+'_amount']),targetAmount:targets[kind],versionNo:target&&target.version_no,targetLabel:year+' Q'+this.data.metricQuarter+(this.properties.profileScope==='team'?' 团队目标':' 个人目标')};});
    },
    editOwnTarget(){const component=this.selectComponent&&this.selectComponent('#fdeQuarterTarget');if(component)component.show();},
    targetsSaved(){return this.loadMetrics();},
    selectHistory(e){
      const code=e?e.currentTarget.dataset.code:this.data.historyCode||(this.data.dimensions[0]||{}).code;
      const dimension=this.data.dimensions.find(row=>row.code===code);if(!dimension)return;
      const points=(this.data.history||[]).map(row=>({date:String(row.date||'').slice(0,10),score:present.numeric((row.dimensions||[]).find(v=>v.code===code)?.score)}));
      this.setData({historyCode:code,historyName:dimension.name,historyPoints:points});
      if(this.properties.chartsHidden || typeof this.createSelectorQuery!=='function')return;
      const draw=()=>this.createSelectorQuery().select('.history-canvas').boundingClientRect(rect=>{if(!rect||this.closed||this.visible===false||this.properties.chartsHidden||this.data.activeTab!=='profile')return;require('../../utils/fdeHistory').draw(wx.createCanvasContext('fdeHistory',this),rect.width,rect.height,points);}).exec();
      if(wx.nextTick)wx.nextTick(draw);else draw();
    },
    async loadMetricTargets(year) {
      const base={period_type:'quarter',anchor_date:year+'-'+String((this.data.metricQuarter-1)*3+1).padStart(2,'0')+'-01'};
      try {
        if(this.properties.profileScope!=='team')return await api.getTargets({...base,...(this.properties.selectedMemberId?{scope:'person',user_id:this.properties.selectedMemberId}:{scope:'self'})});
        if(!this.properties.selectedTeamId)return {items:[],editable:false,notice:'请选择要查看的团队。'};
        return {...await api.getTargets({...base,scope:'team',team_id:this.properties.selectedTeamId}),editable:false};
      } catch(error){return {items:[],editable:false,notice:error.message||'目标暂未加载，可刷新重试。'};}
    },
    async loadMetrics() {
      if(this.properties.profileScope==='team'&&!this.properties.selectedTeamId){this.setData({metricsLoading:false,metricsError:'请选择要查看的团队',performanceBoard:[],canEditOwnTargets:false});return;}
      const identity = access.identity(getApp().globalData.session), serial = this.metricSerial = (this.metricSerial || 0) + 1;
      const now=new Date(Date.now()+28800000),metricYear=now.getUTCFullYear(),metricQuarter=Math.floor(now.getUTCMonth()/3)+1;
      this.setData({metricsLoading:true,metricsError:'',projectMetrics:[],collaborationMetrics:[],stages:[],rhythm:[],metricYear,metricQuarter,targetContextKey:JSON.stringify([identity,this.properties.profileScope,this.properties.selectedMemberId,this.properties.selectedTeamId,metricYear,metricQuarter])});
      const current = () => !this.closed && this.visible !== false && serial===this.metricSerial && identity===access.identity(getApp().globalData.session);
      try {
        const query={...this.activityParams(),year:metricYear,quarters:[metricQuarter],period:'quarter'};
        const [result,targets]=await Promise.all([api.getFdeDashboard(query),this.loadMetricTargets(metricYear)]);
        if(!current()) return;
        if(result.data_source!=='database' || !result.summary) throw Error('协作数据暂未加载完成');
        const row=result.summary;this._targetActuals=row;
        const count=value=>present.numeric(value)===null?'—':String(present.numeric(value));
        this.setData({metricsLoading:false,
          performanceBoard:this.buildTargetBoard(row,metricYear,targets),
          maturityFacts:[
            {name:'在推商机 ACV',value:salesMetrics.money(row.open_acv),detail:'当前进行中的商机金额'},
            {name:'已赢单金额',value:salesMetrics.money(row.won_amount),detail:'已赢单商机累计金额，非确收'},
],
          projectMetrics:[
            {name:'参与商机',value:count(row.opportunities),unit:'个',note:'当前协助项目'},
            {name:'在推商机',value:count(row.open_opportunities),unit:'个',note:'当前进行中的项目'},
            {name:'在推金额',value:present.money(row.open_acv),unit:'万',note:'协助项目整体金额'},
            {name:'确收金额',value:present.money(row.recognized_amount),unit:'万',note:'当季度 · 协助项目'},
            {name:'回款金额',value:present.money(row.collection_amount),unit:'万',note:'当季度 · 协助项目'},
            {name:'参与客户',value:count(row.customers),unit:'家',note:'当前协助客户'}],
          collaborationMetrics:[
            {name:'归档跟进',value:count(row.period_visits),unit:'条',note:this.properties.profileScope==='team'?'当季度团队归档记录':this.properties.selectedMemberName?'当季度该成员归档记录':'当季度本人归档记录'},
            {name:'跟进客户',value:count(row.period_customers),unit:'家',note:'当季度覆盖客户'},
            {name:'已完成任务',value:count(row.completed_tasks),unit:'项',note:'当季度统计'},
            {name:'待完成任务',value:count(row.pending_tasks),unit:'项',note:'当季度统计'},
            {name:'逾期任务',value:count(row.overdue_tasks),unit:'项',note:'当季度统计'}],
          stages:present.visibleStageBars(result.stages||[]),rhythmSource:result.rhythm||[],rhythmWeeks:result.rhythm_weeks||[],rhythm:present.progressBars(this.data.rhythmMode==='week'?(result.rhythm_weeks||[]):(result.rhythm||[]),this.data.rhythmMode)});
      } catch(error) {if(current()) this.setData({metricsLoading:false,metricsError:error.message||'协作数据加载失败，请重试'});}
    },
    changeRhythm(e) {const mode=e.currentTarget.dataset.mode;if(!['day','week'].includes(mode))return;this.setData({rhythmMode:mode,rhythm:present.progressBars(mode==='week'?this.data.rhythmWeeks:this.data.rhythmSource,mode)});},
    async loadTasks() {
      if (this.data.tasksLoading) return;
      const identity = access.identity(getApp().globalData.session), serial = this.taskSerial = (this.taskSerial || 0) + 1;
      this.setData({tasksLoading: true, tasksError: '', tasks: [], taskSummary: null});
      const current = () => !this.closed && this.visible !== false && serial === this.taskSerial && identity === access.identity(getApp().globalData.session);
      try {
        const result = await api.listTaskPage({view:this.activityParams().scope,member_id:this.activityParams().member_id, tab: 'pending', page_size: 20, offset: 0});
        if (!current()) return;
        if (!result || !Array.isArray(result.items) || !result.summary) throw Error('任务数据暂未加载完成');
        this.setData({tasks: result.items, taskSummary: result.summary, tasksLoading: false});
      } catch (error) { if (current()) this.setData({tasksLoading: false, tasksError: error.message || '任务加载失败'}); }
    },
    openTasks() {const p=this.activityParams();wx.navigateTo({url:'/pages/tasks/index?scope='+p.scope+'&member_id='+encodeURIComponent(p.member_id||'')}); },
    openTask(e) { wx.navigateTo({url: '/pages/task-detail/index?id=' + encodeURIComponent(e.currentTarget.dataset.id)}); },
    openVisit(e) { const row = this.data.records.find(item => String(item.id) === String(e.currentTarget.dataset.id)); if (!row) return; if (!row.can_read_detail) { wx.showToast({title: '当前仅可查看这条历史摘要', icon: 'none'}); return; } wx.navigateTo({url: visitDetailUrl(row)}); },
    logout() { this.triggerEvent('logout'); },
  },
});
