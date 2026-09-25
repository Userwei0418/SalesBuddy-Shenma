/**
 * BACKEND-CONTRACT 报告条目仅通过 runId/action/section/row 定位；GET /agent/runs/:runId 后重新 buildReport，不信任本地报告正文。
 * 响应必须含 run.id、result 与对应 action 的结构化数组。权限由服务端核验 run 归属与数据范围，切换账号后响应不展示。
 * 无报告保存、导出或执行推荐任务接口；首页报告生成函数当前缺少可见快捷入口，不能据本详情路由认定报告已全链路可用。
 */
const apiClient = require('../../utils/apiClient');
const {buildReport} = require('../../utils/operatingReport');

Page({
  data: {detail:null,loading:false,error:''},
  onLoad(options={}) {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'report-detail', options)) return;
    if (!getApp().ensureLogin()) return;
    wx.removeStorageSync('pendingReportDetail');
    this.reference=options;
    return this.loadReport();
  },
  loadReport() {
    const ref=this.reference || {};
    if (!ref.runId || !ref.action || !ref.section || !/^\d+$/.test(String(ref.row))) {
      this.setData({detail:null,error:'缺少报告标识，请从总览重新打开'});return Promise.resolve();
    }
    const session=getApp().globalData.session;
    const account=`${session.workspaceId}:${session.userId}`;
    this.setData({loading:true,error:'',detail:null});
    return apiClient.getRun(ref.runId).then(run=>{
      const current=getApp().globalData.session;
      if (`${current.workspaceId}:${current.userId}`!==account) return;
      const result=buildReport(run,ref.action);
      const detail=result.details[`${ref.runId}_${ref.section}_${Number(ref.row)}`];
      if (!detail) throw new Error('报告条目不存在或已无权限');
      this.setData({detail,loading:false});
    }).catch(error=>this.setData({loading:false,detail:null,error:error.message || '报告加载失败'}));
  },
});
