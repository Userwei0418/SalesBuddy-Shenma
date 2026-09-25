/* Web presentation bridge. Native editors still own field normalization, validation and IDs. */
(function (global) {
  'use strict';
  const textFields = new Map([
    ['customer_name', '请输入客户全称'], ['partner_name', '选填，可留空'],
    ['contact_name', '请输入首要联系人姓名'], ['contact_title', '请输入联系人职位'],
  ]);
  const live = page => !page._destroyed && global.SalesRuntime?.current === page && !page.data.accessBlocked;
  const identity = () => {
    const s = global.SalesRuntime?.app?.globalData?.session;
    return s ? [s.workspaceId, s.userId, s.role, s.permissionVersion, s.loginAt].join(':') : '';
  };
  const eventFor = index => ({currentTarget: {dataset: {index}}});
  function configureCustomer(page) {
    page._webInlineDrafts ||= new Map();
    page._webInlineErrors ||= new Map();
    // Decoration is recalculated after voice fill, team directory refresh and native editing.
    for (const field of page.data.fields || []) {
      field.webInlineKind = textFields.has(field.key) ? 'text' : 'select';
      field.webInlinePlaceholder = textFields.get(field.key) || '';
      field.webInlineValue = page._webInlineDrafts.has(field.key) ? page._webInlineDrafts.get(field.key) : field.value;
      field.webInlineError = page._webInlineErrors.get(field.key) || '';
      field.webInlineMissing = Boolean(field.required && !String(field.webInlineValue || '').trim());
    }
    page.data.webRemainingCount = (page.data.fields || []).filter(field => field.webInlineMissing).length;
    page.data.webProgressPercent = page.data.requiredCount ? Math.round((page.data.requiredCount - page.data.webRemainingCount) / page.data.requiredCount * 100) : 0;
    if (page._webInlineConfigured) return;
    page._webInlineConfigured = true;
    function commit(index, focusOnError = false) {
      if (!live(page)) return false;
      const field = page.data.fields[index];
      if (!field || field.readonly || !page._webInlineDrafts.has(field.key)) return true;
      const value = page._webInlineDrafts.get(field.key);
      if (value === field.value) {
        page._webInlineDrafts.delete(field.key); page._webInlineErrors.delete(field.key); return true;
      }
      // These calls are synchronous and the renderer batches them, so no modal is shown.
      page.openEditor(eventFor(index));
      page.inputEditor({detail: {value}});
      page.saveEditor();
      const saved = page.data.editorIndex === -1;
      page.closeEditor();
      if (saved) {
        page._webInlineDrafts.delete(field.key); page._webInlineErrors.delete(field.key);
      } else {
        page._webInlineErrors.set(field.key, `${field.label}为必填项`);
        if (focusOnError) requestAnimationFrame(() => {
          if (live(page)) document.querySelector(`.review-customer-input[data-field-key="${field.key}"]`)?.focus();
        });
      }
      page.setData({});
      return saved;
    }
    page.webInputCustomerField = function (event) {
      const index = Number(event.currentTarget.dataset.index), field = this.data.fields[index];
      if (!live(this) || !field || field.readonly || !textFields.has(field.key)) return;
      this._webInlineDrafts.set(field.key, event.detail.value);
      this._webInlineErrors.delete(field.key);
      this.setData({});
    };
    page.webCommitCustomerField = function (event) { return commit(Number(event.currentTarget.dataset.index)); };
    page.webChooseCustomerField = function (event) {
      const index = Number(event.currentTarget.dataset.index), field = this.data.fields[index];
      if (!live(this) || !field || field.readonly) return;
      this.openEditor(eventFor(index));
      if (this.data.editorType !== 'select' || !global.SalesSelect) return;
      const options = this.data.editorOptions.map((option, index) => ({value: String(index), text: option.label, selected: option.selected, id: option.id}));
      this.closeEditor();
      const snapshot = JSON.stringify(options), context = identity();
      const anchor = document.querySelector(`.review-customer-choice[data-index="${index}"]`);
      global.SalesSelect.open({owner: this, anchor, title: field.label, multiple: false,
        options, selected: options.filter(option => option.selected).map(option => option.value),
        description: options.length ? '' : '暂无可用选项，请稍后重试。',
        guard: () => live(this) && context === identity(),
        commit: values => {
          if (!live(this) || context !== identity() || !values.length) return;
          this.openEditor(eventFor(index));
          const current = this.data.editorOptions.map((option, index) => ({value: String(index), text: option.label, selected: option.selected, id: option.id}));
          if (JSON.stringify(current) !== snapshot) {
            this.closeEditor(); global.SalesRuntime.wx.showToast({title: '可用选项已更新，请重新选择', icon: 'none'}); return;
          }
          this.selectOption(eventFor(Number(values[0])));
          this.saveEditor();
          this.closeEditor();
        },
      });
    };
    function preserveVisibleDraft() {
      if (!live(page)) return false;
      if (!page._webInlineDrafts.size) return true;
      // An incomplete draft is valid draft data. Apply the visible text through
      // native refresh so missingCount remains authoritative for formal creation.
      const fields = page.data.fields.map(field => {
        if (field.readonly || !textFields.has(field.key) || !page._webInlineDrafts.has(field.key)) return field;
        return {...field, value: String(page._webInlineDrafts.get(field.key) || '').trim(), edited: true};
      });
      page._webInlineDrafts.clear();
      page._webInlineErrors.clear();
      page.setData({draftRestored: false});
      page.refresh(fields);
      return true;
    }
    const submitCustomer = page.submitCustomer, saveDraft = page.saveDraft, toggleVoice = page.toggleVoice;
    page.submitCustomer = function (...args) {
      // Formal creation still uses native field and form validation. It must
      // never silently fall back to the previous value of a cleared field.
      for (let index = 0; index < this.data.fields.length; index++) if (!commit(index, true)) return;
      return submitCustomer.apply(this, args);
    };
    page.saveDraft = function (...args) {
      if (preserveVisibleDraft()) return saveDraft.apply(this, args);
    };
    page.toggleVoice = function (...args) {
      // Stopping and transitional recording states always retain native behavior.
      if (this.data.isRecording || this.data.isStarting || this.data.isStopping || this.data.isParsing) return toggleVoice.apply(this, args);
      if (preserveVisibleDraft()) return toggleVoice.apply(this, args);
    };
  }
  function configureOpportunity(page, api) {
    if (page._webCustomerChoicesConfigured) return;
    page._webCustomerChoicesConfigured = true;
    page.webLoadInitialCustomers = function () {
      if (!live(this) || this.data.customerId || this.data.query || this.data.webCustomerLoading) return;
      const context = identity(), serial = this.searchSerial = (this.searchSerial || 0) + 1;
      const current = () => live(this) && context === identity() && serial === this.searchSerial && !this.data.customerId && !this.data.query;
      this.setData({webCustomerLoading: true, webCustomerError: ''});
      return api.listCustomers({page: 1, pageSize: 5}).then(response => {
        if (current()) this.setData({customers: Array.isArray(response.items) ? response.items : [], webCustomerLoading: false});
      }).catch(error => {
        if (current()) this.setData({webCustomerLoading: false, webCustomerError: error.message || '客户列表加载失败'});
      });
    };
    const input = page.inputCustomer;
    page.inputCustomer = function (event) {
      this.setData({webCustomerLoading: false, webCustomerError: ''});
      const result = input.call(this, event);
      if (!event.detail.value.trim()) this.webLoadInitialCustomers();
      return result;
    };
    // Render is reached while source onLoad may still run. Defer until it has applied route context.
    queueMicrotask(() => { if (live(page) && !page.data.customerId && !page.data.query) page.webLoadInitialCustomers(); });
  }
  function configure(page, api) {
    if (page.route === 'pages/customer-create/index') configureCustomer(page);
    if (page.route === 'pages/opportunity-create/index' && api?.listCustomers) configureOpportunity(page, api);
  }
  global.SalesReviewForms = Object.freeze({configure});
})(globalThis);
