# 独立多人待办接口变更

同一次人工派发，为每位负责人建立独立任务。普通创建与经营建议采纳均支持；建议处理统计按建议数，任务统计按任务数。

## 契约

- 新增 `POST /api/v1/tasks/batch`，请求 `{"tasks": [TaskCreate, ...]}`，1–100 项。每项显式指定 assignee_account_code，禁止岗位领取、重复账号（忽略大小写和首尾空白）、不一致的任务内容/执行设置/业务关联。返回 `{"items": [任务详情, ...]}`。
- 同一次提交使用同一 `Idempotency-Key`。整个批次与幂等回执处于一个数据库事务；任何一项校验失败，所有任务、通知和事件回滚。每项复用 TaskService 的现有创建、派发权限和业务对象校验。
- 建议决定接口新增可选 `tasks` 数组；与原 `task` 互斥，仅 adopted 可使用。返回保留 `task`（首条），新增 `tasks`（所有结果）。旧单人请求语义不变。
- 建议读取的每个 suggestion 新增 `tasks`，包含当前身份可见的任务 id/status/assignee_name/assignee_account；task_id 保持首条兼容。普通任务 RLS 仍生效，不因读建议绕过任务权限。
- 来源建议加锁并检查版本，整批任务创建后只更新一次建议决定。统计 SQL 不变。后台活动及 AI 审计增加全部 task_ids，created_tasks 覆盖全部派发结果。

## 数据库与兼容

依赖 `database/migrations/V146__advice_multiple_independent_tasks.sql`：将每建议一个任务的唯一索引换成普通来源索引，保留外键和历史首条字段；更新两个既有审计函数内的任务引用，保留函数签名、权限检查及 ACL。迁移不重写业务数据、不更改授权。

部署顺序：按项目数据库审核流程核验 V146 → 后端 → 前端。旧前端仍可使用单人请求。产生多任务数据后不能直接恢复原唯一索引；如需回滚前端/后端，保留 V146 数据结构，评估旧界面只展示第一条的限制，不删任务迁就旧约束。

## 本地验证

- 后端单元测试 2040 项通过，OpenAPI 从应用重新导出并校验一致。
- 新鲜隔离 PostgreSQL 完整迁移，非超级用户、NOBYPASSRLS 角色下运行 task_batch、business_advice、authorization_tasks：25 项通过。
- 覆盖整批回滚、通知回滚、幂等回放、多角色功能授权、创建/派发独立权限、独立完成、来源建议事务/版本、日常与客户 AI 多选、一次采纳统计、完整审计、无关人员不可见。
- V146 在隔离数据库重复执行通过：两项审计函数的所有者、ACL、安全属性、定义完全不变，来源索引仅一份。
- 同批已记录的 RBAC 大数据量历史查询性能波动属于独立发布门禁，本项不声称修复。

复核命令：`node --test frontend/tests/*.test.js`；`PYTHONPATH=backend/src backend/.venv/bin/python -m pytest backend/tests --ignore=backend/tests/integration -q`；`PYTHONPATH=backend/src backend/.venv/bin/python backend/scripts/export_openapi.py --check`。集成测试由仓库 `backend/tests/system/run_integration_postgres.py` 创建一次性数据库、完整迁移并运行，不连接生产库。

源码未部署，小程序未上传；真实环境验收待整批完成后统一安排。
