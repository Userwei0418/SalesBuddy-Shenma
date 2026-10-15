# 客户风险评估验证证据（2026-09-16）

本目录分别保存客户级 `personal_risks` / `surface=customer_risk` 的合成模型验证，以及后续现有演示客户的后台任务回执。两类证据不混用，现有客户的业务原文不提交到仓库。

## 合成模型验证

以下三个 `*-live.json` 和最终 `original-api-final-recheck.json` 文件属于合成输入验证。输入仅含脚本构造的客户、销售与 FDE 拜访，不发送真实客户记录。通过既有 `inherit_service_environment.py --as-service-user` 继承 Worker 环境；未查看、打印或复制原 EnvironmentFile 或凭据。数据库采用现有 `ReadOnlyDatabase`，强制只读、非 superuser、非 bypass-RLS；没有进入业务队列，也没有写业务、模型调用审计或风险表。

| 证据 | 预算风险 | 明确健康 | 结论 |
|---|---:|---:|---|
| `platform-synthetic-live.json` | 6,732 ms | 2,005 ms | 两次 HTTP 200 后均为 `stream_error`，没有业务 JSON；新 surface 中台直出未通过 |
| `original-api-initial-live.json` | 5,895 ms | 4,838 ms | 真实原接口返回了校验规范元数据，严格契约拒绝；暴露后端提示词歧义 |
| `original-api-fixed-live.json` | 1,748 ms | 868 ms | 该批提示词修正后原接口两例通过；发生在最终输出自检补丁前 |
| `original-api-final-recheck.json` | 33,004 ms | 33,003 ms | 最终提示词两例均超时；不能宣称当前提供方可用性验收通过 |

后端提示词修正明确区分业务结果与校验规范，禁止把 `version/schema/evidence_fields/constraints` 当成答案；未放宽结果校验。通过案例均完整列出两条销售/FDE 拜访 ID；预算风险还通过来源 ID、风险枚举及原文连续片段核验。原接口使用真实 `runtime_config`、已发布的 `agent_business.personal_risks` 规则和 `senseaudio-s2-lite`，以总预算 45 秒减去中台预算 12 秒后的 33 秒作为保守剩余预算。

中台没有实际运行版本回执。绑定元数据为 Agent `01a09022-bb1a-7dab-8481-917e0472c528`、snapshot `01a0a584-29ae-7d95-bbac-35adbf8360da`，不能据此宣称这就是请求实际执行版本；证据明确标记 `runtime_snapshot_verified=false`。该批合成请求在两次失败后停止；后续后台任务按正常路由尝试中台，另列任务证据。

这组证据证明新严格契约可被真实原接口完成，不代表完整 Worker 队列、业务落库或大客户 80 条上限输入已做生产验收；JSON 中保留 `full_queue_lifecycle_exercised=false`。队列、RLS、归属变更及并发由独立 PostgreSQL 生命周期测试覆盖。

`platform-prompt-candidate.json` 是未发布候选，只新增明确的 `customer_risk` 分支，并完整保留原提示词作为无该 surface 的分支；尚无模型验收。已发布的 `backend/agent_platform/personal_risks/prompt.json`、平台配置和绑定均保持原样。

复核脚本为 `probe_platform_synthetic.py` 与 `probe_original_api_synthetic.py`，按自身仓库位置定位 `backend/src` 与 `backend/scripts/acceptance/agent_provider_recheck.py`，使用正常 `sales_backend.domain.customer_risk` 导入，不依赖临时文件或本机绝对路径。原接口脚本导入相邻合成输入模块时不会发起中台调用。`--workspace-external-id` 与 `--account-code` 指定现有有效销售身份，仅用于只读解析身份和配置，不创建登录会话、不使用密码、不写入证据；`--output` 必须指定尚不存在的 JSON 路径。

在包含本变更的已审阅源码目录，通过既有服务环境执行，例如（替换源码、Python、既有演示身份与可写输出路径）：

```sh
python3 /path/to/release/backend/deploy/inherit_service_environment.py \
  --service sales-worker.service --unit customer-risk-original-synthetic \
  --as-service-user --max-runtime 100 -- \
  /path/to/release/.venv/bin/python -B \
  /path/to/release/backend/docs/evidence/customer-risk-20260916/probe_original_api_synthetic.py \
  --workspace-external-id '<已授权演示工作空间>' \
  --account-code '<已授权演示账号>' \
  --output /path/writable-by-service-user/original-api-recheck.json
```

平台脚本有相同参数接口，但本次已在两次失败后停止，不能把存档失败证据写成当前候选提示词合格；候选提示词没有发布或执行。上述合成输入与结果不含真实客户事实、账号、workspace、token、password 或 API Key。合成拜访与计划日期固定在本次验收日期，未来重跑应明确这是历史材料并按需另建时效性测试，不能拿新结果覆盖本次证据。

## 后台任务验证

`queue-first-attempt.json` 是 `ee4e14e` 部署后的现有演示客户任务：9 条拜访进入评估，中台流式错误后原接口返回 9 条风险，超过最多 8 条的严格契约。任务失败且未写风险，保留失败回执，不视为成功验收。随后两次只读原接口诊断在 33 秒预算内超时，分别使用原提示词与最终输出自检候选；没有将超时记为通过。

针对这次失败，最终代码在公司指引后明确“审查全部拜访”与“最多列 8 条重点风险”不是同一数量；拒绝时记录固定原因，关联本次失败推理编号，同任务重试不复用上次推理编号。原返回校验没有放宽，没有自动裁剪风险，也没有新增无限模型重试。合成脚本已采用最终自检；历史 JSON 保留对应批次事实，不覆盖为最新版本结果。

`release.json` 保存最终后端 `ee9bd61`、V098、66 条迁移校验、备份与服务健康结果。`queue-final-audit.json` 保存第二次后台任务的无正文审计：中台 12 秒、原接口剩余 33 秒均超时。`runtime.json` 以实际负责人权限回读，断言评估失败/结论未知、队列失败、0 条风险写入、失败推理关联正确、事实覆盖完整且未变、新旧客户详情六维画像一致。

当前没有完整成功的真实客户评估落库证据。不要把部署成功、失败保护通过、历史合成通过或 6/6 演示画像误写成这项业务验收通过；现有演示健康数据早于本次任务，不是失败任务的产物。后续接口恢复后，通过维护 CLI 使用新的 request-id 补评同一客户即可，仍无需改小程序。
