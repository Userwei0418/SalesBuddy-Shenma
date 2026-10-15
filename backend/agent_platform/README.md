# 业务 Agent 发布配置

此目录保存由销售后端维护的可审阅配置和发布回执，不保存 Key、平台登录凭据或业务原文。运行数据和权限仍由销售后端按当前身份查询；模型输出经过共享契约验证后才进入业务服务。

| 能力 | 当前配置入口 | 发布回执 |
|---|---|---|
| 销售六维能力复盘 | `competency_review/agent.json` | `competency_review/publication.json` |
| 经营即时总结及 FDE 本人协作建议 | `operating_report/prompt.json` | `operating_report/publication.json` |
| 商机新建／更新候选 | `opportunity_draft/prompt.json` | `opportunity_draft/publication.json` |
| 今日待办规划 | `today_tasks/prompt.json` | `today_tasks/publication.json` |
| 周会任务整理 | `task_import/agent.json` | `task_import/publication.json` |

`prompt.json` 是当前完整系统提示词的配置更新文件，只修改 `agent_soul.prompt`；创建完整 Agent 时不能将它当作完整创建配置。`agent.json` 是完整创建模板。每项只有一个当前配置入口，历史配置通过 Git 和平台版本记录回查。

更新时先读取平台当前配置，核对 Agent、工作空间和 revision；确认差异只含本次目标字段后 configure，再使用新 revision publish，最后 get 回读。平台发布成功后，用 [`prepare_agent_metadata.py`](../deploy/AGENT_METADATA_UPDATE.md) 按明确能力子集同步运行绑定。不能复制旧回执来宣称新版本已发布，也不能把 `expected_snapshot_id` 当作平台证明的实际运行快照。

验收规则、当前能力清单、真实失败案例与调优步骤见[中台交接](../../docs/AGENT_TUNING_HANDOFF_20260913.md)。模板、平台发布、服务器绑定和实际任务结果分别核对；提示词有版本并不代表内容质量已达标。
