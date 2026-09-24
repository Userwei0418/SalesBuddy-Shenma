# 神码周报后端接口与 Web 联调

适用对象：神码业务 Web 前端与联调人员。周报仅在业务 Web 提供；本次不增加小程序或运营后台页面。代码位于神码独立仓库，使用神码服务器、数据库和独立中台 Agent。

## 当前交付状态（2026-09-24）

已部署到神码销售服务器，后端版本 `605e6306d5abcd3c63f1961cd152836f5d676739`，数据库 V152。神码正式公司与测试公司均已启用周报。

- 业务 Web 登录、幂等生成、本人权限、主管/总经理本人范围、原小程序登录均已线上验证。
- 测试公司无正式跟进记录，生成接口返回 `insufficient_data`，不伪造周报。
- 客户服务器使用非超级用户、无 RLS 绕过权限的运行账号，在回滚事务内验证了任务领取、真实中台生成、结果保存、人工版本冲突和任务完成回执。合成客户、商机、拜访及周报数据均已回滚。
- 独立 Agent 的 15 个真实 Service API 用例全部通过。
- 1861 项后端单测通过；PostgreSQL 集成 716 通过、20 跳过；周报/OpenAPI 定向 29 项通过。GitHub 代码检查通过。
- 业务 Web 页面及销售人员的实际业务内容验收，待前端代码接入后完成。

部署与运行证据位于 `docs/evidence/weekly-v2-20260924/`。升级已备份并恢复核对 112 张既有表，账号、密码、任职和客户数据保持一致。

## 业务口径

- 本人周报：销售、销售主管、销售总经理使用各自账号。主管、总经理本期也只生成本人周报。
- 统计最近 14 个上海自然日（含今天），按跟进记录的系统上传时间 `created_at` 选取，截止到生成请求的数据快照时间。
- 归属以跟进记录的销售负责人 `recorder_user_ref_id` 为准；代录人不改变归属。
- 读取已确认、已归档且未删除的完整跟进正文。实际拜访日期用于正文说明，不替代上传时间筛选。
- 只补充这些记录关联的客户、商机档案。金额保持币种基本单位，阶段用系统字典。关联档案权限缺失时明确报错，不伪装成空数据。
- 输出按客户、商机分组的可编辑中文 Markdown。AI 原文与人工修改分别保存；需要销售核对内容后使用。

## 登录与访问

部署 API 根地址：`https://salesbuddy.shenzhoukuntai.com:28899`。

