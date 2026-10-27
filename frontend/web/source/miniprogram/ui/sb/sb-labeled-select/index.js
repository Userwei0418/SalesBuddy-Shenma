Component({
  options: {addGlobalClass: true, multipleSlots: true},
  properties: {
    label: String, value: {type: null, value: null}, options: {type: Array, value: []},
    placeholder: {type: String, value: '全部'}, allowClear: {type: Boolean, value: true},
    disabled: {type: Boolean, value: false}, size: {type: String, value: 'default'},
    allOption: {type: Boolean, value: true}, title: String, defaultValue: {type: null, value: null},
    useLabelSlot: {type: Boolean, value: false}
  },
  data: {text: '全部', open: false, columns: [], pickerValue: [], selected: false, showValue: false, pickerEpoch: 0},
  lifetimes: {detached() { this._pickerSession = null; }},
  pageLifetimes: {hide() { this.invalidatePicker(); }},
  observers: {
    'value, options, placeholder, allOption, defaultValue'(value, options, placeholder, allOption, defaultValue) {
      this.invalidatePicker();
      const list = options || [], hit = list.find(option => option.value === value);
      const items = list.map(option => ({value: option.value, label: option.count != null ? `${option.label}（${option.count}）` : option.label}));
      const columns = allOption ? [{value: '', label: placeholder}, ...items] : items;
      const empty = value == null || value === '', base = defaultValue != null ? defaultValue : list[0] ? list[0].value : undefined;
      const selected = !!hit && !empty && (allOption || base !== value);
      this.setData({text: hit ? hit.label : placeholder, columns, selected, showValue: selected || !!hit && !allOption, pickerValue: [hit ? hit.value : allOption ? '' : items[0] ? items[0].value : '']});
    },
    disabled(value) { if (value) this.invalidatePicker(); }
  },
  methods: {
    pickerContext() { return JSON.stringify([this.data.value, this.data.options, this.data.placeholder, this.data.allOption, this.data.defaultValue]); },
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
      const value = values[0], option = (this.data.options || []).find(item => item.value === value) || null;
      if ((!option && !(value === '' && this.data.allOption)) || (option && option.disabled)) { this.invalidatePicker(); return; }
      this.invalidatePicker();
      this.triggerEvent('change', {value: value === '' ? null : value, option});
    },
    onClear() {
      if (this.data.disabled || !this.data.allowClear || !this.data.allOption) return;
      this.invalidatePicker(); this.triggerEvent('change', {value: null, option: null});
    }
  }
});
