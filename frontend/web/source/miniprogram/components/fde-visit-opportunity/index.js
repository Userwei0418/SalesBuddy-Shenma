const api = require('../../utils/apiClient');
const access = require('../../utils/access');

// This list is a write-eligibility contract. Customer-wide read lists must never
// be used here: an FDE can read other projects without being allowed to record them.
Component({
  properties: { customerId: String, selectedId: String, disabled: Boolean },
  data: { selected: null, query: '', items: [], loading: false, error: '', hasMore: false, offset: 0 },
  observers: { 'customerId, selectedId': function () { this.checkSelection(false, true); } },
  lifetimes: { detached() { this.closed = true; this.serial = (this.serial || 0) + 1; clearTimeout(this.searchTimer); } },
  methods: {
    notify(opportunity, extra = {}, deferred = false) {
      const notification = this.notificationSerial = (this.notificationSerial || 0) + 1;
      const identity = access.identity(getApp().globalData.session);
      const emit = () => {
        if (this.closed || notification !== this.notificationSerial || identity !== access.identity(getApp().globalData.session)) return;
        this.triggerEvent('change', { opportunity, verified: Boolean(opportunity), ...extra });
      };
      // Property observers run inside the parent's data update. Emit after that
      // update finishes; a newer selection/result invalidates the pending reset.
      if (deferred) wx.nextTick(emit);
      else emit();
    },
    checkSelection(force = false, deferred = false) {
      const key = `${this.properties.customerId || ''}:${this.properties.selectedId || ''}`;
      if (!force && key === this.contextKey) return;
      this.contextKey = key;
      this.serial = (this.serial || 0) + 1;
      clearTimeout(this.searchTimer);
      this.setData({ selected: null, query: '', items: [], offset: 0, error: '', hasMore: false });
      this.notify(null, {}, deferred);
      if (this.properties.customerId) return this.load(false, Boolean(this.properties.selectedId));
    },
    async load(more = false, exact = false) {
      const customerId = this.properties.customerId;
      if (!customerId || (more && this.data.loading)) return;
      const serial = this.serial = (this.serial || 0) + 1;
      const identity = access.identity(getApp().globalData.session);
      this.setData({ loading: true, error: '' });
      try {
        const response = await api.listFdeVisitOpportunities({ customer_id: customerId,
          ...(exact ? { opportunity_id: this.properties.selectedId } : { q: this.data.query }),
          limit: 50, offset: more ? this.data.offset : 0 });
        if (this.closed || serial !== this.serial || identity !== access.identity(getApp().globalData.session)) return;
        if (!Array.isArray(response.items) || !Number.isInteger(response.total)) throw Error('商机列表响应不完整，请重试');
        if (exact) {
          const row = response.items.find(item => item.id === this.properties.selectedId && item.customer_id === customerId);
          if (!row) throw Error('你已不在这条商机的协助名单中，请重新选择本人参与的商机。');
          this.setData({ selected: row, loading: false });
          this.notify(row);
          return;
        }
        this.setData({ items: more ? this.data.items.concat(response.items) : response.items,
          offset: response.next_offset, hasMore: Boolean(response.has_more), loading: false });
      } catch (error) {
        if (serial === this.serial) { this.setData({ loading: false, error: error.message || '商机加载失败' }); this.notify(null); }
      }
    },
    choose(e) {
      if (this.properties.disabled) return;
      const row = this.data.items.find(item => item.id === e.currentTarget.dataset.id);
      if (!row || row.customer_id !== this.properties.customerId) return;
      this.contextKey = `${this.properties.customerId}:${row.id}`;
      this.setData({ selected: row, error: '' }); this.notify(row);
    },
    change() {
      if (this.properties.disabled) return;
      this.contextKey = null;
      this.notify(null, { cleared: true });
    },
    search(e) {
      if (this.properties.disabled) return;
      this.serial = (this.serial || 0) + 1;
      this.setData({ query: e.detail.value, items: [], hasMore: false });
      clearTimeout(this.searchTimer); this.searchTimer = setTimeout(() => this.load(), 250);
    },
    more() { return this.load(true); },
    retry() { return this.checkSelection(true); },
  },
});
