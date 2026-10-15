# 工作报告概览：任务情况

任务：`SB120-WR-TASK-OVERVIEW-001`，2026-10-12。

## 行为

- 本期概览顶部展示「本期新增任务」「本期完成任务」，点击后进入任务情况明细。
- 新增按创建时间，完成按实际完成时间；以前创建、本期完成的任务计入完成数。完成后重开的任务保留本期完成事实，同时显示当前状态。
- 归属按当前任务负责人；团队按任务去重。成员展开直接读取后端成员计数和任务引用，不累计成员数得到团队总数。
- 明细显示名称、负责人、关联客户和商机、创建/完成时间、当前状态，可进入已有任务详情。
- 会议模式含新增任务与完成任务章节，每页最多 5 条，保持只读；退出后保留概览筛选。
- 完整零值显示 0，部分覆盖或旧接口未提供时显示「—」和说明，不从列表长度或状态推算缺失统计。

## 配套接口

使用 `GET /work-reports/overview` 的可选顶层 `task_activity`，版本 `work_report.task_activity.v1`。其 period、scope、data_as_of 必须与本次概览一致。后端返回 counts、new_tasks、completed_tasks、成员独立 counts/IDs、sources 和 missing_reasons。

此字段与冻结 facts 并列，仅用于本期概览。原报告 facts、hash、generation binding、材料准备和生成请求保持原样；既有草稿、报告正文和已保存会议快照不会插入实时任务数据。

后端固定合成样例已原样保存为 `tests/fixtures/work-report-task-activity.response.json`，SHA-256：`0daa3bdd15c387c20357f0636730513fd51827a577f508591043e298ae52a756`。覆盖多人负责去重、旧任务本期完成后重开、待发起人确认、缺完成时间、零值及旧响应。

## 离线验证

```sh
SALES_WEB_DIST_DIR=.runtime/task-overview-20261012/dist npm run build
SALES_WEB_DIST_DIR=.runtime/task-overview-20261012/dist node --test tests/work-report*.test.mjs
PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs REPORT_SHOTS=.runtime/task-overview-20261012/shots node --test tests/work-report-task-activity.browser.mjs
```

- 构建通过：32 页、57 组件，496 个共享文件保持不变。
- 工作报告回归 80/80 通过，包含服务端任务样例与生成绑定不变检查。测试必须指向当前候选 dist；默认历史 dist 曾导致 3 个生成客户端用例失败，指向当前候选后通过，未修改测试断言或客户端。
- 隔离 Chrome 1440 / 1024 / 600 三档 3/3 通过，包含指标下钻、成员范围、任务详情入口、会议章节和退出后保留选择；未调用真实业务 API 或模型。
- 截图验证顶部六项自适应布局，无整页横向溢出；宽任务表保留内部横向滚动。

## 验收边界

本交付为 Web 源码与离线验证，不等于本地已启用或线上发布。应在后端配套契约集成后，按总控窗口切换固定 Web 制品，再用真实账户验收个人/团队、周期切换、明细与权限。当前工作报告生成故障的恢复包和现场由总控独立维护，本增量不改变正在运行的固定服务。
