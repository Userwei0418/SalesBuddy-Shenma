# 旧生产结构接管：隔离 systemd 演练

在源码仓库根目录执行（需要已运行的 Linux Docker engine）：

```sh
python3 backend/tests/system/verify_legacy_adoption_systemd.py \
  --output /tmp/salesbuddy-adoption-systemd-evidence
```

`--output` 必须为尚不存在、位于源码仓库之外的目录。默认从当前完整 HEAD 制作候选产品包，也可用 `--revision <完整 SHA>` 指定已提交候选。生成过程中使用临时干净 clone，不带入工作区未提交内容；harness 自身另外记录 SHA256。

启动前核验 Docker context 必须使用本机 Unix socket，拒绝远程 TCP/SSH daemon 和 `DOCKER_HOST` / `DOCKER_CONTEXT` 环境覆盖。后续命令固定使用已核验的 context，不修改用户默认 context。

- 系统镜像基于现有 `salesbuddy-local-ci:ubuntu24-python312-node22-pg16`，增加真正的 systemd。依赖准备阶段允许下载包。
- 演练阶段是新的 `network=none` 容器，使用独立 cgroup、临时 `/run`，没有宿主挂载、发布端口和生产配置。仅该容器使用 systemd 所需的 privileged 权限；不会进入或修改 Colima VM 的系统服务。
- 容器内新建 PG16 集群和两家合成公司，复用 V152 升级 fixture。维护连接必须为 Unix peer；四个独立运行身份必须通过 SCRAM 密码认证，错误密码必须被拒绝。
- 真实启动历史 `5d55f49` API、普通 Worker、飞书 Worker，验证 HTTP revision 和 `/proc` 工作目录，再执行 prepare → stop → backup/真实恢复校验 → migrate。
- 原服务包含与线上同名的较晚覆盖文件 `release-runtime.conf`、`zz-python-module.conf`、`zzzz-runtime-credentials.conf`。验收 systemd 最终生效的 ExecStart、WorkingDirectory、EnvironmentFiles 以及真实进程环境，不能仅凭新增覆盖文件存在就算切换成功。
- 数据库提交后，子进程在替换 `current` 的持久化检查点退出。验证维护 guard 阻挡误启动、resume 使用原密码继续、平台首管理员未创建时拒绝 start，然后 bootstrap → start。
- 回读新 HTTP revision、实际进程命令/工作目录、三服务状态、四个数据库连接、旧身份退役、原环境文件/密钥不变、RELEASES 未知字段保留；再次 start 不得重启进程。

历史依赖存在一个已知重建差异：`5d55f49` 的 pyproject 已声明 `pypinyin==0.55.0`，归档 lock 却漏了这一包。演练按原 lock `--frozen` 重建，再按声明固定版本和候选 lock 的 wheel hash 补装 pypinyin，原源码与 lock 均不改。新候选始终执行 `uv sync --locked`。该演练不证明线上旧 `.venv` 与重建环境逐字相同。

输出 `result.json`、`provenance.json`、`cleanup.json` 和私密日志。失败明确返回非零，不降级为模拟 systemctl 或仅数据库通过。结束时移除本轮容器与临时准备镜像，并回读确认已移除；清理未能确认时整体不得记为通过。基础 systemd 镜像作为构建缓存保留。结果仅证明本地合成数据下的接管与恢复，不代表生产部署、真实飞书/AI 联调、小程序体验版验收或长期稳定性验证。
