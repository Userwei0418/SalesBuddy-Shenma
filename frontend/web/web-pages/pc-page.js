// PC-owned route（客户经营、跟进记录、工作报告、知识库、申请历史）。
// 页面展示与只读取数在 department-ui/pc 中完成，复用 utils/apiClient 与当前登录态；
// 这里只给路由一个页面生命周期，不发请求、不写数据，也不改共享业务源码。
Page({
  data: {pcRoute: true},
  onLoad(options = {}) { this.setData({options}); },
});
