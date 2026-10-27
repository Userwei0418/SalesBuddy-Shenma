const pad=n=>(n<10?"0":"")+n,fmt=d=>`${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`,fmtTime=d=>`${pad(d.getHours())}:${pad(d.getMinutes())}`,parseDate=v=>{if(v==null||v==="")return null;if(typeof v=="number")return new Date(v);const s=String(v).replace("T"," ").replace(/-/g,"/").replace(/(\.\d+)?(Z|[+-]\d{2}:?\d{2})$/,""),d=new Date(s);return isNaN(d.getTime())?null:d},toStr=(v,mode)=>{if(v==null||v==="")return"";const d=parseDate(v);return d?mode==="datetime"?`${fmt(d)} ${fmtTime(d)}`:fmt(d):String(v).slice(0,mode==="datetime"?16:10)},PRESET={today:{label:"\u4ECA\u5929",date:()=>fmt(new Date)},week:{label:"\u672C\u5468",date:()=>{const d=new Date,wd=d.getDay()||7;return d.setDate(d.getDate()+(7-wd)),fmt(d)}},yesterday:{label:"\u6628\u5929",date:()=>{const d=new Date;return d.setDate(d.getDate()-1),fmt(d)}},quarter:{label:"\u672C\u5B63",date:()=>{const d=new Date,m=Math.floor(d.getMonth()/3)*3+3;return fmt(new Date(d.getFullYear(),m,0))}}};
Component({
  options: {addGlobalClass: true},
  properties: {
    label: String, value: {type: null, value: ''}, placeholder: {type: String, value: '请选择日期'},
    start: {type: null, value: null}, end: {type: null, value: null}, disabled: {type: Boolean, value: false},
    required: {type: Boolean, value: false}, shortcuts: {type: Array, value: ['today', 'week', 'quarter']},
    mode: {type: String, value: 'date'}, title: String, error: String, help: String,
    clearable: {type: Boolean, value: false}, defaultTime: {type: String, value: '18:00'},
    layout: {type: String, value: 'inline'}, plain: {type: Boolean, value: false}
  },
  data: {open: false, text: '', pickerValue: '', chips: [], pickerEpoch: 0},
  lifetimes: {
    attached() { this.buildChips(this.data.shortcuts); if (!this.data.pickerValue) this.setData({pickerValue: this.now()}); },
    detached() { this._pickerSession = null; }
  },
  pageLifetimes: {hide() { this.invalidatePicker(); }},
  observers: {
    'value, mode'(value, mode) {
      this.invalidatePicker();
      const text = toStr(value, mode);
      this.setData({text, pickerValue: text || this.now()});
    },
    'shortcuts, mode, defaultTime, start, end'(list) { this.invalidatePicker(); this.buildChips(list); },
    disabled(value) { if (value) this.invalidatePicker(); }
  },
  methods: {
    now() { const date = new Date(); return this.data.mode === 'datetime' ? `${fmt(date)} ${fmtTime(date)}` : fmt(date); },
    buildChips(list) {
      const time = this.data.mode === 'datetime' ? ' ' + (this.data.defaultTime || '18:00') : '';
      const lo = toStr(this.data.start, 'date'), hi = toStr(this.data.end, 'date');
      this.setData({chips: (list || []).map(shortcut => typeof shortcut === 'string'
        ? {key: shortcut, label: PRESET[shortcut] ? PRESET[shortcut].label : shortcut, value: PRESET[shortcut] ? PRESET[shortcut].date() + time : shortcut}
        : {key: shortcut.value, label: shortcut.label, value: shortcut.value})
        .filter(chip => { const date = String(chip.value).slice(0, 10); return (!lo || date >= lo) && (!hi || date <= hi); })});
    },
    pickerContext() { return JSON.stringify([this.data.value, this.data.mode, this.data.start, this.data.end, this.data.defaultTime]); },
    invalidatePicker() { this._pickerSession = null; if (this.data.open) this.setData({open: false}); },
    currentPickerEvent(event) {
      const session = this._pickerSession, epoch = event && event.currentTarget && event.currentTarget.dataset && event.currentTarget.dataset.pickerEpoch;
      return !!session && this.data.open && !this.data.disabled && Number(epoch) === session.epoch && session.context === this.pickerContext();
    },
    onClear() { if (!this.data.disabled) { this.invalidatePicker(); this.emit(''); } },
    onOpen() {
      if (this.data.disabled || this.data.open) return;
      const epoch = (this.data.pickerEpoch || 0) + 1;
      this._pickerSession = {epoch, context: this.pickerContext()};
      this.setData({open: true, pickerEpoch: epoch});
    },
    onClose(event) {
      // TDesign sends close(confirm-btn) before confirm. Keep that session until
      // its confirm is checked; every other close retires only its own session.
      if (event && event.detail && event.detail.trigger === 'confirm-btn') return;
      if (!event || this.currentPickerEvent(event)) this.invalidatePicker();
    },
    onConfirm(event) {
      if (!this.currentPickerEvent(event)) return;
      const value = toStr(event.detail.value, this.data.mode);
      this.invalidatePicker(); this.emit(value);
    },
    onChip(event) { if (!this.data.disabled) { this.invalidatePicker(); this.emit(event.currentTarget.dataset.value); } },
    emit(value) { if (!this.data.disabled && value !== this.data.text) this.triggerEvent('change', {value}); }
  }
});
