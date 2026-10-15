# 第五项：拜访记录结构化与质检 Agent

第五项已发布并部署至 `agent-visit-20260912-bf5bd29`，七轮真实业务验收全部通过，`visit_entry` 保持 production。三轮由中台直接返回合格结果，两轮中台无效结果由原 API 接续，另两轮分别验证关闭和受控阻断。原文重新审核与正式配置复验的中台输出仍有调优项，不冒称全部中台直出成功。代码已通过 508 项非数据库和 94 项隔离 PostgreSQL 测试；本次只生成待确认拜访，没有执行正式归档。当前累计五项已部署并完成业务验收，其中地图、商机、风险、拜访四项正式启用，问数关闭且入口隐藏。

## 已确认的业务边界

关联任务再次核对了本聊天已有结论：沟通内容与下一步计划为两栏正文，拜访目标可选；仅显示总分，归档门槛严格为总分 >60，60 本身不通过。首次拜访沿用主营业务、需求、预算和联系人角色的现有要求，预算未确定可以保留文本，不推断联系人角色。缺少选填项不编造、不扣分。客户必选、商机选填，客户可被选择跟进不等于授予该客户全部历史或他人商机的读取权限。

AI 抽取与审核只生成 `visit_entry` 产物、消息和 `waiting_human` 运行。实际归档仍走既有 `archive_visit`：核对本人审核产物、正文及首访字段是否一致，读取服务端保存的分数与下一步审核结果，校验客户/商机/协同人/材料来源，才保存归档拜访。人工归档可以触发现有作战地图复盘和个人能力评估；本项不改变这些行为。模型不得直接创建客户、商机或代替用户确认归档。

## 代码变更

- 新增专用 `AGENT_FDE_VISIT_ENTRY_ID`、`AGENT_FDE_VISIT_ENTRY_API_KEY`、`capabilities.visit_entry`；其他 Agent 的开关和凭据不能误启用本项。默认未配置时继续原 API，production 忽略试点故障标志。
- 后端 `AgentRunHandler` 在本项开关下经 `InferenceService` 调用专用 FDE adapter；中台超时、错误或无效结果后，同一次 operation 走原 API。两路结果使用既有拜访结构归一化，保留当前 prompt version、总分等级、缺失字段及人工确认流程。
- 对明确的“拜访审核 v2”请求，校验结果必须逐字保留审核正文和首次拜访字段；模型改写会触发兜底。内容不足或低分是有效业务审核结果，不能为了获得高分而触发换模型。
- 拜访专用 facts 只含本次时间基准，原文由可信调用信封的 `user_text` 提供。旧默认加载器附带的经营汇总原本未进入直接 API 提示词，因此本项不再查询和向中台携带这些无关数据。
- 归档兼容检查修正：当前 `quality_review.next_action.passed=false` 优先于旧格式的通过标记，避免旧 `next_action_passed=true` 或顶层 `next_action.passed=true` 覆盖明确拒绝。只有缺少新版字段时才兼容旧格式。分数 >60 和人工确认要求未变。
- 模型伪造对象 ID、所有人和商机变更字段不能进入归档字段；产物 model_ref 和 run trace 记录实际提供方，前端没有新增入口或改动。

## 发布前的平台草稿基线

- 名称：销售智助-拜访记录结构化与质检-BETA
- Agent ID：`01a09022-c06d-7ac3-b863-8a1345b069ae`
- `published=false`
- 草稿 revision：`ee0468aab0974bd93b4f305c8c374fd568def33c317b1e8cb00c00099793bb74`
- 草稿 snapshot：`01a09022-c06f-75b6-bd03-21edf1c89a61`（不能作为已发布版本）
- 模型：senseaudio-s2

发布前已通过本人 CLI 核对账号和 FDE Workspace，回读提示词包含当前代码默认拜访提示词全文，无需重建同名 Agent。该草稿检查阶段未调用真实模型推理，也未使用其他 Agent 的服务凭据。见 [platform-draft.json](evidence/visit-entry-business-20260912/platform-draft.json)。

## 验证状态

