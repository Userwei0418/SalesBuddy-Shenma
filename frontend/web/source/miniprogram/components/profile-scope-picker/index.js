const access = require('../../utils/access');
Component({
  properties: { modes: Array, members: Array, teams: Array, mode: String, memberId: String, teamId: String, defaultTeamId: String, label: String, loading: Boolean, error: String },
  data: { active:false,search:'',filterTeam:'',visibleMembers:[],open: false, selecting: 'person', personSelected: [] },
  observers: { 'members,teams,memberId,teamId,mode': function () { this.close(); this.filter(); this.markActive(); this.setData({personSelected: this.properties.memberId ? [this.properties.memberId] : []}); } },
  lifetimes: {attached(){this.markActive();}},
  pageLifetimes: { hide() { this.close(); } },
  methods: {
    markActive(){const p=this.properties,d=this._defaults||(this._defaults={}),sig=rows=>(rows||[]).map(row=>row.id).join(',');for(const [kind,rows,id] of [['member',p.members,p.memberId],['team',p.teams,p.teamId]]){const s=sig(rows);if(d[kind+'Sig']!==s){d[kind+'Sig']=s;d[kind]=null;}if(d[kind]===null&&id&&(rows||[]).some(row=>row.id===id))d[kind]=id;}const active=p.mode==='person'?d.member!==null&&p.memberId!==d.member:p.mode==='team'?d.team!==null&&p.teamId!==d.team:false;if(active!==this.data.active)this.setData({active});},
    onModeSegment(e){return this.switchMode({currentTarget:{dataset:{mode:e.detail.value}}});},
    onTeamTab(e){return this.teamFilter({currentTarget:{dataset:{id:e.detail.key}}});},
    search(e){this.setData({search:e.detail.value});this.filter();},
    teamFilter(e){this.setData({filterTeam:e.currentTarget.dataset.id||''});this.filter();},
    filter(){const query=String(this.data.search||'').trim().toLowerCase(),team=this.data.filterTeam;this.setData({visibleMembers:(this.properties.members||[]).filter(row=>(!team||(row.team_ids||[]).includes(team))&&(!query||String(row.name||'').toLowerCase().includes(query)))});},

    noop() {},
    switchMode(event) {
      const mode = event.currentTarget.dataset.mode;
      if (!(this.properties.modes || []).some(row => row.value === mode)) return;
      this.close(); this.triggerEvent('modechange', {mode});
    },
    show() {
      this._pickerIdentity=access.identity(getApp().globalData.session);
      this.triggerEvent('visibilitychange', {open: true});
      this.setData({open: true, search:'',filterTeam:'',selecting: this.properties.mode === 'team' ? 'team' : 'person', personSelected: this.properties.memberId ? [this.properties.memberId] : []});this.filter();
    },
    close() { if (!this.data.open) return; this.setData({open: false}); this.triggerEvent('visibilitychange', {open: false}); },
    select(event) {
      if(!this.data.open || this._pickerIdentity!==access.identity(getApp().globalData.session) || this.properties.loading || this.properties.error)return;
      const id = event.currentTarget.dataset.id, kind = this.data.selecting;
      const rows = kind === 'team' ? this.properties.teams : this.properties.members;
      if (!(rows || []).some(row => row.id === id)) return;
      this.close(); this.triggerEvent('subjectchange', {kind, id});
    },
    confirmPerson(event) {
      if(!this.data.open || this.data.selecting!=='person' || this._pickerIdentity!==access.identity(getApp().globalData.session))return;
      const ids = event.detail.ids;
      if (!Array.isArray(ids) || ids.length !== 1 || this.properties.loading || this.properties.error) return;
      this.select({currentTarget: {dataset: {id: ids[0]}}});
    },
    retry() { this.triggerEvent('retry'); },
  },
});
