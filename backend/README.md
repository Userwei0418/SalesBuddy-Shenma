# Raccoon SalesBuddy后端

当前实现为模块化单体 API + 独立 Worker，业务数据存于 PostgreSQL。运行状态见 [CURRENT_STATUS](../docs/CURRENT_STATUS.md)，完整职责见 [维护入口](../docs/BACKEND_MAINTENANCE_20260910.md)。

## 实际链路

- API：FastAPI，前缀 `/api/v1`；线上仅监听回环地址，HTTPS 由 Nginx 代理。
- Worker：PostgreSQL ops.job 队列，带租约、心跳、过期回收和完成回执；与 API 使用同一 release 的独立运行时。
- 权限：服务端身份和 RLS 共同约束。客户由运营建档、销售申请认领并由运营审批；唯一当前归属与历史访问关系分开。销售商机与FDE协助/客户全景使用明确的不同读取范围。
- 文件：当前拜访上传保存到服务器持久目录，默认 `/var/lib/sales-backend/visit-imports`；导入任务、文字和转写产物存数据库。对象存储尚未接入。
- AI/ASR：业务智能体通过中台配置调用，保留受控直调兜底；语音等简单能力有独立适配器。实际绑定、直接成功和兜底验收见CURRENT_STATUS，不因接口存在推定调用成功。
- 登录：后台开通账号、分配角色和部门，使用账号密码及访问/刷新令牌；正式微信身份/公司SSO仍需单独对接。
- 前端：通过 utils/apiClient.js 连接远程 API；不存在 BI 样本开关或经营 Mock 降级。

## 源码与契约

| 入口 | 内容 |
|---|---|
| src/sales_backend/api/ | HTTP 参数、身份与事务边界 |
| contracts/、domain/、services/、repositories/ | DTO与版本契约、纯规则、业务流程、持久化 |
| services/agent_run/、integrations/ | 模型事实装载、提示词、候选校验、外部接口 |
| openapi/openapi.yaml | 从应用导出的实际 API 契约 |
| tests/、tests/integration/、tests/system/ | 单元与契约、SQL集成、隔离库安装及故障验证 |
| scripts/acceptance/ | 会真实调用 API/模型并写入演示事实的显式验收工具 |

客户/商机详情的完整统计与分页历史见[详情读模型](../docs/DETAIL_READ_MODELS_20260914.md)。当前架构、API 和 AI 流程分别见 [架构](docs/01-backend-architecture.md)、[接口](docs/02-api-design.md)、[Agent](docs/03-agent-architecture.md)。维护业务字段须同步唯一契约和向前迁移，不另造页面专用事实表。

`GET /api/v1/notifications` 在当前收件人 RLS 范围内先分页，再为 `customer_claim` 通知补充 `payload.customer_name`；名称来自受控客户引用，不能读取时为 null，不授予客户历史访问权限。原 `title`、`body`、事件编号和已读状态保持原契约；前端渲染后才调用已读接口。认领结果通知及页面刷新范围见[链路复验记录](../docs/CHAIN_REVIEW_20260915.md)。

## 开发和检查

前端联调无需启动本地后端。需要开发后端时，在本目录使用 Python 3.12+：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
# 由受控环境注入所需配置后再启动；程序不会自动读取当前目录的 .env 文件。
.venv/bin/python -m uvicorn sales_backend.main:app --reload --port 8080
```

密钥和数据库口令不写进命令示例或仓库。线上由 systemd EnvironmentFile 注入。检查命令和隔离库方式见 [验证指南](../docs/VERIFICATION_RUNBOOK.md)；不设置专用测试库而直接跑 integration 会跳过测试，不能计为 SQL 验证通过。

数据库唯一入口位于仓库根部 [database/](../database/README.md)；本目录 database/ 只是说明目录，没有第二套迁移入口。

拜访材料上传的文件交接、取消清理、提交结果不明及验收入口见 [上传生命周期](docs/UPLOAD_LIFECYCLE.md)。
