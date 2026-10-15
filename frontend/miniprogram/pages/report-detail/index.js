/**
 * BACKEND-CONTRACT 报告条目仅通过 runId/action/section/row 定位；GET /agent/runs/:runId 后重新 buildReport，不信任本地报告正文。
 * 响应必须含 run.id、result 与对应 action 的结构化数组。权限由服务端核验 run 归属与数据范围，切换账号后响应不展示。
 * 无报告保存、导出或执行推荐任务接口；首页报告生成函数当前缺少可见快捷入口，不能据本详情路由认定报告已全链路可用。
 */
const apiClient = require('../../utils/apiClient');
const {buildReport} = require('../../utils/operatingReport');
const access = require('../../utils/access');

Page({
  data: {detail:null,loading:false,error:''},
  onLoad(options={}) {
    this._hidden=false;
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'report-detail', options)) return;
    if (!getApp().ensureLogin()) return;
    wx.removeStorageSync('pendingReportDetail');
    this.reference=options;
    return this.loadReport();
  },
  loadReport() {
    const serial=this._loadSerial=(this._loadSerial||0)+1;
    const identity=access.identity(getApp().globalData.session);
    const current=()=>!this._hidden&&serial===this._loadSerial&&identity===access.identity(getApp().globalData.session);
    const ref=this.reference || {};
    if (!ref.runId || !ref.action || !ref.section || !/^\d+$/.test(String(ref.row))) {
      this.setData({detail:null,loading:false,error:'缺少报告标识，请从总览重新打开'});return Promise.resolve();
    }
    this.setData({loading:true,error:'',detail:null});
    return apiClient.getRun(ref.runId).then(run=>{
      if (!current()) return;
      const result=buildReport(run,ref.action);
      const detail=result.details[`${ref.runId}_${ref.section}_${Number(ref.row)}`];
      if (!detail) throw new Error('报告条目不存在或已无权限');
      this.setData({detail,loading:false});
    }).catch(error=>{if(current())this.setData({loading:false,detail:null,error:error.message || '报告加载失败'});});
  },
  onShow() {
    if (!this._hidden) return;
    this._hidden=false;
    if (getApp().guardPage && !getApp().guardPage(this,'report-detail',this.reference)) return;
    if (getApp().ensureLogin()) return this.loadReport();
  },
  onHide() {this._hidden=true;this._loadSerial=(this._loadSerial||0)+1;this.setData({detail:null,loading:false});},
  onUnload() {this.onHide();},
});
