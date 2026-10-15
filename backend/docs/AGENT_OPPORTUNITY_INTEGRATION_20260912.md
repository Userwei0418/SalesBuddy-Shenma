# 第三项：商机新建／更新判断 Agent

以当前 `salegent-v1` 的 `opportunity_draft` 流程接入中台，原 `SenseAudioClient` 保留兜底。本项已获用户专项授权，完成中台发布、专用 API、真实业务验收和生产部署。当前 release 为 `agent-opportunity-20260912-82b5db5`，商机候选与作战地图均保持 production；问数仍关闭且前端隐藏。

## 现有业务与接入边界

拜访录入页 `submitTranscript` 原本并行发起拜访结构化和商机候选两次业务运行。本项只接后者，不改变拜访结构化、语音基础能力或前端。`AgentFactsLoader` 按当前身份及所选客户查询现有可见商机；原文用于判断本次意图，历史字段只用于匹配 ID，不能自动变成本次修改建议。

- `none`：本次不关联商机，清空商机字段，不强制建档。
- `create`：只生成新建候选，保留原文已明确字段，缺失项由人工补充。
- `update`：只能引用本次权限事实包中的商机 ID，不凭名称跨客户或跨销售更新；未提及字段保持未知。

候选通过既有 `opportunity-candidate-v2-20260910` 校验，运行进入 `waiting_human`，只保存一份 `agent.artifact` 和候选回答。它不调用正式商机写入服务，也不发送“已创建／已更新”业务变化通知。用户在既有确认页确认、补充和保存后，仍走 `POST /customers/{id}/opportunities` 与 `save_opportunity` 的权限、版本、名称查重、幂等、业务变化卡片和后续作战地图重评。

关联任务已明确同步：本期不恢复 Agent 自动创建正式商机，不恢复撤下的首页商机创建／更新确认推送；客户搜索范围不等于商机修改权限。商机名称、阶段、ACV 和预计关单日期沿用现有人工保存必填项，伙伴选填。没有新增前端入口、API 或数据库迁移。

## 中台与原 API 路由

`opportunity_draft` 使用独立的 `AGENT_FDE_OPPORTUNITY_ID`、`AGENT_FDE_OPPORTUNITY_API_KEY`、工作空间绑定和 `capabilities.opportunity_draft` 开关，不借用问数或作战地图的专用凭据。缺失、关闭或试点过期时继续原 API；中台超时、无效候选或受控阻断后在同一次 operation 转原 API，原调用及结果同样校验。双方均失败时明确失败，不保存伪候选。

支持限定账号、到期时间和受控请求阻断的 pilot，以及验收后可持续启用的 production。production 忽略测试阻断标志。用户已授权本项发布、专用 API、限定业务验证及通过后正式启用；最终验收已通过，按用户既定原则保持正式接入。

调用 trace 保存在运行的私有业务上下文，既有前端候选字段不变。新增候选产物的 `model_ref` 采用后端实际选择的提供方，避免中台产物被误记为原模型。Agent 未获 MCP 或任何数据库写入工具；本期仍由程序查询事实，MCP 二期。

## 中台发布核验

已通过 CLI 核对 `zhouwei5@sensetime.com`、`FDE Workspace`，回读现有草稿且发布 dry-run 通过；用户授权后执行发布并再次回读：

- 名称：销售智助-商机新建更新判断-BETA
- Agent ID：`01a09022-b58f-7214-85eb-29d035ea6f2c`
- 草稿 revision：`71f6b210b3d49eee199b3907d544a949442848eeb4374436aa6ba54c4038778a`
- 草稿 snapshot：`01a09022-b591-76bb-8877-188b5287e1b3`
- `published=true`；发布 revision `51ae0a53b88f54c12595802f5656f429ea2849192489cb17b6ba0b4d3d889950`，snapshot `01a091f8-9542-7c63-8439-c9f1240669a5`。
- 模型 `senseaudio-s2`；提示词含当前 v2 候选契约，未修改中台业务规则。专用服务凭据仅保存于私有后端配置。

发布后已回读新的 snapshot 再绑定；草稿 snapshot 不作为发布证明。运行 API 仍无法证明实际 snapshot，需保留该限制。

## 自动化测试与证据边界

464 项非数据库回归通过；包括 create/update/none、中台关闭、主动阻断、超时、越权 ID、非法阶段、双路失败和独立开关。测试使用真实 FDE SSE 解析器与业务 handler，但 HTTP 为合成响应。

75 项隔离 PostgreSQL 测试通过，其中新增六种情形，核对权限事实、单运行／单候选／单回答、候选生成前后正式商机及变化通知不变、另一销售不可读取候选，以及独立人工保存后才正式新建或更新、重复保存不重复写入。代码提交为 `d71a7cc`；归档与实际测试目录的文件哈希另外留存于证据目录。提供方 HTTP 为合成数据，不能冒充真实平台验收。

真实业务验证使用 XS001、XS002 和下述测试客户，经过已有 conversation/message API、队列、Worker 和持久化；新建、更新、不关联三类候选，以及关闭中台后原 API 接续和主动阻断后的同任务兜底均通过。真实验证只生成候选，未确认或修改正式商机。该项已通过，第四项尚未开始。

验收脚本为 `backend/scripts/acceptance/opportunity_business_live.py`，`--preflight-only` 只核对限定测试账号、客户及更新样例，不提交候选、不调用模型。正式运行分别使用 `--intent create/update/none` 和 `--scenario normal/off/blocked/production`，记录私有调用路由与任务标识，核对商机数据和业务变化记录未变，并只注销自己的测试会话。脚本与业务实现分别记录版本；用户已确认“授权本项发布、验证及启用”。

