# 后台作业执行容量与验收

本配置影响 AI／文件任务的排队时间，不改变普通列表 API 的查询口径、权限或数据源。部署需先完成 V078，再运行新版 Worker；原二参 `ops.claim_job(text, integer)` 继续可用，但所有消费者应升级为新版才能获得类型隔离。

## 执行规则

每个 Worker 进程维护三个互不借用的执行组：

| 执行组 | 任务 | 默认槽位 | 配置 |
|---|---|---:|---|
| 文件导入 | `visit.import`，文档解析及长音频转写 | 1 | `WORKER_IMPORT_CONCURRENCY` |
| 交互 AI | `agent.run`、`business.advice` | 2 | `WORKER_INTERACTIVE_CONCURRENCY` |
| 后台复盘 | `sales_competency.review`、`battle_map.review`及其他类型 | 1 | `WORKER_REVIEW_CONCURRENCY` |

一个槽位完整执行并处理终态后才再次领取；没有内存待执行队列，也不会为了分流先占用租约。类型条件在数据库回收和领取前执行，组内保留原任务优先级、可执行时间及创建顺序。未知类型进入后台复盘组，沿用不支持类型的终态失败记录，避免静默积压。

每组可设置 1–8 个槽，总和最多 16。该上限是配置防误设边界，不代表已经通过 16 路外部模型容量验收。当前建议维持默认合计 4；增加进程数会成倍增加整体槽位，需同时评估模型配额、数据库连接总预算和机器资源。

Worker 使用独立进程连接池，`WORKER_DATABASE_MAX_POOL_SIZE` 默认 8，允许 4–32；不改 API 的 `DATABASE_MAX_POOL_SIZE`。模型与音频响应等待期间不持续占用业务事务连接；心跳、维护任务和结果写入共用 Worker 的有界连接池。全机连接预算仍需加上 API 每个进程及其他服务，不能将“Worker 8”理解为整个系统只有 8 条连接。

## 租约、取消与重启

每次领取生成独立 lease token；每个执行槽的 Actor 与 JobLease 用任务上下文隔离。原有数据库事务栅栏、心跳、结果回执及失败重试规则保留。心跳失败时取消对应处理器，旧租约不能再提交结果或终态，也不会停止其他组的作业。

SIGTERM 同时取消所有执行槽和心跳，再关闭日志与连接池。中断的任务不被误记为业务失败，也不强制释放仍可能提交的租约；到期后由同一领取函数恢复，已提交结果通过 `ops.job_effect` 等原完成证据回放成功。耗尽重试次数仍进入死信。systemd 停止超时为 30 秒，硬中断后的恢复沿用同一租约机制。

任务身份载荷不完整或无效时，不构造默认管理员执行。`ops.reject_invalid_job_actor`仅接受真实任务ID和当前租约，锁定数据库任务行并使用它的工作空间、类型及关联记录，将未完成的导入/Agent/建议/能力复盘与队列同步终止。导入显示失败后可由原上传者重新发起；已完成结果和回执保留，租约失效则整个终态事务回滚。作战地图已确认象限不被异常任务清空。新函数权限精确继承原领取入口，不新增泛化的业务写入权限。

## 观察指标

Worker 日志分别记录 `worker_job_started` 和 `worker_job_ended`：

- `queue_wait_ms`：本次可执行时间 `available_at` 到实际领取的间隔，排除预约和重试退避的计划等待。
- `execution_ms`：当前执行尝试从领取后开始，到确认结果、租约丢失、失败或取消的时间。
- 任务 ID、类型、尝试次数、执行结果用于对应队列记录；日志不包含拜访正文或密钥。

这些记录输出到 Worker 服务日志。真实模型响应慢与任务排队慢应分别统计；默认槽位保证长导入不能占用交互槽，不承诺外部模型响应的固定完成时间。

## 可复现验证

- `python -m pytest backend/tests/test_worker_scheduling.py backend/tests/test_worker_maintenance.py -q`：真实调度控制流，队列／外部处理器替身；验证长导入期间短任务启动、容量不预占、Actor／租约隔离、失租取消及停止。
- `python -m pytest backend/tests/integration/test_typed_job_claim.py -q`：配置 `SALES_TEST_DATABASE_URL` 后，回滚事务中运行实际领取 SQL，验证过滤、回收、退避、回执和旧租约栅栏；未配置时明确跳过。
- `python backend/tests/system/verify_jobs_postgres.py`：CI PostgreSQL 中创建临时数据库，迁移到最新版本，运行真实 Worker 进程、连接池、并发领取、SIGTERM 与 SIGKILL 恢复。外部付费处理器替为数据库探针，不调用模型。总执行有 120 秒界限，finally 仅终止本次子进程并删除本次 `salegent_verify_*` 数据库；CI 外层另限 3 分钟。

前两层测试及 CI 故障注入不等于生产外部模型成功率或 50–100 人压力测试。发布后需用授权原账号分别复核真实任务排队、模型完成、人工确认与页面状态。

## 同批认证配置约束

开发默认 `AUTH_MODE=password`，只有显式 `AUTH_MODE=demo` 才能开启开发演示登录。`APP_ENV=production` 必须显式配置 `AUTH_MODE=password`；缺失、demo、未知值在 API／Worker 启动前失败。生产仍需有效登录签名配置。

`deploy-forward-release.sh` 在停机前分别继承 API 和 Worker 的有效环境，执行 `verify_auth_runtime.py` 并比较非敏感结果。该生产发布预检强制要求 `APP_ENV=production` 与 `AUTH_MODE=password` 同时成立；通用运行时允许的开发／测试 demo 配置不能通过生产发布预检。该检查继承 `Environment`、有序 `EnvironmentFile`、`PassEnvironment` 和最终 `UnsetEnvironment`。直接环境值暂存在 `/run` 的私有 0600 文件，检查结束自动删除；原文件仍由 systemd 读取，值不放入命令行或输出。无法完整读取元数据、按值精确删除形式的 `UnsetEnvironment=NAME=value` 均在停机前明确拒绝，避免预检与实际服务不一致。

该检查不读取或输出原 EnvironmentFile 内容，不生成／修改账号，不重置密码，也不替代部署人员明确设置 `AUTH_MODE=password`。

已有环境升级时，先回读两个服务各自的有序配置文件列表，再追加只含 `AUTH_MODE=password` 的独立配置文件。不要用 API 的文件列表覆盖 Worker；两者的智能体绑定文件可能不同。追加后通过 `daemon-reload` 回读顺序，确认原列表完整保留，再执行上述有效环境预检；预检通过后才进入发布停机和切换。

环境探针默认最多运行 120 秒，可用 `--max-runtime` 在 1–3600 秒内指定。systemd 同时限制服务运行时间与 5 秒停止期限，客户端另有上限；超时会终止整个探针进程组并清理临时环境文件。不能对需要常驻的 API 服务使用等待退出的探针命令代替健康检查。

验证环境继承可运行 `python backend/deploy/verify_service_environment.py`（Linux root）：仅自建短时探针服务和私有虚构配置，比较原服务与继承检查的环境布尔断言，不修改现有服务或配置。V078 的客户默认权限兼容可运行 `python database/tests/verify_typed_claim_acl_postgres.py`：在隔离数据库模拟额外默认函数授权，验证新旧领取函数的 owner、EXECUTE、grant option 完全一致，并清理全部探针角色。
