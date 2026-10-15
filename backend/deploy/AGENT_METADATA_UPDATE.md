# Agent 元数据增量部署

`prepare_agent_metadata.py` 用于已发布业务 Agent 的运行元数据更新。它只读取 `get_settings()` 中的有效绑定、所选能力的 Agent ID 和 Key 是否存在，以及明确的两个非凭证文件；不打开原始凭证 EnvironmentFiles，不创建 Key，不调用模型，不发布 Agent。

能力选择与边界：

- 可重复传入 `--capability` 选择一个或多个已接入能力。未传时保持原默认：`competency_review`、`operating_report`。拒绝空集合、重复、未知能力及问数；选择顺序不影响共同计划 SHA。
- **已有绑定统一只更新 `expected_snapshot_id`**，包括 `operating_report`、`opportunity_draft` 和已有 `competency_review`。当前 Agent ID 和快照须匹配所选能力目录中 `publication.json` 的已审阅绑定前驱或目标值，其他绑定字段、模型标识和现有 rollout 保持原值。默认使用平台 `previous_snapshot_id`；有明确核验的 `previous_binding_snapshot_id` 时，它替代默认前驱作为唯一旧绑定 CAS，不扩大为“任意旧版本都可升级”的白名单。只接受现有有效的 `filtered_facts` 绑定，不把停用绑定顺带启用。
- `competency_review`、`visit_quality`、`task_import` 保留明确选择的新增通道，必须明确提供灰度参数。pilot 需要 1–20 个指定用户 UUID 和带时区的未来到期时间；只有明确传 `--rollout production` 才全量启用。已有不同灰度规则拒绝覆盖；其他能力缺少绑定一律拒绝创建。
- 更新已有绑定时不需要 rollout 参数，也不会创建缺失的 capability rollout。未恰好选择一项允许新增的能力时传入 rollout 参数会被拒绝，避免让操作者误以为这次会调整灰度。
- 只读取所选能力的发布文件和 typed adapter 字段。`opportunity_draft` 对应现有 `agent_fde_opportunity_*`，`battle_map_review` 对应 `agent_fde_battle_map_*`；不新建一套凭据字段。未选能力和其他工作空间的绑定、灰度及凭据配置保留；当前工作空间问数继续关闭。

## 仅同步已发布的报告与商机候选

两项当前发布文件位于 `backend/agent_platform/{operating_report,opportunity_draft}/publication.json`。完成平台发布及回读，并明确核对两项业务运行绑定的前驱后，分别生成 API/Worker 计划：

```bash
<release>/.venv/bin/python <release>/backend/deploy/inherit_service_environment.py \
  --service sales-api.service --unit agent-plan-api-<batch> \
  -- <release>/.venv/bin/python <release>/backend/deploy/prepare_agent_metadata.py plan \
  --service sales-api.service --workspace <workspace-uuid> \
  --capability operating_report --capability opportunity_draft \
  --output <evidence-parent>/api-plan.json
```

Worker 使用同样参数，将服务、瞬态 unit 和输出文件分别替换为 Worker 对应值。此方式不传 `--rollout`、`--user-id` 或 `--expires-at`，不读取/更新 competency 发布文件，也不改变已上线的 competency 绑定和灰度。下文 apply 命令保持一致，选择集合已纳入共同计划和 SHA，不能用不同选择的 peer-plan 混合执行。更新工具后应重新生成计划，旧版未包含选择集合的计划不可复用。

## 1. 分别获取 API、Worker 的只读计划

`task_import` 的预览和重新整理在 API 同步执行，因此 API 必须配置 `AGENT_FDE_TASK_IMPORT_ID` 和 `AGENT_FDE_TASK_IMPORT_API_KEY`，Worker 可以缺省这两个字段。其他所选能力继续在 Worker 的相关 Handler 执行，Worker 必须配置对应 typed Agent ID 和专用 Key，API 可以缺省。任一服务若提供了可选 ID 也必须匹配发布元数据，不允许只有 Key 没有 ID。不要为了让两服务的凭证存在性相同而复制 Key，不要把 Key 放在命令行、计划或仓库。工具只检查存在性，不生成或分发 Key。

使用本次完整 release 的 Python 和源码。以下是保留的首次新增 competency 示例，占位符需替换为真实路径及已确认的原业务账号 UUID；API 和 Worker 的参数必须完全相同，只有 `--service`、瞬态 unit 名和输出文件不同。已有 competency 只更新快照时应省略这些灰度参数。

