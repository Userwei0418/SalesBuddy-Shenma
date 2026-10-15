# 公司任务管理查询接口

两个接口均为只读，沿用 `task.read` 和现有任务 RLS。`all` 指当前授权范围，不是无条件全公司。原 `/api/v1/tasks` 及小程序列表语义保持不变。

| 接口 | 返回 |
| --- | --- |
| `GET /api/v1/tasks/management` | 当前页任务、完整筛选范围统计、分页信息、统计时间 |
| `GET /api/v1/tasks/management/owners` | 同一筛选下任务的负责人目录、无负责人任务数、分页信息 |

两个接口在单次请求内使用 `readonly`、`repeatable_read` 事务，使统计与该页数据共享数据库快照。不同请求之间仍会反映最新变动，不承诺连续翻页期间冻结全部数据。

## 查询参数

| 参数 | 规则 |
| --- | --- |
| `scope` | `all`（默认）或 `mine`；后者包含本人发起、负责/协作或为候选人的任务 |
| `status` | `pending_confirm`、`pending_execution`、`in_progress`、`pending_review`、`completed`、`deferred`、`cancelled` |
| `overdue` | 默认 `false`；为 `true` 时，只保留已过截止、且未完成/取消的任务 |
| `member_id` | 当前负责人 UUID；历史多负责人任务任一当前 owner 均可命中 |
| `unassigned` | 当前无 owner；指定人待接受仍有 owner，不算无负责人；不能与 `member_id` 同时指定 |
| `date_from` / `date_to` | 北京日期，首尾均包含，按安排区间相交过滤；不只查截止日 |
| `batch_id` | 已正式派发批次 UUID；不存在或无权访问返回空结果 |
| `undated` | `include`（默认）、`exclude`、`only`；当前正式任务数据库要求截止日期非空，正常情况下无日期计数为零 |
| `page_size` / `offset` | 默认 50 / 0；每页 1–100，offset 非负 |
| `q` | 仅负责人目录支持，按可见负责人姓名或账号搜索；不从全公司目录检索 |

不合法状态、倒置日期及冲突参数返回 422。缺少 `task.read` 返回 403；有读取权限但筛选范围不可见返回空结果。

## 返回字段

`TaskManagementPage` 返回 `items`、`summary`、`has_more`、`next_offset`、`as_of`。列表按截止日期升序、创建时间降序、任务 ID 稳定排序。`summary` 统计所有筛选结果，与当前页大小无关；历史多人任务只算一次。

统计字段为：`total`、`pending_count`（未完成，包含待验收）、`pending_review_count`、`overdue_count`、`completed_count`、`cancelled_count`、`undated_count`。

每项保留现有任务详情/动作字段，增加：

| 字段 | 含义 |
| --- | --- |
| `owner_id` / `owner_name` | 当前 owner 按 ID 稳定排序的首项，仅兼容单负责人显示 |
| `owners` / `owner_count` | 所有当前 owner；每人含 `id/name/account_code/active/account_status`；时间线应按所有 owner 分组 |
| `owner_active` / `owner_account_status` | 首项负责人账号状态；历史停用人员仍可筛选 |
| `batch_id` / `batch_name` / `meeting_date` | 可见的已派发批次头；不返回周会原文、草稿和不可见任务 |
| `dispatched_at` | 批次正式确认时间，非批次任务为 null |
| `schedule_start_at` / `start_kind` | 已派发批次用 `confirmed_at`（`dispatched`），其他用任务 `created_at`（`created`） |
| `schedule_invalid` | 截止早于上述安排时间；展示异常，不自动改写日期 |

`accepted_at` 仍是接收事实，不作为实际开工日期。安排区间不表示工时、实际执行时长或完成百分比。任务创建/派发至截止日期用于时间线；异常倒置区间仍可被日期查询找到并明确标记。

负责人目录返回 `TaskManagementOwnerPage`，每项含上述人员字段和 `task_count`，并提供 `unassigned_count`、分页信息、`as_of`。目录仅从可见任务生成，调用者无需 `task.create_*` 或 `organization.read`。历史多人任务会计入每个负责人的任务数，因此按人相加可大于去重后的总任务数。

OpenAPI 响应模型由 `contracts/task_management.py` 定义并生成到 `openapi/openapi.yaml`，详情已有字段通过 `extra=allow` 保留。

## 校验与边界

专项隔离 PostgreSQL 验证入口：`backend/tests/system/verify_task_management_postgres.py`，依赖私有测试集群 `PGHOST/PGUSER`。它创建随机数据库，应用完整 V195 迁移与规范运行权限，使用非超级用户、非 BYPASSRLS 角色，运行新管理接口及已有任务分页/权限回归，完成后销毁临时数据库。

覆盖全筛选统计/翻页、日期相交、停用负责人、只读权限、跨租户、历史多 owner、转交后当前责任、批次安全头、取消后逾期计数、HTTP 参数/授权及实际只读一致性快照。本地证据不替代生产验收。

批次头复用已有安全摘要函数；不指定批次时会读取可见已确认批次的任务映射。当前没有新增物化统计或额外索引，尚未进行多年大量周会批次规模的性能压测；这不影响权限隔离，但需随实际数据量观察查询耗时。