非数据库 508 项通过（新增 23 项拜访路由/内容测试与 2 项归档兼容拒绝用例）；94 项数据库用例原先因连接问题未执行，网络恢复后已在独立临时 PostgreSQL 数据库中全部通过，耗时 5.58 秒。验证角色为非超级用户且无 RLS 绕过权限；结束后临时数据库和临时角色均为 0。Ruff 与 OpenAPI 同步检查通过。原“未接入能力不得使用 filtered_facts”用例已改为继续保护尚未接入的 today_tasks / operating_report，visit_entry 的专用 Agent/模式/凭据隔离另有正反测试。

11 项新增数据库用例已通过真实 handler、FDE SSE 解析器、原 API HTTP 客户端、artifact 持久化及人工 `archive_visit` 验证：正常/首次拜访/重新审核、off、blocked、低分、下一步不通过、首访字段缺失、坏分数、改写审核、两路失败。外部模型 HTTP 为合成响应，数据库为真实隔离执行。内容改动、跨用户确认、伪造客户端分数、自动商机字段、重复归档和失败回滚均有断言；这些测试不替代实际中台和原 API 的业务验收。

此前服务器 SSH 22、业务 HTTPS 443 与 GitHub 从当前电脑连接超时；本机 PostgreSQL 替代安装也未连上，已停止且未启动实例。用户随后调整网络并要求续接，本机到服务器的路由已从飞连全局模式的 `utun6` 改为 `en0`，SSH 与业务 readiness 实测恢复，恢复连通时线上仍为 `agent-risk-20260912-47956c7-r2`，API/Worker active。第五项测试包已上传、哈希核对及隔离测试完成，当时尚未部署第五项。GitHub 443 与 Vault 使用的 GitHub SSH 22 仍不可达，提交当前仅确认保存在本地。见 [connectivity.json](evidence/visit-entry-business-20260912/connectivity.json) 和 [verification.json](evidence/visit-entry-business-20260912/verification.json)。

验收脚本 Ruff、帮助命令、四类合成输入生成和证据 JSON 格式检查通过；`--preflight-only` 已在当前服务器以 RLS 受限角色核对 XS001 和指定演示客户，未创建应用会话或调用模型，见 [preflight.json](evidence/visit-entry-business-20260912/preflight.json)。随后获授权执行的七轮真实业务结果见后文。

## 已授权的真实业务验证方案

脚本 `backend/scripts/acceptance/visit_entry_business_live.py` 已准备：先按当前 RLS 校验 XS001 和“【演示】字段验收-422815”，只提供合成拜访原文，分别验证再次拜访、首次拜访、原文重新审核、低质量输入，以及 off、blocked、production。

真实验证只创建本次会话、运行、待确认拜访产物和回答，不点击人工确认、不调用真实归档接口，不改变正式拜访、客户、商机和业务通知。脚本检查审核质量和文本保留、单 job/回执/产物/回答、幂等与 409、实际提供方及另一销售账号隔离；自己的测试登录会话最后退出。中台超时后原 API 完成属于兜底成功，直出效果另记，不把二者混为一谈。

[scope-proposed.json](evidence/visit-entry-business-20260912/scope-proposed.json) 的 approval_status 当前为 approved（保留文件名便于追溯），只读预检已通过。用户已明确授权本项发布、专用 API、上述业务验证及验收后持续启用。`--preflight-only` 不建应用登录会话、不调用模型或写业务结果；正式运行强制要求 scope 已获批。

## 发布及专用 API

发布后 CLI 再次 get 确认 `published=true`，正式 snapshot 为 `01a093e9-eb31-7c35-a3e8-4409a374b967`，revision 为 `7d3cb09404810db1ede82477b9c1d8bbbd51964fc3ed6512f7728c206f92d182`。上述“平台草稿”保留的是发布前基线。服务访问页已回读为服务中，创建了本 Agent 的独立 API Key；凭据仅保存于私有配置，不写入仓库。后端新增配置保留原四项 Agent 的 ID 和预期发布快照，见 [发布记录](evidence/visit-entry-business-20260912/platform-published.json) 和 [非敏感绑定](evidence/visit-entry-business-20260912/runtime-bindings.json)。