```bash
<release>/.venv/bin/python <release>/backend/deploy/inherit_service_environment.py \
  --service sales-api.service --unit agent-plan-api-<batch> \
  -- <release>/.venv/bin/python <release>/backend/deploy/prepare_agent_metadata.py plan \
  --service sales-api.service --workspace <workspace-uuid> \
  --user-id <original-business-user-uuid> --expires-at <future-date-with-timezone> \
  --output <evidence-parent>/api-plan.json

<release>/.venv/bin/python <release>/backend/deploy/inherit_service_environment.py \
  --service sales-worker.service --unit agent-plan-worker-<batch> \
  -- <release>/.venv/bin/python <release>/backend/deploy/prepare_agent_metadata.py plan \
  --service sales-worker.service --workspace <workspace-uuid> \
  --user-id <original-business-user-uuid> --expires-at <future-date-with-timezone> \
  --output <evidence-parent>/worker-plan.json
```

`plan` 就是 dry-run：不会修改运行配置或启停服务，只新建权限 0600 的非凭证计划文件。共同计划 SHA 覆盖能力选择、当前绑定和灰度、拟变更内容、发布元数据、原文件 SHA 及有效元数据摘要。各服务 typed ID / Key 存在性在 `adapter_status` 中单独报告，不纳入必须相同的共同计划，apply 会重新核验实际服务。审阅两份计划，确认共同 SHA 相同，变更只覆盖所选能力，再记录准许执行的 SHA。输出文件已存在时不会覆盖。

## 2. 停止服务，按审阅 SHA 应用

在核心 release 部署完成之后，单独安排这个维护步骤。停止 `sales-api.service` 与 `sales-worker.service`，然后通过同一 EnvironmentFiles 继承入口运行：

```bash
<release>/.venv/bin/python <release>/backend/deploy/inherit_service_environment.py \
  --service sales-api.service --unit agent-metadata-apply-<batch> \
  -- <release>/.venv/bin/python <release>/backend/deploy/prepare_agent_metadata.py apply \
  --plan <evidence-parent>/api-plan.json --peer-plan <evidence-parent>/worker-plan.json \
  --approve-sha256 <reviewed-sha256> --evidence <new-recovery-directory>
```

apply 自己非阻塞持有 `/www/wwwroot/salegent/.deploy.lock`，不能再套一层相同的部署锁。要求 root 维护身份和两个服务均为 inactive；正在运行、锁忙或有效配置变化均拒绝。不要与核心发布脚本并行。

写入前重建计划并比较全部内容，对原绑定文件和原 pilot 文件做 SHA 一致性检查，还会重新通过两个服务的 EnvironmentFiles 获取当时配置。完整绑定/灰度摘要必须相同；所选能力的实际执行服务 typed ID / Key 必须齐备（task_import 为 API，其余为 Worker），另一服务可缺省但不能错配。各服务存在性独立记录在恢复目录的 `service-configuration-before.json`，写后必须各自保持一致。这是重新读取当时配置，不能用早先生成的 Worker 计划代替运行前核对。Key 值由独立受控配置维护，工具只验证存在性。

之后保留完整非凭证计划，复用 `atomic_write` 写入 `/etc/sales-fde-advice-bindings.env`；只有明确新增能力导致灰度变化时才写原 `agent_fde_pilot_path`，否则保留 pilot 文件原字节。文件属主、权限及路径保持。两服务停止期间不会观察到两个文件的中间状态。写后再次核对两个服务的有效摘要；任一写入或核对失败会恢复原文件，不启动服务。机器掉电或进程被强制终止时，需要按恢复目录中计划的 `before.bindings`、`before.pilot` 和路径核对/恢复后再启动；缺少成功 `result.json` 不能视为配置成功。

成功后由运维启动服务，再分别运行 `verify_agent_runtime.py`、健康检查和真实业务验收。工具本身不启动服务、不把元数据配置成功当作 Agent 任务验收成功。

## 本轮绑定前驱核验

2026-09-13 在 `1358c6b` 核心发布后，通过 API/Worker 的有序 EnvironmentFiles 启动只读瞬态核验进程，仅从 typed Settings 输出 ID、快照和 Key 存在性。两个服务的 report 绑定均为：

