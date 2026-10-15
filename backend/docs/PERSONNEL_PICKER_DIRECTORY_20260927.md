# 人员选择目录契约（2026-09-27）

本次只增加组织目录元数据及商机稳定负责人 ID 筛选。没有数据库迁移，不修改读取、创建、转交或正式写入权限。团队和成员归属读取当前组织表，不从客户、商机或任务记录反推。

|接口|人员集合|团队集合|默认团队|
|---|---|---|---|
|`GET /directory/members`|原 `items`，保留 `id/name/account_code` 与业务汇总，包含 `team_ids`|原 `teams`|新增 `defaults.team_id`|
|`GET /dashboard/options`|原 `members` 新增 `team_ids`|新增 `teams`；兼容原 `team_groups`|新增 `defaults.team_id`|
|`GET /profile/scope-options`|保留原 `members` 的 `id/display_name/account_code/team_ids`|原 `teams`|修正 `defaults.team_id`，不再取首个团队|
|`GET /fde/scope-options`（画像 FDE 分支）|成员新增 `account_code`；原 `members.team_ids` 收窄为可选团队中的有效 FDE/FDE主管归属|原 `teams`|同样使用当前人可选的有效主团队，缺失返回 `null`|
|`GET /opportunities`|新增 `owner_options`：`id/name/account_code/team_ids`，保留目录角色与团队显示信息|原 `team_options`|新增 `defaults.team_id`|
|`GET /tasks/recipients`|原分页 `items` 新增 `team_ids`|新增 `teams`|新增 `defaults.team_id`|
|`GET /tasks/{id}/reassignment-options`|原任务转交分页 `items` 新增 `team_ids`|新增 `teams`|新增 `defaults.team_id`|

`team_ids` 为当前有效归属；人员同名时用 `id` 识别，用 `account_code` 辅助显示。新元数据中的团队是有效的可选业务叶子团队，保留尚无成员或业务记录的空团队，排除停用、过期、未生效、删除和组织汇总父节点。

`defaults.team_id` 仅在当前人的有效主团队属于本接口可选团队时返回该 ID，否则返回 `null`。默认团队只是人员选择器的初始视图，不代替正式业务筛选，也不变更当前登录身份。

商机新增 UUID 参数 `owner_id`，在原授权商机集合内精确匹配负责人。提供 `owner_id` 时优先使用 ID，兼容旧 `owner` 姓名链接。未知或无权负责人的 ID 不能扩大商机结果；UUID 格式非法返回 422。`owner_options` 来源于 `opportunity.read` 可查看的当前人员目录，即使其没有商机，也可出现在选项中。

任务新建仍检查 `task.create`；转交目录仍逐项检查 `task.coordinate`、该任务的可见性、协调资格及当前状态，不新增 `task.create`、`directory.read` 或组织管理权限要求。通过原入口权限后，接收人及团队范围仍为当前公司的有效组织目录，提交时继续走现有接收人解析和任务权限复核。

验证入口：`PYTHONPATH=backend:backend/src:backend/tests/system python backend/tests/system/verify_personnel_picker_postgres.py`。该入口创建并清理隔离数据库，以非超级、非绕过 RLS 角色运行新增与相关集成测试；不连接生产业务库。

本地验证：最终后端非集成测试 2605 项通过（2 项依赖弃用提示）；隔离 PostgreSQL 定向 40 项通过，覆盖多团队归属、空团队、停用团队、真实主团队、缺失默认、同名不同 ID、旧姓名链接、跨公司隔离及无新建权限的任务转交。OpenAPI/画像/路由定向 25 项通过，Ruff F 检查和 `git diff --check` 通过。OpenAPI 从当前应用重新导出，保留本工作区既有地图恢复接口。

补齐 FDE 画像分支后，隔离 PostgreSQL 同一入口扩大至 43 项全部通过。新增有主团队、无主团队两种情况，验证默认不取首个团队，成员归属不包含无权团队或非 FDE 岗位归属，并验证账号字段和真实 HTTP 响应。此增量的 OpenAPI/FDE 定向 17 项通过，Ruff F 检查通过；没有重复运行上述 2605 项全量。

这些记录仅代表后端本地源码和隔离数据库验证。本次未提交、部署、上传小程序或写入生产业务数据；前端交互与配套运行验收由主任务继续完成。