先完成第五项的测试与业务接入，再推进今日待办和经营即时总结。自主查询 MCP、中台写工具挂载和问数 UI 继续留二期。

## 部署与回滚流程

`backend/deploy/deploy-visit-agent.sh` 将五个操作参数作为输入：不可变源码提交、归档包、文件哈希清单、已授权范围 JSON、全新证据目录。执行前要求范围已批准且预检通过、当前 release 仍为第四项、现有三个 production 开关和 Worker 配置文件引用未变化，并只通过元数据确认第五项凭据文件为 root 0600；不读取或输出凭据内容。

脚本持有既有部署锁，先校验源码和 Python 导入位置、完成原子数据库备份及归档可读性检查，再检查运行中任务并切换独立 release。按四类正常输入、off、blocked 和 production 依次执行七轮真实业务验收；对每轮业务结果、写入边界和兜底路径严格采用验收脚本结果，单独统计中台直出次数。不会额外添加“必须当次中台直出成功”的发布门槛。业务验收失败则恢复先前的非敏感开关配置、Worker 配置引用和第四项 release，重启后核验健康。

用户授权后已执行该脚本，七轮业务检查全过并保持正式启用；本次没有触发回滚，不能把脚本存在说成本轮回滚实测。回滚基线保留为 `agent-risk-20260912-47956c7-r2`。

## 七轮真实业务结果

| 场景 | 最终提供方 | 提交至完成 | 结果 |
|---|---|---:|---|
| 正常再次拜访 | 中台 Agent | 5.061 秒 | 客户、联系人和原文事实保留，结构化审核有效 |
| 正常首次拜访 | 中台 Agent | 3.039 秒 | 首访四字段保留，预算未确认文本及明确角色正确 |
| 原文重新审核 | 原 API | 5.053 秒 | 中台结果未过业务校验，原 API 同任务接续，审核正文不改写 |
| 低质量输入 | 中台 Agent | 6.060 秒 | 正确给出不通过和补充建议，不为追求通过分数换模型 |
| 关闭中台 | 原 API | 4.047 秒 | 没有中台尝试，原路径独立完成 |
| 受控阻断中台请求 | 原 API | 3.039 秒 | 请求发送前阻断，同 operation 自动兜底 |
| 正式配置再次拜访 | 原 API | 7.066 秒 | 中台 InvalidAgentResult，原 API 接续并形成有效待确认产物 |

每轮均验证：运行状态 `waiting_human`；一份待确认产物、一个 job 且 attempts=1、一份完成回执和回答；重复消息返回同一 run，改变内容返回 409；另一销售无法读取运行、消息与产物；正式客户、商机、拜访及通知保持不变，没有人工确认记录；脚本建立的两名测试账号会话均注销。实际原 API 模型为 `senseaudio-s2-lite`。七轮原始证据与中台 conversation/task 对照见 [业务摘要](evidence/visit-entry-business-20260912/business-summary.json)。

中台日志页实际看到五次 `visit_entry` 请求，见 [平台日志截图](evidence/visit-entry-business-20260912/platform-logs.png)。关闭及发送前阻断不产生中台日志。两次无效输出的原始判定和调用轨迹保留，没有放宽结果校验；本次没有自然超时事件，超时分支由此前自动化测试覆盖，不能说本 Agent 已做真实 60 秒或自然超时试验。

## 发布后核验与边界

- 线上 362 文件哈希全部匹配，128 个后端运行源码与通过隔离数据库测试的 `e1afdd3` 完全一致；API 与 Worker 实际工作目录均为本次 release，readiness 正常。
- 切换前备份 2,684,922 字节，归档目录检查通过；备份定时器 active、最近结果 success。数据库仍 V054，临时测试库/角色均为 0。
- 四项 production 开关均在，无试点到期时间或故障注入残留；原四个凭据引用及本项第五个引用保留，专用文件 root 0600。详见 [部署核验](evidence/visit-entry-business-20260912/deployment.json)。
- 前端及交互契约未变，本轮验证的是实际业务 API/队列/Worker/中台/数据库，未重新做微信真机视觉验收。平台运行 API 不返回实际 snapshot，仍不能宣称运行快照已被平台强校验。