- Agent ID：`01a09022-d0ec-7703-b59f-0699150d63a7`。
- 当前部署绑定快照：`01a094ae-f0c9-76c0-95a1-9cd8f6188379`。
- 平台最近一次发布的前驱：`01a094da-43ed-7c80-a124-73e537e08355`。
- 本轮目标发布快照：`01a09a6f-6430-7ea6-9ca2-2387b8efb8a1`。

平台发布历史比业务部署快照更新，因此保留 `previous_snapshot_id` 的平台含义，另明确保存实测 `previous_binding_snapshot_id` 作为此次部署的 CAS；Agent ID 仍必须相同。当前绑定若已经发生其他变化，即使变为上述平台前驱也会拒绝，需重新核验和审阅，不能自动取当前值覆盖 CAS。

核验时，API 的 report adapter ID/Key 缺省，Worker 的 ID 正确且 Key 存在，符合现有异步执行架构；两个服务 competency 配置 ID 正确且 Key 存在。瞬态核验反映 systemd 当前配置下新进程会取得的 Settings，不能据此宣称尚未重启的 API/Worker 已加载新配置。

## 验证

`pytest backend/tests/test_prepare_agent_metadata.py -q` 覆盖只读计划、平台前驱与绑定前驱严格 CAS、既有绑定保护、无凭证输出、显式 pilot/production、双服务共同 SHA、按能力执行位置校验必要配置（task_import 的 API 必需/Worker 可缺省，其余反之）且可选配置不可错配、实际配置漂移、并发修改拒绝、停止状态/部署锁、原子写失败和后验失败恢复；另覆盖能力子集 CLI、适配器字段别名、未选能力/灰度/凭据保留、已有 competency 通用 CAS、不在明确新增集合的能力禁止新增、选择排序、发布变动拒绝以及不变 pilot 原字节保留。

## 2026-09-24：明确选择的新公司接入

用户已批准为真实公司接入 11 项既有业务 Agent。新增 `--allow-new-bindings`，必须同时显式提供 `--capability`（可重复）和 `--rollout production` 或有效 pilot 参数。默认行为不变；没有此开关时，仅原有 competency/quality 专用新增入口可创建绑定。

新模式只对选中能力补缺失绑定，要求已有 rollout 与请求一致；已有绑定仍检查 Agent ID、快照前驱与 `filtered_facts` 模式，不启用停用绑定，也不覆盖不同灰度。开关、能力集合、全量前后配置均进入共同计划 SHA，apply 重建核对。问数仍拒绝选择，其他租户配置逐值保留。原子写入与双服务停止、凭据存在性检查、失败恢复规则不变。

本次选择：`visit_entry`、`visit_quality`、`customer_advice`、`opportunity_advice`、`visit_advice`、`battle_map_review`、`opportunity_draft`、`personal_risks`、`today_tasks`、`operating_report`、`competency_review`。业务 workspace 使用运行时核验的真实公司 UUID，不使用中台 FDE Workspace UUID。

发布文件于 2026-09-24 从本人 CLI 的已发布配置回读。`previous_repository_snapshot_id` 仅记录更新前的本地回执，不代表当前业务绑定或平台的直接发布前驱；保留的历史绑定前驱不能作为本次演示公司升级授权。本次创建真实公司缺失绑定，演示公司不变。`expected_snapshot_id` 是配置目标，当前平台协议不回传实际运行快照，仍保持 `runtime_snapshot_verified=false`。

## 2026-09-28：周会任务导入接入

`task_import` 已列入可明确选择的能力；默认能力集合保持不变。新增时用 `--capability task_import` 和已批准的 rollout 参数，仍需对应目录中已发布的 `publication.json`。共享绑定和公司灰度元数据由 API / Worker 同时核验，专用运行 Key 只需交给执行此能力的 API。该变更不要求把 Key 分发给 Worker，也不修改其他公司或能力。

任务导入走 `filtered_facts`，仅接收受控的 `source_line`、`description` 草稿内容；中台失败沿用直接模型与逐行规则兜底。已存在原文批次使用显式重新整理接口，不通过更换原文或日期绕过幂等。重新整理要求 V194 数据库边界，只允许本人尚未人工修改的未派发草稿，版本冲突或 AI 失败均保留原稿，最终派发仍须人工确认。中台协议不回传实际运行快照，不能把发布文件或配置目标写成已验证运行快照。平台和总超时仍使用公司运行配置；此接入没有扩大默认全局时限。