业务 Web 使用同源 `/api/v1/web/auth`，复用现有销售账号。登录不会赋予运营后台权限。浏览器写入类登录请求需要与地址一致的 `Origin`；本地开发建议通过代理保持同源。

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/v1/web/auth/login` | `{ "account_code": "XS001", "password": "用户输入" }` |
| GET | `/api/v1/web/auth/me` | 当前身份、功能权限、是否需要修改初始密码 |
| POST | `/api/v1/web/auth/refresh` | 空请求体；自动携带同源 Cookie |
| POST | `/api/v1/web/auth/password` | `{ "old_password": "原密码", "new_password": "新密码" }` |
| POST | `/api/v1/web/auth/logout` | 退出业务 Web 会话 |

登录返回 `access_token`、`expires_at`、`actor`、`must_change_password`。Access Token 保存在内存，业务请求发送 `Authorization: Bearer <access_token>`；刷新凭据由 HttpOnly Cookie 管理，前端不读取、不放入 localStorage。初始密码需修改时，先完成密码修改并重新登录。

小程序 Token 和运营后台 Token 不能直接调用周报接口。业务 Web 登录的销售账号也不能据此进入运营后台。

## 周报接口

所有接口均须登录，且只操作本人当前公司下的周报。前端不传作者、公司、时间范围或原始记录，防止客户端改变取数范围。

| 方法 | 路径 | 请求与结果 |
|---|---|---|
| POST | `/api/v1/web/weekly-reports` | `{ "request_id": "客户端生成的UUID" }`；HTTP 202，返回周报任务 |
| GET | `/api/v1/web/weekly-reports?limit=20&offset=0` | 返回 `items`、`has_more`；limit 最大 100 |
| GET | `/api/v1/web/weekly-reports/{id}` | 查询任务、正文、原始结果、草稿版本 |
| PATCH | `/api/v1/web/weekly-reports/{id}/draft` | `{ "expected_version": 1, "body_markdown": "修改后的完整正文" }` |
| POST | `/api/v1/web/weekly-reports/{id}/cancel` | 取消本地生成任务，后续模型结果不再写入草稿 |

生成按钮每次新操作生成一个 UUID；网络重发使用原 `request_id`。同一用户重复提交这个 ID 返回同一任务。明确重新生成时使用新 UUID，保留之前的周报与人工修改。

建议每 2–3 秒查询任务详情，页面离开后停止轮询，重进页面可从列表恢复。后端有任务队列，不要求浏览器保持长连接。

### 状态处理

| `status` / `result_status` | Web 行为 |
|---|---|
| `queued` / null | 等待生成，可取消 |
| `running` / null | 生成中，可取消 |
| `succeeded` / `ready` | 展示 `body_markdown` 并允许编辑 |
| `succeeded` / `insufficient_data` | 显示“最近14天没有可用于周报的已确认跟进记录” |
| `succeeded` / `invalid_input` | 显示校验失败并联系维护人员，不展示为可用草稿 |
| `failed` | 展示生成失败；保留已有周报，由用户决定是否重新发起 |
| `cancelled` | 已取消，不展示迟到的模型结果 |

取消表示不再保存本次结果，并不承诺上游计算已停止。超时、断流、输出校验失败不会自动重试或切换其他模型。

### 返回字段

列表和生成响应：`id`、`request_id`、`status`、`result_status`、`period`、`statistics`、`input_sha256`、`draft_version`、`error_code`、`created_at`、`updated_at`、`finished_at`。

详情额外返回：

- `title`、`body_markdown`：当前可编辑草稿。无可用草稿时正文为 null。
- `original_result`：Agent 原始合法 JSON，包含原始正文、条目与来源引用，人工保存不会改它。
- `draft_source`：`agent` 或 `manual`，没有草稿时为 null。
- `draft_references_validated`：初版引用通过结构校验为 true；人工编辑后为 false。它不等于所有文字事实已完成人工核验。
- `runtime_metadata`：调用 ID、Token 用量、期望发布版本等。不返回运行 Key 或完整输入快照。
- `actual_snapshot_id=null`、`runtime_snapshot_verified=false`：当前中台 API 不提供实际运行版本证明，不把期望版本当实测版本。

时间戳包含时区；`period.timezone=Asia/Shanghai`，`period.date_basis=created_at`。`statistics` 仅用于辅助提示，正文不插入概览统计。

保存返回新 `draft_version`。HTTP 409 `WEEKLY_DRAFT_VERSION_CONFLICT` 表示另一个标签页已保存，先重新读取，让用户选择如何合并；不要直接重发覆盖。正文最大 200000 字符。渲染 Markdown 时禁用原始 HTML、危险链接和脚本。

### 错误

| HTTP / 错误码 | 处理 |
|---|---|
| 401 | 会话过期，尝试业务 Web 刷新或重新登录 |
| 403 / `BUSINESS_WEB_LOGIN_REQUIRED` | 使用业务 Web 登录入口 |
| 403 / `WEEKLY_SOURCE_ACCESS_INCOMPLETE`、`WEEKLY_ENTITY_ACCESS_INCOMPLETE`、`WEEKLY_SOURCE_ACCESS_CHANGED` | 记录或关联档案的权限/状态已变化，联系管理员确认后重新生成 |
| 404 / `WEEKLY_REPORT_NOT_FOUND` | 任务不存在，或不属于当前账号/公司 |
| 409 / `WEEKLY_DRAFT_NOT_READY` | 当前没有可保存的生成草稿 |
| 409 / `WEEKLY_DRAFT_VERSION_CONFLICT` | 重新读取并处理编辑冲突 |
| 422 / `WEEKLY_CONTEXT_TOO_LARGE` | 完整内容超出当前单次输入上限，不裁剪；由维护人员处理 |
| 422 / `WEEKLY_INVALID_SOURCE` | 输入数据不符合冻结契约 |
| 503 / `WEEKLY_AGENT_NOT_CONFIGURED` | 该公司尚未启用周报 Agent |

`failed.error_code` 中 `WEEKLY_UPSTREAM_*` 为模型或传输失败；`WEEKLY_GENERATION_REJECTED` 为绑定/契约拒绝；`WEEKLY_WORKER_INTERRUPTED` 为队列任务中断。不要根据失败响应拼接一份“成功”周报。

## 部署与维护

数据库增量迁移为 V152，新增周报、草稿版本表和业务 Web 权限。保留用户、密码、客户、商机、原有 Agent 绑定及小程序配置。内置销售、主管、总经理模板新增本人周报权限；自定义模板由管理员显式授予。

后端与 Worker 同时部署。仅在服务器的 `/etc/shenma-sales/runtime.env` 配置：

```dotenv
WEEKLY_ENABLED_WORKSPACES=<允许使用的公司UUID，多个以逗号分隔>
WEEKLY_AGENT_APP_ID=<神码周报App ID>
WEEKLY_AGENT_SNAPSHOT_ID=<期望发布版本ID>
WEEKLY_AGENT_API_KEY=<仅服务器持有的专用Key>
WEEKLY_TIMEOUT_SECONDS=180
WEEKLY_MAX_INPUT_BYTES=180000
```

通过 `deployment/configure-weekly-agent.py --binding <仅服务器可读的绑定文件> --workspace <公司UUID>` 可备份并原子更新上述配置，随后重启 API/Worker。绑定文件不得入 Git。

沿用客户 `AGENT_FDE_BASE_URL=https://ops-salesbuddy.shenzhoukuntai.com:18899/v1` 和可信 TLS 配置。后端限制周报只能发往这个客户中台地址。

通过 `python3 deployment/build-weekly-agent-spec.py --output /安全目录/weekly-agent.json` 生成可发布规范。提示词由交接原文和只针对输出结构的补充组成；`senseaudio-s2`，temperature=0，客户插件 max_tokens=4096，不挂工具和知识库。输出缺失或结构不合格时任务失败，不能用半份结果。

迁移前备份数据库、上传目录和运行配置，并演练恢复。现有升级脚本支持 `--expected-schema V151 --target-schema V152`；升级后检查 API/Worker 健康与版本。关闭周报可移除 `WEEKLY_ENABLED_WORKSPACES` 并重启服务，历史数据保留。

## Web 到位后的联调顺序

1. 业务 Web 登录、刷新、退出；确认销售账号访问运营后台被拒绝。
2. 生成一次本人周报，验证轮询、空数据、失败和取消页面。
3. 编辑并保存；两个标签页模拟版本冲突，确认旧草稿不被覆盖。
4. 用两个销售账号检查互不可见，再验证不同公司的隔离。
5. 由销售核对实际跟进、客户分组、金额、下一步计划后验收业务页面。

OpenAPI 由应用生成，见 `backend/openapi/openapi.yaml`；Agent 输入输出契约见 `backend/src/sales_backend/weekly_contract/`。
