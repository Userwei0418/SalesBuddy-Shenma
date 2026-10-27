const api = require('../../utils/apiClient');
const access = require('../../utils/access');
const reference = require('../../utils/opportunityFollowUpReference');

// Preserve readable detail/edit candidates; selection separately verifies the narrow follow-up reference.
// Structure, quality and archive revalidate the same customer/opportunity.
Component({
  properties: { customerId: String, selectedId: String, disabled: Boolean },
  data: { selected: null, query: '', items: [], loading: false, error: '', hasMore: false, offset: 0 },
  observers: { 'customerId, selectedId': function () { this.checkSelection(false, true); } },
  lifetimes: { detached() { this.closed = true; this.serial = (this.serial || 0) + 1; clearTimeout(this.searchTimer); } },
  methods: {
    notify(opportunity, extra = {}, deferred = false) {
      const notification = this.notificationSerial = (this.notificationSerial || 0) + 1;
      const customerId = this.properties.customerId;
      const identity = access.identity(getApp().globalData.session);
      const emit = () => {
        if (this.closed || notification !== this.notificationSerial || customerId !== this.properties.customerId || identity !== access.identity(getApp().globalData.session)) return;
        this.triggerEvent('change', { customerId, opportunity, resolved: Boolean(opportunity), ...extra });
      };
      if (deferred) wx.nextTick(emit);
      else emit();
    },
    checkSelection(force = false, deferred = false) {
      const key = `${this.properties.customerId || ''}:${this.properties.selectedId || ''}`;
      if (!force && key === this.contextKey) return;
      this.contextKey = key;
      this.serial = (this.serial || 0) + 1;
      clearTimeout(this.searchTimer);
      this.setData({ selected: null, query: '', items: [], offset: 0, error: '', hasMore: false, loading: false });
      this.notify(null, { resolved: !this.properties.selectedId }, deferred);
      if (this.properties.customerId) return this.load(false, Boolean(this.properties.selectedId));
    },
    async load(more = false, exact = false) {
      const customerId = this.properties.customerId, selectedId = this.properties.selectedId;
      if (!customerId || (more && (this.data.loading || !this.data.hasMore))) return;
      const serial = this.serial = (this.serial || 0) + 1;
      const identity = access.identity(getApp().globalData.session);
      const current = () => !this.closed && serial === this.serial && customerId === this.properties.customerId && selectedId === this.properties.selectedId && identity === access.identity(getApp().globalData.session);
      this.setData({ loading: true, error: '' });
      try {
        if (exact) {
          if(access.can(getApp().globalData.session,'visit.company_opportunity_follow_up')) {
            reference.read(await api.getOpportunityFollowUpAccess(selectedId),{opportunityId:selectedId,customerId});
            if(!current())return;
          }
          const result = await api.getOpportunityDetailOverview(selectedId);
          if (!current()) return;
          if (!result || String(result.id) !== String(customerId)) throw Error('已选商机不属于当前客户，请重新选择或清除关联');
          const row = (result.opportunities || []).find(item => item.id === selectedId) || (result.primary_opportunity && result.primary_opportunity.id === selectedId ? result.primary_opportunity : null);
          if (!row || (row.customer_id && row.customer_id !== customerId)) throw Error('已选商机不存在或不可读取，请重新选择或清除关联');
          this.setData({ selected: row, loading: false }); this.notify(row); return;
        }
        const offset = more ? this.data.offset : 0;
        const response = await api.listOpportunities({ customerId, query: this.data.query, includeClosed: true, pageSize: 20, offset, order: 'recent_follow_up' });
        if (!current()) return;
        if (!response || !Array.isArray(response.items) || response.items.length > 20 || typeof response.has_more !== 'boolean' ||
          (response.has_more && (!Number.isInteger(response.next_offset) || response.next_offset <= offset))) throw Error('商机列表响应不完整，请重试');
        const items = more ? this.data.items.slice() : [], seen = new Set(items.map(row => row.id));
        for (const row of response.items) {
          if (!row.id || (row.customer_id && row.customer_id !== customerId)) throw Error('商机客户数据不匹配');
          if (!seen.has(row.id)) { items.push(row); seen.add(row.id); }
        }
        this.setData({ items, offset: response.next_offset, hasMore: response.has_more, loading: false });
      } catch (error) {
        if (!current()) return;
        this.setData({ loading: false, error: error.message || '商机加载失败' });
        if (selectedId) this.notify(null);
      }
    },
    async choose(e) {
      if (this.properties.disabled || this.data.loading) return;
      const row = this.data.items.find(item => item.id === e.currentTarget.dataset.id);
      if (!row || (row.customer_id && row.customer_id !== this.properties.customerId)) return;
      const serial=this.serial=(this.serial||0)+1,customerId=this.properties.customerId,identity=access.identity(getApp().globalData.session);
      const current=()=>!this.closed&&serial===this.serial&&customerId===this.properties.customerId&&identity===access.identity(getApp().globalData.session);
      if(access.can(getApp().globalData.session,'visit.company_opportunity_follow_up')) {
        this.setData({loading:true,error:''});
        try {reference.read(await api.getOpportunityFollowUpAccess(row.id),{opportunityId:row.id,customerId});}
        catch(error){if(current())this.setData({loading:false,error:error.message||'商机跟进状态核对失败'});return;}
        if(!current())return;
      }
      this.contextKey = `${this.properties.customerId}:${row.id}`;
      this.setData({ selected: row, error: '', loading: false }); this.notify(row);
    },
    clear() {
      if (this.properties.disabled) return;
      this.serial = (this.serial || 0) + 1;
      this.contextKey = null;
      this.notify(null, { cleared: true, resolved: true });
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
