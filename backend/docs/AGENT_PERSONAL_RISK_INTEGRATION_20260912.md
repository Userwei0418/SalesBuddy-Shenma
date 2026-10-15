# 第四项：个人客户风险识别 Agent

第四项已发布并部署到 `agent-risk-20260912-47956c7-r2`，`personal_risks` 保持 production。后端接入、真实业务保存及原 API 兜底通过；三次实际中台请求均超时，所以本项“中台直接返回合格风险”尚未验收通过，不能把这五轮结果说成中台生成成功。作战地图和商机判断继续 production，问数关闭且前端隐藏。

## 当前业务及已确认的写入规则

现有首页“查看个人风险”通过 conversation/message、队列与 `AgentRunHandler` 运行 `personal_risks`。仅销售角色可用。后端查该用户最近 180 天内、最多 12 条已确认或归档拜访，并附本人可见的未解除风险清单。事实查询继续使用现有身份及 RLS，不扩大客户历史、他人商机或其他销售风险的可见范围。

既有风险识别并不是“每条先出 artifact，人工确认后才出现提示”：`AgentRunStore` 会把有效提示保存到 `insight.risk`，新风险为 `pending`，显示在风险列表和卡片中。后端从来源拜访和当前身份确定客户、商机、所有人及团队；模型提供这些字段或 `resolved` 状态也不会成为写入依据。

用户已确认沿用此流程：风险提示自动保存为待处理；用户在既有详情页明确解除时，才走 `POST /risks/{risk_id}/resolve` 的权限和版本检查。风险识别不修改客户类型、商机阶段或金额，不替用户解除风险、不自动创建业务任务。每次重评同一“用户＋来源拜访＋风险类型”更新原提示，已 resolved / accepted 的风险不会被重新打开。每条提示额外增加人工审批并非当前已确认要求。

已与关联任务对齐：红绿灯只作为展示建议，未确认的新量化规则不固化。本次不引入“七天未联系即高风险”、新评分权重或阈值。MCP 自主查询及中台工具挂载仍留二期。

## 后端接入

- 独立配置：`AGENT_FDE_PERSONAL_RISKS_ID`、`AGENT_FDE_PERSONAL_RISKS_API_KEY` 和 `capabilities.personal_risks`，不能借用问数、地图或商机开关及凭据。
- pilot 限定用户和有效期；production 持续启用且忽略测试故障标志。本项默认缺配置时仍用原 API。
- 中台超时、无效结果或受控阻断后，沿用同一次 inference operation 转原 SenseAudio API，后端校验后才统一持久化；双方失败不生成假风险或成功卡片。
- 既有合法类型、严重度、来源拜访和证据校验继续；将提示词已有的最多 8 条独立风险落实为校验，拒绝重复来源/类型、无效标题或建议。没有新增风险判断规则。
- 风险行及 run 的 `model_ref` 采用实际提供方，避免中台结果仍标成原模型。提供方与错误细节仅留后端 trace，前端卡片契约保持一致。

## 配置与代码验证

CLI 已核对 `zhouwei5@sensetime.com` 和 FDE Workspace，回读现有草稿并通过 publish dry-run，获本项授权后发布并再次 get 核验：

- 名称：销售智助-个人客户风险识别-BETA
- Agent ID：`01a09022-bb1a-7dab-8481-917e0472c528`
- 发布 revision：`e0e1bb791f337cd427b26ebc3b497624149fe6cdfd1f3972ee50aec0087bc27c`
- 发布 snapshot：`01a09217-97c0-7043-adb9-3f46d0bc3c10`
- 模型 `senseaudio-s2`，`published=true`。提示词与现行个人风险规则一致，无 MCP 或数据库工具。

483 项非数据库测试通过，新增 19 项覆盖配置隔离、过期/越权配置、正常/空结果、关闭/受控阻断/超时、事实外的拜访 ID、非法类型、缺证据、重复或超量风险、不合格标题和双路失败。这里的 HTTP 为合成数据，使用真实 FDE SSE 解析器和业务 handler。

83 项隔离 PostgreSQL 测试通过，其中新增 8 项验证实际权限事实和风险持久化：仅当前用户拜访进入事实，风险所有人、客户和 pending 状态由后端确定；模型伪造客户/所有人/解除状态无效；卡片只保存一份，重复提交同 run、内容冲突拒绝；重评更新同一风险，显式人工解除后或既有 accepted 风险不复活；另一销售不能读/解除；客户、商机和业务通知保持不变。第一次测试因 fixture 漏传可空 `customer_id` 参数失败，补齐后 83 项全部通过，业务代码未因此改动。

335 个归档文件与实际隔离测试目录核对，运行代码基线为 `086b5aa`，fixture 修正另随后续提交保留。OpenAPI 与应用一致，无接口或数据库迁移；临时测试库、角色已清理。源码、实际测试目录和证据分别记录，不将预检说成业务接入成功。

## 已授权的真实业务验收范围

只读预检确认 XS001 的最近 12 条拜访均带明确的测试客户标识。本人既有 18 条未解除风险中，有范围外的历史风险；不删除、不解除，也不将其视为可任意修改的测试数据。

用户授权范围是：将现有程序查询的这 12 条测试拜访及本人可见风险清单提供给本 Agent；由后端只新增或重评这批拜访对应的待处理风险提示。范围外和已解除风险、客户及商机数据须保持不变。预检范围文件为 `evidence/personal-risk-business-20260912/scope-proposed.json`，当前 approval_status=approved（保留原文件名以衔接预检依据）。它记录具体用户与 12 条来源 ID，未保存跟进正文或模型凭据。