只读预检已通过：XS001 与现有测试客户“【演示】字段验收-422815”（`1b127c42-c436-470f-a6dc-876de6586d53`）具备可见开放商机，可覆盖更新场景；XS002用于隔离检查。预检没有提交候选或模型调用，仅建立并注销自己的测试会话。先前作战地图所用客户没有开放商机，因此没有用它伪造更新样例或新建正式商机。用户已授权发布本项现有草稿、开通专用后端 API、部署本项并在上述范围真实业务验证，通过后保持本项正式启用；线上作战地图已启用配置保留。

## 首次真实验证与验收脚本修正

`7e862fb` 的真实 create / update 均由中台正常完成（6.068 秒 / 5.054 秒）。none 场景中台返回未通过既有业务校验，原 API 在同一 operation 内接续，4.044 秒生成正确待确认候选，正式商机和变化卡片未变。但验收脚本仅允许 TimeoutError 兜底，没有允许既有 InvalidAgentResult 兜底，导致脚本失败并按发布规则自动回滚到 `75d7e36`。失败原记录保留为 `prior-none-validation-fallback.json`，不能把其中 passed=false 改成通过。

本次只修正验收判定：明确区分中台直接采纳与 TimeoutError / InvalidAgentResult 后原 API 成功；继续要求结果字段、真实提供方 HTTP、同 operation、单候选、人工确认状态、RLS 和幂等全部通过。运行代码和契约没有因此放宽。重新部署后仍要求至少一项真实中台成功，再完成 off / blocked / production 验证。

## 最终真实业务验收与当前运行状态

2026-09-12，release `82b5db584485e60b0585d4c281ee1f69e5509e76`。最终七项结果如下，耗时为业务消息提交到候选可读取，HTTP 为实际中台或原 API 调用。

| 场景 | 最终提供方 | 耗时 | 结论 |
|---|---|---:|---|
| 正常新建 | 中台 Agent | 5.059 秒 | 明确字段保留、候选待确认 |
| 正常更新 | 中台 Agent | 4.046 秒 | 关联可见已有商机，只提取本次修改 |
| 正常不关联 | 原 SenseAudio API | 5.050 秒 | 中台结果未过校验，同任务兜底成功 |
| 关闭中台后新建 | 原 SenseAudio API | 3.037 秒 | 原路径独立完成 |
| 主动阻断后更新 | 原 SenseAudio API | 3.037 秒 | 中台请求未发出，同任务兜底成功 |
| 正式配置不关联 | 原 SenseAudio API | 4.045 秒 | 中台结果未过校验，同任务兜底成功 |
| 正式配置新建 | 中台 Agent | 3.038 秒 | 正式路由中台结果直接采纳 |

新建保留明确名称、8 万元 ACV、50% 阶段和关单日期；更新只关联受权限约束的已有商机，ACV 为 1200 元候选，未提及阶段、日期、伙伴和产品线保持未知；不关联清空商机字段。七项都进入 `waiting_human`，每次只有一个完成的 Worker job、一个 completion receipt、一个待确认 artifact 和一条回答。正式商机指纹与业务变化数量保持不变，跨销售不可读取，重复消息同 run、不同内容复用标识返回 409。仅注销脚本建立的测试会话。

“不关联”在本次中台输出校验上仍有优化项，三次（含首次脚本失败）均实际触发 `InvalidAgentResult` 后由原 API 成功完成，不能写成该场景的中台回答已直接通过。按已确认的业务兜底验收口径，不阻塞本项启用；后续中台方可依本 Agent 日志调整提示词细节。本次不深入排查中台内部，不放宽业务校验。

已核对 324 个归档文件与实际 release 一致，134 个运行源码文件与 75 项隔离 PG 验证源码相同；API / Worker 均 active 且进程工作目录为新 release。数据库保持 V054，无新增迁移，临时验证数据库、角色为 0。切换前备份 2,560,365 字节并经 pg_restore --list 验证，备份 timer active、上次结果 success。回滚 release `75d7e36` 保留。凭据仅存私有配置，root 0600。

`/etc/sales-fde-pilot.json` 当前在业务 workspace `00000000-0000-0000-0000-000000000001` 分别启用 `capabilities.battle_map_review` 和 `capabilities.opportunity_draft` 的 production，无试点过期、无测试阻断配置。Worker 的 EnvironmentFile 依次保留原问数、作战地图及新增商机配置；后续部署须保留三个文件引用，不导出或输出其中凭据。

平台日志页已实际看到本次 8 条中台调用记录，见 `platform-logs.png`。关闭与主动阻断两次没有发送中台请求，因此不会产生中台对话。可按下面标识对应后端与中台：

- 正式新建业务 run：`3719231f-3218-4e65-9fa8-8e8ac95d6cf3`。
- 对应中台 conversation：`256786f6-a472-43b8-952a-c446dbee71b0`；task：`7d7f9adb-cb1b-4564-b308-8252c735dda3`。
- [平台日志页](https://123.207.235.181/agents/01a09022-b58f-7214-85eb-29d035ea6f2c/logs)。

证据目录：`backend/docs/evidence/opportunity-business-20260912/`。七个业务记录与 deployment 为最终验证，verification 为发布前合成测试，prior-none-validation-fallback 保留首次脚本失败原记录。本轮没有重做微信真机 UI 验收，没有实际确认或修改正式商机；显式人工保存覆盖在隔离 PG 测试。
