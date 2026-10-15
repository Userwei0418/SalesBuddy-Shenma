# 商机筛选与主列表分页

用户授权增量：`OPP-SCHEME-MULTI-DATE-20261012-01`、`LIST-PAGE-SIZE-SCROLL-20261012-01`，2026-10-12。当前运行状态仍以中央 `docs/PROJECT_FACTS.md` 为准。

## 使用行为

- 商机经营的部门、成员均支持多选。同一维度取并集，部门与成员同时选择取交集；成员候选来自已选部门，缩小部门范围时清理不匹配的成员。未选择代表不额外限定，不能扩大账号授权。
- 增加“商机创建日期／预计关单日期＋起止日期”，支持同一天、跨月和跨年。起止日均包含在内；创建日期使用业务创建日期，只有季度信息的预计关单记录不推算为某一天。
- 季度控件明确为“财务期间”，仅决定季度确收、回款字段的期间，与商机日期条件独立。
- 保存方案会保留多选与日期条件；商机看板、再次编辑使用同一份配置。旧的单选方案继续读取，发生范围编辑时再转换为多选字段。
- 主列表统一提供每页 10／20／50／100 条，默认 20 条，按账号和列表保存数字偏好。筛选、页大小变动回第一页。商机使用真实服务端 offset／limit，任务沿用已有 20 条游标接口按需补齐当前页，失败可重试。
- 主列表按所选行数自然展开，由页面主体纵向滚动；宽表保留横向滚动。覆盖任务、客户经营、商机经营及看板、客户申请历史、商机创建历史、跟进记录、风险、知识库、经营明细、普通工作台表格和我的报告。弹窗、抽屉及会议逐页展示保持其独立交互。
- 窄窗口主列表末尾预留底部导航和账号按钮空间，使分页区可滚动至完全可操作的位置。

## 后端配套

依赖 Backend `07e4efe3a1287ed10d7444f1998237d19d8ea846` 在实际 9bb 运行底座上的最小配套覆盖，不能只部署前端。契约保持 `schema_version: 2`：

```json
{
  "scope": {"kind": "authorized", "team_ids": [], "owner_ids": []},
  "date_filter": {"field": "business_created_on", "start": "2026-10-01", "end": "2026-10-12"},
  "period": {"kind": "current_quarter"}
}
```

`date_filter` 可为 null／省略，field 也可为 `expected_close_date`。旧 team_id／owner_id 读取兼容；未知／越权选项仍由后端拒绝，前端不静默扩张范围。分页数量不写入业务方案，只作为本机账号界面偏好。

## 检查与交付边界

- 构建保持 32 页、57 组件，496 个共享小程序文件校验未变化。
- 定向单测：商机配置、日期与范围、分页参数及工作报告既有链路 103／103 通过。
- 列表参数与既有客户／历史／任务单测 29／29 通过（与上组有 3 项分页测试重合）。
- 商机隔离浏览器 11／11 通过，覆盖旧方案、保存回读、部门成员多选、跨年关单日期、独立财务季度、拖动列、权限失败清空和三档窗口分页。
- 列表隔离浏览器 3／3 通过（1440／1024／600px），覆盖任务 20→50→翻页→10、账号偏好隔离、选择 100 时分页失败停止与手动恢复，以及客户 20→50→第二页和自然滚动。
- 浏览器测试使用内存接口样例，无真实业务 API／模型调用，不能视为真实数据库验收。
- 首轮 600px 检查发现固定账号按钮遮挡分页，已修复主列表滚动安全区；保留失败原因，不使用强制点击掩盖。
- 最终候选的源码提交、构建清单和浏览器测试数量在交付清单登记。LOCAL 切换由总控按固定前后端制品执行，然后用普通账号验证范围、保存回读与分页；本源码说明不代表已切本地或线上发布。

```sh
SALES_WEB_DIST_DIR=.runtime/opp-multi-date-20261012/dist npm run build
SALES_WEB_DIST_DIR=.runtime/opp-multi-date-20261012/dist node --test tests/opportunity-workspace.test.mjs tests/opportunity-scope-date.test.mjs tests/list-pagination.test.mjs tests/work-report*.test.mjs
PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs SALES_WEB_DIST_DIR=.runtime/opp-multi-date-20261012/dist node --test tests/opportunity-workspace.browser.mjs tests/list-pagination.browser.mjs
```
