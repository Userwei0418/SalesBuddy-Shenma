const { normalizeTeams, normalizeMembers, candidateTeam, validSelection, filterMembers } = require('../../utils/personPicker');
Component({
  options: { addGlobalClass: true },
  properties: {
    open: Boolean, members: Array, teams: Array, defaultTeamId: String, selected: Array,
    multiple: Boolean, allowAll: Boolean,
    allLabel: { type: String, value: '全部成员' }, title: { type: String, value: '选择成员' },
    loading: Boolean, error: String, maxSelected: { type: Number, value: 100 },
  },
  data: { query: '', teamId: '', teamRows: [], rows: [], draft: [], selectedRows: [], selectionError: '', selectionInvalid:false, canConfirm: false },
  observers: {
    open(value) { if (value) this.begin(); },
    'members, teams, defaultTeamId, loading, error, multiple, allowAll, maxSelected': function () { if (this.properties.open) this.refresh(); },
  },
  methods: {
    noop() {},
    begin() {
      this._teamTouched=false;
      this.setData({ query: '', teamId: candidateTeam(this.properties.teams, this.properties.defaultTeamId),
        draft: [...new Set((this.properties.selected||[]).map(String))], selectionError: '', selectionInvalid:false });
      this.refresh();
    },
    refresh() {
      const teamRows = normalizeTeams(this.properties.teams), members = normalizeMembers(this.properties.members, teamRows);
      const teamId = candidateTeam(teamRows, this._teamTouched?this.data.teamId:this.properties.defaultTeamId);
      const draft = this.properties.loading?this.data.draft.slice():validSelection(this.data.draft, members, this.properties.multiple, this.properties.maxSelected);
      const selectionInvalid=this.data.selectionInvalid || draft.length!==this.data.draft.length;
      const selectionError=selectionInvalid?'部分已选人员已不可用，请重新选择'+(this.properties.allowAll?'或明确选择全部':''):this.data.selectionError;
      this.setData({ teamRows, teamId, draft, selectionInvalid, selectionError,
        rows: filterMembers(members, teamId, this.data.query).map(row => ({ ...row, checked: draft.includes(row.id) })),
        selectedRows: members.filter(row => draft.includes(row.id)),
        canConfirm: !selectionInvalid && !this.properties.loading && !this.properties.error && (draft.length > 0 || !!this.properties.allowAll) });
    },
    search(event) { this.setData({ query: event.detail.value, selectionError: '' }); this.refresh(); },
    chooseTeam(event) {
      this._teamTouched=true;
      this.setData({ teamId: candidateTeam(this.properties.teams, event.currentTarget.dataset.id), selectionError: '' });
      this.refresh();
    },
    toggle(event) {
      if (this.properties.loading || this.properties.error) return;
      const id = String(event.currentTarget.dataset.id || '');
      const members = normalizeMembers(this.properties.members, this.properties.teams);
      if (!members.some(row => row.id === id)) return;
      let draft = this.data.draft.slice();
      if (this.properties.multiple) {
        if (draft.includes(id)) draft = draft.filter(value => value !== id);
        else if (draft.length >= (Number(this.properties.maxSelected) || 100)) { this.setData({ selectionError: `最多选择 ${Number(this.properties.maxSelected) || 100} 人` }); return; }
        else draft.push(id);
      } else draft = [id];
      this.setData({ draft, selectionError: '', selectionInvalid:false }); this.refresh();
    },
    chooseAll() { if (!this.properties.allowAll || this.properties.loading || this.properties.error) return; this.setData({ draft: [], selectionError: '', selectionInvalid:false }); this.refresh(); },
    confirm() { if(!this.properties.open)return;this.refresh(); if (this.data.canConfirm) this.triggerEvent('confirm', { ids: this.data.draft.slice(), teamId: this.data.teamId }); },
    close() { this.triggerEvent('close'); },
    retry() { this.triggerEvent('retry'); },
  },
});
