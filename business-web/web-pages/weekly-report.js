// Web-only read adapter. Original business APIs, authentication and source stay unchanged.
const api = require('utils/apiClient');
Page({
  weeklyRequest(path='', options={}) { return api.weeklyRequest(path, options); },
  data: {records: [], loading: true, error: ''},
  onShow() { const app=getApp(); if (!app.ensureLogin()) return; if(app.globalData.session?.permissions && !app.can(globalThis.SALES_MODE === 'preview' ? 'visit.read' : 'weekly_report.read')){this.setData({loading:false,error:'当前身份没有跟进记录读取权限'});return;} if(globalThis.SALES_MODE === 'preview') this.loadRecords(); },
  onUnload() { this._loadSerial = (this._loadSerial || 0) + 1; },
  async loadRecords() {
    const serial = this._loadSerial = (this._loadSerial || 0) + 1;
    const identity = () => {const s = getApp().globalData.session || {}; return `${s.workspaceId}:${s.userId}:${s.role}:${s.permissionVersion}`;};
    const owner = identity(), current = () => serial === this._loadSerial && owner === identity() && !this._destroyed;
    this.setData({loading: true, error: '', records: []});
    try {
      const userId = getApp().globalData.session?.userId;
      if (!userId) throw Error('请重新登录后查看本人记录');
      const loadedAt = Date.now();
      const rows = new Map(); let offset = 0;
      for (let i = 0; i < 100; i++) {
        const result = await api.listVisits({offset, page_size: 100, sort: 'created_desc'});
        if (!current()) return;
        if (!Array.isArray(result?.items) || typeof result.has_more !== 'boolean') throw Error('更新记录返回不完整，请重试');
        // Scope by stable recorder ID, never by display name or customer ownership.
        for (const row of result.items) if (row.id && row.recorder_id === userId) rows.set(row.id, row);
        if (!result.has_more) { this.setData({records: [...rows.values()], loadedAt, loading: false}); return; }
        const next = Number(result.next_offset);
        if (!Number.isInteger(next) || next <= offset) throw Error('更新记录分页异常，请重试');
        offset = next;
      }
      throw Error('记录量较大，尚未完整加载；请稍后重试');
    } catch (error) { if (current()) this.setData({loading: false, error: error.message || '更新记录读取失败'}); }
  },
});
