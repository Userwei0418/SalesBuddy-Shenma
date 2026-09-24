const api = require('../../utils/apiClient');
const opp = require('../../utils/opportunity');
const access = require('../../utils/access');
Component({
  properties: { customerId: String, existing: { type: Object, value: null }, visitContext: Boolean, disabled: Boolean, savedDraft: { type: Object, value: null } },
  data: { ownerTeams:[],ownerTeamIndex:0,ownerTeamLabel:'请选择所属团队',ownerTeamsLoading:false,ownerTeamsError:'',requiresOwnerTeam:false, partnerModes:['直销','合作伙伴'], partnerIndex:0, partners: [], partnerQuery: '', partnersLoading: false, partnersError: '', partnerTotal: 0, showPartnerSearch: false, form: opp.formFor(null), stages: opp.STAGES, error: '', checking: false, showMore: false,
    stageText: '请选择阶段', predictedCollection: '—', predictedRecognized: '—', forecastRate: '—', showForecast: false, forecastRequired: false, forecastError: '', quarterOptions: [], quarterIndex: 0, quarterLabel: '', recognized: '', collection: '', dateHint: '' },
  observers: { 'customerId, existing, visitContext': function() { this.reset(); } },
  lifetimes: { attached() { this.reset(); }, detached() { this.closed=true; clearTimeout(this.nameTimer); clearTimeout(this.partnerTimer); this.partnerSeq = (this.partnerSeq || 0) + 1; this.seq = (this.seq || 0) + 1; } },
  methods: {
    retryCatalog() {if(!require('../../utils/businessOptions').isReady())this.reset();},
    reset() {
      if (!require('../../utils/businessOptions').isReady()) {
        if(this.catalogLoading)return;
        this.catalogLoading=true;this.setData({error:'正在加载业务选项…'});
        api.getBusinessOptions().then(()=>{this.catalogLoading=false;if(!this.closed)this.reset();})
          .catch(error=>{this.catalogLoading=false;if(!this.closed)this.setData({error:(error.message||'业务选项加载失败')+' · 点击重试'});});
        return;
      }
      // Parent loading/disabled updates may re-deliver the same object property.
      // Only a different customer or server record may replace the user's edits.
      const sourceKey = JSON.stringify([this.properties.customerId, this.properties.existing || null, !!this.properties.visitContext]);
      if (this.sourceKey === sourceKey) return;
      this.sourceKey = sourceKey;
      this.formContext = {customerId:this.properties.customerId,opportunityId:(this.properties.existing || {}).id || '__new__'};
      clearTimeout(this.partnerTimer); this.partnerSeq=(this.partnerSeq || 0)+1;
      this.setData({partners:[],partnerQuery:'',partnersLoading:false,partnersError:'',partnerTotal:0,showPartnerSearch:false});
      clearTimeout(this.nameTimer); this.seq = (this.seq || 0) + 1;
      const now = opp.quarterNow(), opts = [];
      for (let y = now.year - 2; y <= now.year + 5; y++) for (let q = 1; q <= 4; q++) opts.push({ year: y, quarter: q, label: `${y} Q${q}` });
      const form = this.properties.visitContext
        ? opp.formForVisit(this.properties.existing, this.properties.savedDraft)
        : JSON.parse(JSON.stringify(this.properties.savedDraft || opp.formFor(this.properties.existing)));
      // A historical unknown channel needs confirmation; it is not inferred to be direct sales.
      if (!form.partner_mode) form.partner_mode = form.partner_name === '直销' ? 'direct' : form.partner_name ? 'partner' : 'unknown';
      if (form.partner_mode === 'direct') form.partner_name = '直销';
      form.quarters.forEach(q => { if (!opts.some(o => o.year === q.year && o.quarter === q.quarter)) opts.push({ year: q.year, quarter: q.quarter, label: `${q.year} Q${q.quarter}` }); });
      opts.sort((a,b) => a.year - b.year || a.quarter - b.quarter);
      this.setData({ stages:opp.STAGES, form, partnerIndex:form.partner_mode === 'partner' ? 1 : 0, forecastRequired: opp.forecastRequired(form.stageIndex), showForecast: opp.forecastRequired(form.stageIndex), forecastError: '', error: '', checking: false, quarterOptions: opts, quarterIndex: opts.findIndex(o => o.year === now.year && o.quarter === now.quarter), stageText: (opp.STAGES[form.stageIndex] || {}).text || '请选择阶段' });
      this.loadQuarter(); this.emit(); this.loadOwnerTeams();
    },
    async loadOwnerTeams() {
      const session=typeof getApp==='function'?getApp().globalData.session:null;
      const serial=this.ownerTeamSerial=(this.ownerTeamSerial||0)+1,identity=access.identity(session),source=this.sourceKey;
      const required=!!(session&&session.permissions&&!this.properties.existing&&access.can(session,'opportunity.create'));
      this.setData({requiresOwnerTeam:required,ownerTeams:[],ownerTeamsLoading:required,ownerTeamsError:''});
      if(!required)return;
      const current=()=>!this.closed&&serial===this.ownerTeamSerial&&source===this.sourceKey&&identity===access.identity(getApp().globalData.session);
      try {
        const result=await api.getOpportunityCreateOptions();if(!current())return;
        if(!Array.isArray(result.teams)||!result.teams.length)throw Error('当前没有可创建商机的团队，请联系管理员');
        const saved=this.data.form.owner_team_id,id=saved?(result.teams.some(t=>t.id===saved)?saved:''):result.default_team_id||'';
        const index=result.teams.findIndex(t=>t.id===id);
        this.setData({ownerTeams:result.teams,ownerTeamIndex:Math.max(0,index),ownerTeamLabel:index>=0?result.teams[index].name:'请选择所属团队',ownerTeamsLoading:false,'form.owner_team_id':id});this.emit();
      } catch(error){if(current())this.setData({ownerTeamsLoading:false,ownerTeamsError:error.message||'团队选项加载失败，请重试'});}
    },
    changeOwnerTeam(e) {
      if(this.properties.disabled)return;
      const index=Number(e.detail.value),team=this.data.ownerTeams[index];if(!team)return;
      this.setData({ownerTeamIndex:index,ownerTeamLabel:team.name,'form.owner_team_id':team.id});this.emit();
    },
    fdeChanged(e) {
      if (this.properties.disabled) return;
      const prefix = this.properties.visitContext ? 'visit_fde_' : 'fde_';
      this.setData({[`form.${prefix}members`]:e.detail.members, [`form.${prefix}member_ids`]:e.detail.memberIds});
      this.emit();
    },
    emit() { this.triggerEvent('change', { form: this.data.form, ...this.formContext }); },
    input(e) { if (this.properties.disabled) return; const key = e.currentTarget.dataset.key; this.setData({ [`form.${key}`]: e.detail.value }); this.emit(); if (key === 'name') { clearTimeout(this.nameTimer); this.seq = (this.seq || 0) + 1; this.setData({ error: '' }); this.nameTimer = setTimeout(() => this.checkName(), 350); } },
    partnerMode(e) {
      if (this.properties.disabled) return;
      const index = Number(e.detail.value);
      if (![0,1].includes(index) || (index === this.data.partnerIndex && this.data.form.partner_mode !== 'unknown')) return;
      this.setData({partnerIndex:index,'form.partner_mode':index ? 'partner' : 'direct','form.partner_id':null,'form.partner_name':index ? '' : '直销',showPartnerSearch:!!index});
      if (index) this.loadPartners();
      this.emit();
    },
    openPartners() { if (this.properties.disabled) return; this.setData({showPartnerSearch:!this.data.showPartnerSearch}); if (this.data.showPartnerSearch) this.loadPartners(); },
    partnerSearch(e) { this.setData({partnerQuery:e.detail.value}); this.partnerSeq = (this.partnerSeq || 0) + 1; clearTimeout(this.partnerTimer); this.partnerTimer=setTimeout(() => this.loadPartners(),250); },
    async loadPartners(e) {
      const more = !!(e && e.currentTarget && e.currentTarget.dataset.more);
      if (more && this.data.partnersLoading) return;
      const seq=this.partnerSeq=(this.partnerSeq || 0)+1;
      this.setData({partnersLoading:true,partnersError:''});
      try {
        const result=await api.listPartners({q:this.data.partnerQuery.trim(),offset:more ? this.data.partners.length : 0});
        if (seq!==this.partnerSeq) return;
        this.setData({partners:more ? this.data.partners.concat(result.items) : result.items,partnerTotal:result.total,partnersLoading:false});
      } catch(error) { if(seq===this.partnerSeq) this.setData({partnersLoading:false,partnersError:error.message || '伙伴目录加载失败，请重试'}); }
    },
    choosePartner(e) {
      if (this.properties.disabled) return;
      const partner=this.data.partners.find(p=>p.id===e.currentTarget.dataset.id);
      if (!partner) return;
      this.setData({'form.partner_id':partner.id,'form.partner_name':partner.name,showPartnerSearch:false}); this.emit();
    },
    stage(e) {
      if (this.properties.disabled) return;
      const index = Number(e.detail.value), required = opp.forecastRequired(index);
      this.setData({ 'form.stageIndex': index, stageText: opp.STAGES[index].text,
        forecastRequired: required, showForecast: required || this.data.showForecast, forecastError: '' });
      this.updatePredictions(); this.emit();
    },
    date(e) { this.setData({ 'form.expected_close_date': e.detail.value, dateHint: this.data.form.quarters.length ? '关单日期已调整，请检查季度回款和确收是否需要修改。' : '' }); this.emit(); },
    toggleMore() { this.setData({ showMore: !this.data.showMore }); },
    toggleForecast() { this.setData({ showForecast: !this.data.showForecast }); },
    quarter(e) { this.setData({ quarterIndex: Number(e.detail.value) }); this.loadQuarter(); },
    loadQuarter() { const q = this.data.quarterOptions[this.data.quarterIndex]; if (!q) return; const row = this.data.form.quarters.find(r => r.year === q.year && r.quarter === q.quarter); this.setData({ quarterLabel: q.label, recognized: row ? row.recognized : '', collection: row ? row.collection : '' }); this.updatePredictions(); },
    forecast(e) {
      if (this.properties.disabled) return;
      const q = this.data.quarterOptions[this.data.quarterIndex], key = e.currentTarget.dataset.key;
      const rows = this.data.form.quarters.map(r => ({ ...r }));
      let row = rows.find(r => r.year === q.year && r.quarter === q.quarter);
      if (!row) { row = { year: q.year, quarter: q.quarter, recognized: '', collection: '' }; rows.push(row); }
      row[key] = e.detail.value; this.setData({ 'form.quarters': rows, [key]: e.detail.value, forecastError: '' }); this.updatePredictions(); this.emit();
    },
    updatePredictions() {
      const index = this.data.form.stageIndex, stage = opp.STAGES[index];
      this.setData({
        predictedCollection: opp.weightedQuarterAmount(this.data.collection, index),
        predictedRecognized: opp.weightedQuarterAmount(this.data.recognized, index),
        forecastRate: stage && stage.probability !== null ? `${stage.probability}%` : '—'
      });
    },
    async checkName() {
      clearTimeout(this.nameTimer); const name = this.data.form.name.trim(), customerId = this.properties.customerId;
      if (!name || !customerId) return false;
      const seq = this.seq = (this.seq || 0) + 1; this.setData({ checking: true });
      try { const r = await api.checkOpportunityName(customerId, name, this.properties.existing && this.properties.existing.id);
        if (this.seq !== seq) return false;
        this.setData({ checking: false, error: r.available ? '' : r.message }); return r.available;
      } catch(e) { if (seq === this.seq) this.setData({ checking: false, error: '名称检查失败，请重试' }); return false; }
    },
    // BACKEND-CONTRACT 提交前共享校验：名称预检 /customers/{id}/opportunities/check-name 不是数据库唯一约束。
    // 30%及以上至少一组完整季度回款/确收；0 与未填不同；直销/伙伴的保存契约见 utils/opportunity.js。
    // 此组件同时供独立商机和拜访录入使用；后端所有写入口应复用相同校验与关单/重开确认规则。
    async prepare() {
      if(this.data.requiresOwnerTeam&&(this.data.ownerTeamsLoading||this.data.ownerTeamsError||!this.data.form.owner_team_id))throw Error(this.data.ownerTeamsError||'请选择商机所属团队');
      let p;
      try {
        p = opp.payload(this.data.form, this.properties.existing);
        if (this.properties.visitContext) delete p.fde_member_ids;
      }
      catch (error) {
        if (error.code === 'FORECAST_REQUIRED') {
          const quarter = error.forecastQuarter;
          const index = quarter ? this.data.quarterOptions.findIndex(q => q.year === quarter.year && q.quarter === quarter.quarter) : this.data.quarterIndex;
          this.setData({ showForecast: true, forecastError: error.message, quarterIndex: index >= 0 ? index : this.data.quarterIndex });
          this.loadQuarter();
        }
        throw error;
      }
      if (!await this.checkName()) throw new Error(this.data.error || '请等待名称检查完成');
      const confirmation = opp.needsConfirmation(p, this.properties.existing);
      if (confirmation) {
        const r = await new Promise(resolve => wx.showModal({ title: confirmation === 'close' ? `确认${p.status === 'won' ? '赢单' : '丢单'}？` : '确认重新打开商机？', content: '历史记录会保留，变化将通知负责人。', confirmText: '确认', success: resolve, fail: () => resolve({ confirm: false }) }));
        if (!r.confirm) throw new Error('已取消');
        p.closure_confirmed = confirmation === 'close'; p.reopen_confirmed = confirmation === 'reopen';
      }
      return p;
    }
  }
});
