# 当前 Agent 与模型流程

核对日期：2026-09-11。Agent 负责真实模型候选、审核和解释；经营事实由数据库维护，正式客户/商机/拜访/任务由应用服务按人工操作写入。

## 运行过程

1. API 创建会话、消息和运行任务，按显式 mode 检查角色能力。
2. Worker 领取任务；services/agent_run/ 装载当前账号可见事实、字段契约与提示词。
3. SenseAudio 适配器真实请求模型，校验结构化输出，保存 run、message、artifact 或分析记录。
4. 前端轮询已保存结果。需要正式写入的候选由人核对后提交业务 API。

问数使用 chatbi / customer_chatbi；拜访使用 visit_entry 及审核流程；customer_create 生成客户候选；opportunity_draft 明确 none/create/update。management_task 模式只允许管理角色；一线销售可通过 REST 表单创建范围内任务，不等于可进入管理 Agent 模式。模式清单和角色规则以 domain/policy.py 为准。

## 当前契约

- 拜访：contracts/visit_schema.py 为 visit.v3 唯一字段入口，正文为沟通内容、下一步计划；首访四项资料参与审核。提示词版本 `visit-v3-20260910.2`；真实分数严格高于60且下一步通过，由人归档。
- 商机：contracts/opportunity_candidate.py 校验 none/create/update、可见商机 ID 与人民币元金额。提示词 `opportunity-candidate-v2-20260910`；未表达关联意图不强行匹配，未要求变化的字段保留空值供表单保留原值。
- 分析：run 保存 fact_scope_version 和范围；message.source_run_id 为明确来源外键。旧范围结果失效后不可从报告、卡片或产物绕回读取。
- 原始语音转写可无分析 run，仍按上传人隔离；经营分析产物须引用可见运行。历史无来源数据保留审计，不自动作为当前分析显示。

## 模型与文件

默认配置是 senseaudio-s2-lite、senseaudio-asr-lite-1.5-260319、senseaudio-tts-1.5-260319；这是 config.py 的默认值，运行覆盖应从授权配置入口核对。TTS 适配器存在不代表端到端播报已验收。密钥仅由服务端配置注入，禁止复制进文档、Trace、前端和仓库。

直接 /audio/transcriptions 受适配器约10MB限制；拜访 /visit-imports 是另一条异步入口，录音最大100MB/60分钟，先转码分片再调用ASR。文本PDF/DOCX/PPTX/MD/TXT最大20MB、提取最多5万字，PDF最多200页。不支持OCR或旧DOC/PPT二进制格式。

## 故障与证据

网关有重试/熔断；失败保留明确状态，不用规则编造模型回答。Worker 租约和完成回执保证本地结果恢复，无法保证外部供应商计费恰好一次。输出格式正确只证明契约合法；模型质量仍需真实样例评测，不得把人工改分当作提示词通过。

实际测试版本、样例和限制见 [验收记录](../../docs/MAINTENANCE_VALIDATION_20260910.md)。智能体中台、任意工具调用和模型直连数据库均不属于当前链路。
