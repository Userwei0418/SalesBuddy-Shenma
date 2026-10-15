# Web 1.1.0 适配记录

- 基础包：SalesBuddy-Web-1.1.0-完整源码与周报-20260929，原始 revision 5f022c53432ace4f22958f12f3f61fbc7511fec7。
- 当前小程序主线：4d9491637dbb785c09b5c904b57ad509dcf6ea83。
- 已回补：当前主线中从原 Web 基准之后变更的 frontend/miniprogram 文件，覆盖手机号登录、任务/跟进管理、商机季度筛选与相关页面逻辑。
- 已移除：Web 独有的周报 Demo 路由 pages/weekly-report/index 与 pages/report-detail/index，避免展示未纳入当前产品范围的能力。
- 线上状态：尚未切换；待本地构建、浏览器测试和同源静态入口检查通过后再发布。
