# 任务取消入口后端契约（2026-09-27，本地候选）

用户确认错误任务按“取消并保留记录”处理。本次仅补查询字段，不修改任务权限、状态机、数据库或取消写入行为。

## 响应契约

- `GET /api/v1/tasks`：旧列表与带 `tab` 的分页列表，每个 `items[]` 增加 `can_cancel` 布尔值。
- `GET /api/v1/tasks/{task_id}`：顶层增加相同 `can_cancel` 布尔值。
- 现有任务创建、事件处理返回详情，也随详情投影携带该字段。响应仍为原有字典结构，没有增加多余响应 schema。
- 列表和详情共用 `TASK_CAN_CANCEL_SQL`：当前任务的 `task.cancel`、`security.fde_can_coordinate_task` 同时为 true，且状态不为 `completed`、`cancelled`、`pending_review`。
- 客户端应严格判断 `can_cancel === true` 并结合当前会话 `task.cancel`，不从角色名、负责人或创建人自行推导权限；字段缺失不展示取消入口。

提交继续使用 `POST /api/v1/tasks/{task_id}/events`，传 `event_type=cancel`、非空原因和当前 `version_no`，同一次重试复用 Idempotency-Key。服务端在事务中重新检查权限与版本。

## 保留的业务规则

取消保留任务、原负责人、关联对象、取消原因与事件历史；移出未完成统计，不计入已完成，仍可从全部及现有 `rejected` 分类查看。已有“接收人拒绝”与“发起人取消”都使用 `cancelled` 状态。

已完成或已取消任务不能再次取消；待验收任务先按现有流程处理验收/驳回。取消不会撤回 AI 建议采纳、重新打开建议或影响同一建议产生的其他独立任务。没有新增删除、恢复或批量取消接口。

## 验证

唯一工作树 `/Users/zhouwei5/Developer/Raccoon SalesBuddy`，基线 HEAD `7c6e0b9a3810883c751ffdf47964a48a38ac1f0c`；本次在已有地图及选人组件未提交改动上局部增量，未覆盖其他改动。

|验证|结果|
|---|---|
|`test_task_browse.py`、`test_tasks_contract.py`、`test_task_response_rules.py`、`test_openapi_contract.py`|21 passed|
|`tests/system/verify_task_cancellation_postgres.py`|隔离 PostgreSQL：26 passed，0 skipped|
|新增取消集成用例|15 项；其余 11 项为完成验收、既有任务权限、分页与转交目录回归|
|Ruff F / `git diff --check`|通过|

隔离库由既有 runner 创建、迁移并清理；业务断言使用无 superuser / 无 bypass RLS 的临时角色。仅历史状态和第二租户合成夹具短时使用专用测试库 owner，随后恢复受限角色。

覆盖：发起人、普通接收人、主管/总经理/运营/管理员、个人取消禁用、独立协调禁用、七种任务状态、跨租户不可见、HTTP 两种列表与详情字段、旧版本 409、幂等重放、原负责人/历史/原因保留、取消后待办减少/已完成不变、实际收件人可见且只生成一条取消通知。

未部署、未提交、未上传小程序、未操作生产任务、未调用模型。隔离 SQL/HTTP 验证不等于小程序视觉或用户验收；整套本地刷新与页面验收由主任务记录。
