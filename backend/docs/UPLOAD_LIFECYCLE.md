# 拜访材料上传与音频资源生命周期

本实现保持 `POST /api/v1/visit-imports` 的 202 返回、状态查询、本人重试及归档 `source_import_id` 契约。文档上限 20 MB、录音上限 100 MB，上传前与登记前均检查 `visit.create`。原件仍使用本地存储，没有对象存储迁移或数据库结构变更。

## 职责与交接

- `api/visit_imports.py` 仅承接 HTTP 输入和错误映射。
- `VisitUploadService` 负责验证、上传流程及登记。`LocalVisitImportStorage` / `LocalVisitUpload` 负责本次文件的所有权。
- `repositories.visit_imports.create_import` 沿用原有同事务写入 `activity.visit_import` 与 `ops.job`。
- Worker 领取登记后的任务，保留原件供失败重试；处理成功后归档引用同一导入记录。

文件先以 UUID 加 `.part`、独占创建、0600 权限写入。按 1 MB 分块写盘，完整写入后 flush/fsync；通过同文件系统硬链接独占发布完整文件，再移除 `.part` 并 fsync 目录。硬链接避免覆盖意外同名文件，不会暴露尚未完整写入的正式文件。

本次资源阶段为 `receiving → ready → registering → registered`。登记事务体结束前的异常或取消会回滚写入并清理本次文件；事务体正常返回、即将发出 COMMIT 后，客户端收到异常不代表数据库没有提交，因此保留原件，并记录 `visit_upload_commit_unknown` 与 `import_id`。不会因取消而删除已登记、正在处理、失败待重试或已成功的原件。清理错误记录 `visit_upload_cleanup_failed`，保留原始业务异常或取消。

## 取消、超时与边界

`async_resources.file_io` 对有限大小的本地文件操作使用线程执行；取消时先等待本次线程操作结束，再允许关闭或删除文件。这避免线程继续向已释放的文件或目录写入。操作系统或文件系统本身卡住时，Python 无法安全终止线程，取消可能等待内核 I/O 返回；实现不会以遗弃线程伪装成安全完成。

短录音与长录音共用 `integrations.ffmpeg.run_ffmpeg`。原有转换预算分别为 45 秒与 120 秒。创建进程的返回边界、转换过程取消、超时均保留进程所有权，终止并等待退出；终止后的通信与回收预算为 5 秒。回收异常留系统错误日志，原异常继续传播。模型请求不受这一资源清理 shield 保护。

FastAPI 负责关闭 multipart `UploadFile` 本身的临时卷宗；服务负责其新写入的持久原件，音频转换函数负责各自 `TemporaryDirectory`。三者不能视为同一份临时文件。

前端收到 202 后离页只停止轮询，后台任务继续；当前 `removeFile` 仅移除本地引用。此变更不新增服务端删除或取消导入接口。

## 提交不明与硬退出的后续核对

本批没有自动扫描或删除上传文件。`SIGKILL`、磁盘故障可能留下本次 `.part`；COMMIT 结果不明会有意保留完整原件。后续核对须使用日志中的 `import_id`，在数据库健康且具有该工作空间完整登记可见性时检查导入与队列记录。RLS 隐藏、查询失败、单次查不到记录，都不是允许删除的证据。

未来若实现回收器，需加入活动上传锁、与登记共用的数据库协调锁、可靠的引用检查及年龄窗口，并单独验证进程硬退出与提交竞争。不能按扩展名或“失败状态”直接删除现有原件。本批不回收历史文件，也不改变正式原件保留政策。

## 验证入口和当前证据

- `python -m pytest backend/tests/test_visit_upload_lifecycle.py backend/tests/test_audio_lifecycle.py -q`：真实临时文件、实际本地子进程；覆盖取消、提交边界、碰撞、权限变化、大小限制及 HTTP 202。
- `python backend/tests/system/verify_audio_lifecycle.py`：需要 `/usr/bin/ffmpeg`。可在独立临时目录对候选音频模块使用 `--overlay`，不写入运行中的版本。短 WAV 与长 WAV 转换、取消、超时、进程创建返回边界取消共 5 项；无数据库/模型请求。
- `python backend/tests/system/run_integration_postgres.py`：现有 CI 集成入口会收集 `integration/test_upload_lifecycle.py`。该测试在临时验收数据库内再创建独立数据库，执行 `system/verify_upload_lifecycle_postgres.py` 的真实 COMMIT 故障、Worker、归档、租约回收及本人重试场景，然后销毁该独立数据库。
- 也可用测试 PostgreSQL 管理连接的 `PGHOST` / `PGUSER` 单独执行 `python backend/tests/system/verify_upload_lifecycle_postgres.py`。需要创建临时数据库权限；禁止使用生产配置或把造数/清理指向业务库。

2026-09-14 本地完整离线回归已通过（1212 项，含上传发布边界取消用例）。实际 Linux ffmpeg 的上述 5 项已通过，模型请求 0、数据库请求 0，仅使用独立 `/tmp`。真实 PostgreSQL 场景已编写并接入 CI，但此隔离分支提交时尚未执行，不将本地模拟事务作为真实提交验收证据。
