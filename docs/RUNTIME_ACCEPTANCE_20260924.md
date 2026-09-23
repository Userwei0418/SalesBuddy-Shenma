# 神码运行版本验收记录

- 销售运行版本：`d287330a1f04317c634147e5792362ebcd4234de`，PR #9 完整 CI 通过后切换。
- API、Worker 正常，readiness 为 ok；公网版本接口核验一致，数据库仍为 V125。
- 升级前备份：`/var/backups/shenma-sales/20260923T181733Z`；旧版本保留，可回退代码。
- 客户经营建议的独立 Agent ID、应用 Key、证书信任和预期快照已配置；试用只对初始部署管理员开放 48 小时。
- 部署管理员已走正常首次改密流程。当前随机口令仍只在服务器 `/var/lib/shenma-provision/initial-admin.json` 内由 root 保管；交接时由客户再自行修改。
- 管理员登录后通过 HTTP 提交测试，真实 Worker 调用中台成功，完整 JSON 回执耗时 1,841 毫秒，未走备用模型、未写入 CRM 业务记录。
- 运行角色仍为 NOSUPERUSER、NOBYPASSRLS，私有认证数据读取被拒绝；实际服务账号可读取加密钥匙，登录与退出检查通过。

测试回执：`b9a84e82-eb61-4dc8-a711-5a580de4ba6c`。回执明确 `business_acceptance=false`、`runtime_snapshot_verified=false`：这是实际任务链路连通验证，不是完整业务验收或远端执行快照证明。

仍需完成：其余 Agent 接入、登录后的完整业务样例、正式可信证书、客户小程序 AppID 与真机验收。本记录更新了之前文档中“候选代码未部署、业务绑定未启用”的阶段状态。
