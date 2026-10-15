# 问数 Agent 实际业务试点

## 当前改动

当前业务 Worker 的 `AgentRunHandler` 只在后端试点规则命中时启用中台。规则以工作空间 UUID、用户 UUID 白名单、显式 enabled 和带时区到期时间限定；未命中、配置缺失或格式错误仍使用原实现。前端问数入口继续关闭，首页保持倒序。ASR、TTS、其他 Agent 和正式业务确认流程不变。

中台先取得后端已按用户权限查询的事实。它不取得数据库、MCP 或写工具凭据。正常答案与兜底答案使用共同契约校验，回到原持久化事务，只保存一份最终回答。运行记录新增私有 `business_context.inference_route`，保留提供方、回退原因和中台运行标识，前端结果不带这些字段。

同一 conversation + client_message_id 的重试返回原 run；同一标识换内容或输入来源返回 HTTP409。数据库表结构与请求字段不变，OpenAPI 已同步冲突响应。单客户问数总量独立聚合，不再把最多20条明细当成全部记录；权限仍由现有 RLS 控制。

## 运维配置

通用原模型配置继续由现有环境/工作空间运行配置提供，不替换其 base URL，也不导出其凭据。FDE 的专用 Key 必须使用独立安全配置注入，不进入仓库。

- `AGENT_FDE_BASE_URL`：平台 HTTPS 地址，路径 `/v1`。
- `AGENT_FDE_CHATBI_ID`、`AGENT_FDE_CHATBI_API_KEY`：已发布问数 Agent 及专用运行 Key。
- `AGENT_PLATFORM_BINDINGS_JSON`：工作空间下 `chatbi` 的 enabled、agent_id、execution_mode=filtered_facts、expected_snapshot_id。
- `AGENT_FDE_PILOT_PATH`：后端可读、运维可写的 JSON 文件。每次运行读取，原子替换即可切换，无需为了故障试验重复重启 Worker。可用 `AGENT_FDE_PILOT_JSON` 作为没有文件时的静态配置。
- `AGENT_INFERENCE_TOTAL_SECONDS=45`、`AGENT_INFERENCE_PLATFORM_SECONDS=12`：总预算和中台预算。

试点文件示意（UUID 与到期时间须由操作者明确填写，禁止通配符）：

```json
{
  "<workspace UUID>": {
    "enabled": true,
    "user_ids": ["<test user UUID>"],
    "expires_at": "<future ISO8601 timestamp with timezone>",
    "block_platform_requests": false
  }
}
```

中台 API 当前不能证明实际运行的发布快照。expected_snapshot_id 仅是配置预期，actual_snapshot_id 保持 null；当前例外只适用于用户已批准的权限过滤问数，不扩展到评分或有写工具的能力。

## 验收步骤

先运行非数据库回归和隔离 PostgreSQL 回归，之后按发布手册准备独立 release、备份核验、部署锁、原子切换和可回滚版本。当前服务的真实切换与超出已授权试点的生产开启分别按既有授权边界执行。默认试点文件为空，不对其他用户启用。

每种场景从真实业务 API 创建独立会话；同一已确认消息重放一次验证幂等。使用 `backend/scripts/acceptance/chatbi_business_live.py`，凭据仅保留内存，结束注销本脚本创建的两个会话。不要注销小程序正在使用的用户会话。

1. **normal**：白名单 enabled=true、block=false，必须记录真实中台 HTTP200、接受的中台结果及 conversation/task标识。
2. **off**：enabled=false，要求原 SenseAudio API真实 HTTP200、任务成功，没有中台尝试；它验证原路径，不算失败兜底。
3. **blocked**：enabled=true、block=true，中台尝试在网络发送前被本地受控阻断，同run/operation的原 SenseAudio API真实 HTTP200，只有一份最终回答。
4. 单客户模式使用 `--customer` 复验，仍是同一个 Agent，不另计接入数量。
5. `chatbi_business_audit.py` 在数据库主机通过已有应用角色及真实用户权限只读核对上述run；只输出调用元数据和指标匹配结果，不输出业务数据、模型正文或凭据。
6. 完成后恢复试点为空/关闭，记录代码实际部署状态和下一轮明确启用范围。另一用户的 run读取404、消息不可见；所有现有页面业务不新增入口。

受控阻断日志必须标注 `ControlledPlatformBlock`、`test_fault_injected=true`、`network_dispatch_suppressed=true`、HTTP状态为空。它没有向中台发送网络请求，因此中台网页不会有对应调用日志；不能写成平台真的500/503。

异常返回、超时、空答案、校验不合格、迟到结果、权限变化和双方失败边界由自动测试覆盖；真实受控阻断只证明同run兜底链可用，不代表每类自然故障都已在生产重演。API成功不等于中台成功，必须同时核对运行提供方记录。

## 首轮真实验收发现与修正

`c699680` 曾短时部署，仅为XS001启用。正常场景run `74a729b0-2dc8-4bdf-b4e1-ecddde21239b` 未通过：首个中台HTTP200、三次原模型HTTP200均不能代表业务成功，最终状态failed/dead_letter；日志确认问数结果格式校验拒绝。部署脚本随即关闭试点并回滚 `report-fix-20260911-7b67cb0`，live/ready及API/Worker健康正常，测试登录均已注销。该轮不得计为中台或兜底验收成功。

代码复核发现，原问数允许JSON数字，移植校验却只接受字符串。修正后对有限数值按原值转换为卡片文本，仍拒绝布尔、空值、对象和非有限数；不补造缺失答案。同时在两个提供方均耗尽预算/失败后直接给失败终态，避免队列重试把整轮收费请求再次执行。兼容与失败边界新增13项测试；需要在后续版本用新业务请求复验。

最终部署、真实正常/关闭/拦截与自然超时结果见 [业务验收](AGENT_CHATBI_BUSINESS_VERIFICATION_20260912.md)。当前试点已关闭；单客户正常中台时延仍未通过，按其真实状态继续收口首项。