准备好的 `backend/scripts/acceptance/personal_risk_business_live.py` 会在提交业务前重新核对相同的来源集合，变化即停止。`--preflight-only` 不建业务会话、不调用模型、不写风险；正式运行需范围文件获批，分别验证 normal、off、blocked、production。每次检查单 job/receipt/回答、最多 8 条非空风险写入、风险来源与所有人、范围外/已关闭风险指纹、实际提供方、同 operation 兜底及 XS002 访问隔离。不会为了通过测试删掉历史风险或自动解除本轮提示。

用户已专项授权发布、专用 API、上述业务验证及验收后持续启用。本项业务可用性及兜底验收完成，production 保持开启；中台直出未通过单独保留。后续第五项继续按代码、验证、专项发布授权、业务验证的顺序推进。


## 真实业务结果与正式启用

所有请求使用已部署业务 API → 队列 → Worker → 当前用户权限事实 → 提供方 → 契约校验 → 后端风险保存；没有用直调 Agent 脚本代替业务验收。五轮均 `succeeded`，结果卡片与已保存风险一致。

| 场景 | 实际提供方和结果 | 端到端耗时 | 本轮新增或重评条数 | 证据 |
|---|---|---:|---:|---|
| 首轮正常请求 | 中台自然超时，原 API 接手成功 | 21.178 秒 | 6 | [natural-timeout.json](evidence/personal-risk-business-20260912/natural-timeout.json) |
| 关闭中台开关 | 原 API 独立成功，没有中台尝试 | 9.093 秒 | 5 | [off.json](evidence/personal-risk-business-20260912/off.json) |
| 手动拦截中台请求 | ControlledPlatformBlock，同 operation 原 API 成功 | 8.082 秒 | 5 | [blocked.json](evidence/personal-risk-business-20260912/blocked.json) |
| 同范围正常补验 | 中台自然超时，原 API 接手成功 | 21.180 秒 | 6 | [normal-recheck.json](evidence/personal-risk-business-20260912/normal-recheck.json) |
| production 正式开关 | 中台自然超时，原 API 接手成功 | 20.175 秒 | 4 | [production.json](evidence/personal-risk-business-20260912/production.json) |

实际最终模型为原 `senseaudio-s2-lite`。三次中台请求均确实发出并收到 HTTP 200 响应头，但未在既有预算内完成有效结果；HTTP 200 不代表业务回答通过。中台日志页可见三次 personal_risks 请求，截图 [platform-logs.png](evidence/personal-risk-business-20260912/platform-logs.png)。off 不尝试中台，blocked 在发送前受控拦截，两者不应出现在中台日志。

五轮都验证了：相同提交只对应一个 run、不同内容冲突 409；单 job 且 attempts=1、单完成回执及回答；写入只对应已授权的 12 条拜访；25 条范围外或已关闭风险指纹保持不变；客户、商机和业务通知不变；XS002 读取对应运行与风险均 404，不能读取会话内容；验收创建的两个账号会话均退出。五轮涉及同一组六条测试风险的新增或重评，未删除或解除这些提示。风险解除与已关闭风险不复活另由隔离 PostgreSQL 用例覆盖，本轮未调用真实解除接口。

首轮及补验部署门槛曾额外要求“至少一次中台直出合格”，因此两次业务兜底虽成功，门槛仍触发回滚至 `82b5db5`。原始证据完整保留。依据用户此前明确的“超时就触发兜底、保证业务可用”，最终使用同一份已测且哈希一致的代码恢复部署，并完成 production 业务验证；没有放宽运行结果校验、延长模型预算、修改测试事实或隐藏失败。该取舍只完成本期业务可用性验收，中台直出效果仍是未完成项。

## 部署与验证边界

- 发布代码 `47956c7f44c674c7cbddf0374e0609ae6f42f0f0`；实际运行 release `/www/wwwroot/salegent/releases/agent-risk-20260912-47956c7-r2`。
- 部署文件 342 个哈希一致，134 个运行源文件与已执行 483 项非数据库、83 项隔离 PostgreSQL 测试的源码一致。测试 HTTP 是合成数据，五轮业务 HTTP 和两种提供方尝试是真实运行证据。
- API、Worker 活跃且 cwd 都指向该 release；readiness 正常，数据库仍 V054；临时测试库和角色均为 0。
- 最后切换前备份 2,599,045 字节，`pg_restore --list` 校验通过；定时备份 active、最近执行 success。回滚版本保留 `agent-opportunity-20260912-82b5db5`。
- 风险专用凭据只保存在私有后端配置（root 0600）；原三项绑定和专用凭据保留。配置明确开启地图、商机、风险三项 production，不残留试点白名单或故障注入；问数保持关闭。详见 [deployment.json](evidence/personal-risk-business-20260912/deployment.json)。
- FDE 运行 API 尚不能证明实际使用的 snapshot；发布 snapshot 只是期望版本，不能伪称已做运行版本锁定。MCP 查询和中台写工具挂载仍留二期。
- 本轮验证真实后端业务与中台日志页，没有重新执行微信真机 UI 验收；前端代码及交互契约未改动。超时场景约 20–21 秒，比原 API 独立约 9 秒更慢，交互一致不等于耗时一致。
