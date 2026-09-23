# 神码运行版本验收记录

## 最新运行版本

销售已部署 `7c7a376cc36873a7ba122c2663f075c2dfc85102`，对应 PR #13 完整 CI 通过的审核版本；合并后的 main 为 `4e7dd6ab590030b63948526cf6fbf1b4a330a08f`，两者源码树一致。升级包含能力复盘事实约束，不改变数据库或依赖；V125 保持不变。

切换后公网版本接口、readiness 200、API/Worker、管理员登录/读取自身/退出、运行角色权限和密钥读取均已复验通过。旧 d287330 版本保留；升级前最近可用快照为 `/var/backups/shenma-sales/20260923T185704Z`，恢复演练记录见 OPERATIONS.md。服务器版本切换回执为 `/var/lib/shenma-provision/reviewed-release-activation.json`。

仍仅开放原有四项管理员试点，不自动扩大未通过语义验收的能力。正式可信证书、客户 AppID 和完整业务端到端验收未完成。

## 首次中台客户端部署记录

- 销售运行版本：`d287330a1f04317c634147e5792362ebcd4234de`，PR #9 完整 CI 通过后切换。
- API、Worker 正常，readiness 为 ok；公网版本接口核验一致，数据库仍为 V125。
- 升级前备份：`/var/backups/shenma-sales/20260923T181733Z`；旧版本保留，可回退代码。
- 客户经营建议的独立 Agent ID、应用 Key、证书信任和预期快照已配置；试用只对初始部署管理员开放 48 小时。
- 部署管理员已走正常首次改密流程。当前随机口令仍只在服务器 `/var/lib/shenma-provision/initial-admin.json` 内由 root 保管；交接时由客户再自行修改。
- 管理员登录后通过 HTTP 提交测试，真实 Worker 调用中台成功，完整 JSON 回执耗时 1,841 毫秒，未走备用模型、未写入 CRM 业务记录。
- 运行角色仍为 NOSUPERUSER、NOBYPASSRLS，私有认证数据读取被拒绝；实际服务账号可读取加密钥匙，登录与退出检查通过。

测试回执：`b9a84e82-eb61-4dc8-a711-5a580de4ba6c`。回执明确 `business_acceptance=false`、`runtime_snapshot_verified=false`：这是实际任务链路连通验证，不是完整业务验收或远端执行快照证明。

仍需完成：其余 Agent 接入、登录后的完整业务样例、正式可信证书、客户小程序 AppID 与真机验收。本记录更新了之前文档中“候选代码未部署、业务绑定未启用”的阶段状态。
