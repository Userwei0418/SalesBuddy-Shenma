# 智能体绑定保全与恢复

2026-09-13，FDE 当前库验收发现：三次拜访 AI 成功来自原 API 直调，没有中台尝试。核对 Worker 实际 Settings 后确认，已开启的六项既有能力缺少 binding。

## 原因和修复

首次三类建议部署只列出部分 EnvironmentFile，合并脚本在不完整输入上生成统一绑定文件。统一文件加载在最后，覆盖了六项原有绑定。旧的 `unrelated_capabilities_preserved` 仅证明输入内条目保留，不能证明继承输入完整。

- `inherit_service_environment.py` 从 `systemctl show ... EnvironmentFiles` 读取文件名与加载顺序，由 systemd 加载完整配置；不读取或打印凭证文件。
- `prepare_advice_runtime.py` 在写入前验证每个已启用 rollout 都有有效 binding；缺失则终止，不静默退为直调。
- `recover_agent_bindings.py` 只执行经过审阅的追加恢复计划。来源、当前值、目标值均有摘要校验；当前配置发生变化时拒绝覆盖；重复执行返回已恢复。
- 常规 `deploy-forward-release.sh` 不改 EnvironmentFile 或绑定内容。停服务前及启动后，都分别继承 API/Worker 实际文件顺序执行 `verify_agent_runtime.py`，校验有效绑定完整性及两服务最终元数据摘要一致、发布前后摘要不变。两服务文件名单允许不同，Worker 专有适配器密钥不作为 API 校验条件；每个服务自己的加载文件名单仍须前后不变。

## 本次恢复范围

仅补回既有批准的 `battle_map_review`、`opportunity_draft`、`personal_risks`、`visit_entry`、`today_tasks`、`operating_report`。保留当前所有其他 binding，包括三类建议；不改变 rollout，ChatBI 仍关闭，语音转写/合成仍直调。

公开来源为 [原六项配置证据](evidence/operating-report-business-20260912/runtime-bindings.json)。该元数据已逐项与服务器原配置加载结果比对。恢复计划及执行结果保存在仓库外本次验收目录，不包含 API Key。

中台协作会话于 2026-09-13 05:58:18 +08:00 使用现有 CLI 授权只读核对，六个 Agent 均 published=true；前五项发布快照与原绑定相同。经营总结当前为 `glm-5.3-flash` / `01a094da-43ed-7c80-a124-73e537e08355`，不同于原绑定中的 `01a094ae-f0c9-76c0-95a1-9cd8f6188379`。本次恢复不修改 Agent 发布或提示词，也不把 expected_snapshot 当成执行证据。此类 filtered_facts 接入的实际采用来源以同一次 run 的 provider_attempt、校验结果和业务收据为准。

## 操作规程

1. 获取部署锁，核对当前 release/REVISION、计划摘要及实际 EnvironmentFile 顺序；先用完整继承器执行恢复 CLI 的不带 `--apply` 检查。
2. 停 API 再停 Worker，确认没有 running 任务；通过继承器执行恢复 CLI，传入 `--plan`、`--source`、新的 `--evidence` 目录和 `--apply`。
3. 工具原子保存非秘密绑定备份与 rollout 摘要，只替换生成的统一绑定文件。它不读取凭证文件、不改原六项凭证、不改数据库。
4. 使用新的 transient unit 重新继承配置，重复无写检查应返回 `already_applied=true`；核对 rollout 摘要不变、六项与三类建议均有效。
5. 启动 API/Worker，核对实际 release 目录及公网 live/ready。逐能力做真实业务验收，分别记录中台直出、失败后兜底、契约接受和业务结果，不把 HTTP 200 等同成功。

若计划不一致，在写入前停止；若写入后验证失败，保留运行证据，使用同一部署锁处理恢复，不能临时删减校验或调整无关发布配置。

本文件记录维护机制与范围。是否已执行、实际调用结果及待验界面以 [FDE 验收记录](../../docs/FDE_ACCEPTANCE_20260913.md) 为准。
