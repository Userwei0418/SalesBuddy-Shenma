Component({
  options: {addGlobalClass: true},
  properties: {
    label: String, value: {type: null, value: null}, options: {type: Array, value: []},
    placeholder: {type: String, value: '请选择'}, disabled: {type: Boolean, value: false},
    required: {type: Boolean, value: false}, title: String, defaultValue: {type: null, value: null},
    error: String, help: String, layout: {type: String, value: 'inline'}, plain: {type: Boolean, value: false}
  },
  data: {open: false, text: '', columns: [], pickerValue: [], pickerEpoch: 0},
  lifetimes: {detached() { this._pickerSession = null; }},
  pageLifetimes: {hide() { this.invalidatePicker(); }},
  observers: {
    'value, options, defaultValue'(value, options, defaultValue) {
      // A parent reset must retire the open choice even when the index is reused.
      this.invalidatePicker();
      const list = options || [], hit = list.find(option => option.value === value);
      const columns = list.map(option => ({value: option.value, label: (option.count != null ? `${option.label}（${option.count}）` : option.label) + (option.disabled ? '（不可选）' : '')}));
      this.setData({text: hit ? hit.label : '', columns, pickerValue: [hit ? hit.value : list.some(option => option.value === defaultValue) ? defaultValue : list[0] ? list[0].value : '']});
    },
    disabled(value) { if (value) this.invalidatePicker(); }
  },
  methods: {
    pickerContext() { return JSON.stringify([this.data.value, this.data.options, this.data.defaultValue]); },
    invalidatePicker() { this._pickerSession = null; if (this.data.open) this.setData({open: false}); },
    currentPickerEvent(event) {
      const session = this._pickerSession, epoch = event && event.currentTarget && event.currentTarget.dataset && event.currentTarget.dataset.pickerEpoch;
      return !!session && this.data.open && !this.data.disabled && Number(epoch) === session.epoch && session.context === this.pickerContext();
    },
    onOpen() {
      if (this.data.disabled || this.data.open || !this.data.columns.length) return;
      const epoch = (this.data.pickerEpoch || 0) + 1;
      this._pickerSession = {epoch, context: this.pickerContext()};
      this.setData({open: true, pickerEpoch: epoch});
    },
    onCancel(event) { if (!event || this.currentPickerEvent(event)) this.invalidatePicker(); },
    onClose(event) {
      if (event && event.detail && event.detail.trigger === 'confirm-btn') return;
      if (this.currentPickerEvent(event)) this.invalidatePicker();
    },
    onConfirm(event) {
      if (!this.currentPickerEvent(event)) return;
      const values = event.detail && event.detail.value;
      if (!Array.isArray(values) || values.length !== 1) return;
      const value = values[0], option = (this.data.options || []).find(item => item.value === value);
      if (!option) { this.invalidatePicker(); return; }
      if (option.disabled) { wx.showToast({title: `${option.label}不可选`, icon: 'none'}); return; }
      this.invalidatePicker();
      if (value !== this.data.value) this.triggerEvent('change', {value, option});
    }
  }
});
