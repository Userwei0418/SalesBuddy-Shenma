# 神码周报后端接口与 Web 联调

适用对象：神码业务 Web 前端与联调人员。周报仅在业务 Web 提供；本次不增加小程序或运营后台页面。代码位于神码独立仓库，使用神码服务器、数据库和独立中台 Agent。

## 本次接入（2026-09-25）

业务 Web 已纳入神码独立仓库的 `business-web/`。正式入口为 `/workspace/`，运营后台为 `/admin`，小程序本次不增加周报入口。部署与验收状态单独记录，不能用源码版本代替服务器版本。

本次增加共享登录接入、本周／上周筛选、完整素材分页、服务端周报历史和人工保存。后端需要数据库 V153；V152 既有周报会补齐归属周，原始输入、AI 结果、人工正文及修改历史均保留。

周报 Agent 沿用神码独立 `weekly.v2`，运行 Key 保存在客户服务器。前次真实 Agent、Worker 与 V152 部署证据见 `docs/evidence/weekly-v2-20260924/`。

## 业务口径

- 本人周报：销售、销售主管、销售总经理使用各自账号。主管、总经理本期也只生成本人周报。
- 报告归属自然周，周一至周日。默认本周；本周素材为截至生成时最近 14 个上海自然日。上周素材为截至上周日的 14 天。按系统上传时间 `created_at` 取数，不以拜访日期替代。
- `report_week` 是归属周的周一，`period` 是 14 天素材范围。历史周的客户、商机档案仍取生成时可见版本，不能理解为回放当时的 CRM 档案。
- 归属以跟进记录的销售负责人 `recorder_user_ref_id` 为准；代录人不改变归属。
- 读取已确认、已归档且未删除的完整跟进正文。实际拜访日期用于正文说明，不替代上传时间筛选。
- 只补充这些记录关联的客户、商机档案。金额保持币种基本单位，阶段用系统字典。关联档案权限缺失时明确报错，不伪装成空数据。
- 输出按客户、商机分组的可编辑中文 Markdown。AI 原文与人工修改分别保存；需要销售核对内容后使用。

## 登录与访问

部署 API 根地址：`https://salesbuddy.shenzhoukuntai.com:28899`。

业务 Web 与小程序使用同一套销售账号、密码登录和 Bearer 会话。周报功能仅在 Web 界面提供，不以另开账号或登录接口区分端。

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/v1/auth/password/login` | `{ "account_code": "XS001", "password": "用户输入" }` |
| GET | `/api/v1/auth/me` | 当前身份与权限 |
| POST | `/api/v1/auth/refresh` | `{ "refresh_token": "登录返回值" }` |
| POST | `/api/v1/auth/password` | `{ "old_password": "原密码", "new_password": "新密码" }` |
| POST | `/api/v1/auth/logout` | 撤销当前会话 |

登录返回 `access_token`、`refresh_token`、`actor`、`must_change_password`。请求携带 `Authorization: Bearer <access_token>`；Web 通过同源 `/api/v1` 访问，沿用已交接的 `apiClient` 刷新及身份切换保护。初始密码需要修改时先完成密码修改。

周报继续按 `weekly_report.*` 权限和公司／本人范围鉴权，登录成功不授予运营后台权限。V152 的 `/web/auth/*` 保留兼容已有会话，当前 Web 不再调用它。

## 周报接口

所有接口均须登录，且只操作本人当前公司下的周报。前端只选报告归属周，不传作者、公司、任意时间范围或原始记录。服务端确定本人数据及 14 天素材窗口。

| 方法 | 路径 | 请求与结果 |
|---|---|---|
| POST | `/api/v1/web/weekly-reports` | `{ "request_id": "客户端生成的UUID", "report_week": "2026-09-21" }`；HTTP 202，返回周报任务 |
| GET | `/api/v1/web/weekly-reports?limit=20&offset=0` | 可选 `report_week=YYYY-MM-DD`；返回 `items`、`has_more`、服务端 `current_week`；limit 最大 100 |
| GET | `/api/v1/web/weekly-reports/sources?report_week=2026-09-21&limit=50&offset=0` | 预览本人完整素材，返回 `items`、`total`、`has_more`、`next_offset`；不截断正文 |
| GET | `/api/v1/web/weekly-reports/{id}/sources?limit=50&offset=0` | 本次周报实际使用的不可变素材快照；仍校验当前读取权限 |
| GET | `/api/v1/web/weekly-reports/{id}` | 查询任务、正文、原始结果、草稿版本 |
| PATCH | `/api/v1/web/weekly-reports/{id}/draft` | `{ "expected_version": 1, "body_markdown": "修改后的完整正文" }` |
| POST | `/api/v1/web/weekly-reports/{id}/cancel` | 取消本地生成任务，后续模型结果不再写入草稿 |

生成按钮每次新操作生成一个 UUID；网络重发使用原 `request_id`。同一用户重复提交这个 ID 返回同一任务；相同 ID 指定不同归属周返回 409。生成响应未知时保留 ID，避免重试创建重复任务。明确重新生成时使用新 UUID，保留之前的周报与人工修改。

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

列表和生成响应包含 `report_week`、`report_week_end`、`source_cutoff_at`（上传截止时间）、`snapshot_at`（实际取数时间），以及 `id`、`request_id`、`status`、`result_status`、`period`、`statistics`、`input_sha256`、`draft_version`、`error_code`、`created_at`、`updated_at`、`finished_at`。

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
| 401 / 登录失效 | 使用与小程序相同的密码登录和刷新流程 |
| 403 / `WEEKLY_SOURCE_ACCESS_INCOMPLETE`、`WEEKLY_ENTITY_ACCESS_INCOMPLETE`、`WEEKLY_SOURCE_ACCESS_CHANGED` | 记录或关联档案的权限/状态已变化，联系管理员确认后重新生成 |
| 404 / `WEEKLY_REPORT_NOT_FOUND` | 任务不存在，或不属于当前账号/公司 |
| 409 / `WEEKLY_DRAFT_NOT_READY` | 当前没有可保存的生成草稿 |
| 409 / `WEEKLY_DRAFT_VERSION_CONFLICT` | 重新读取并处理编辑冲突 |
| 422 / `WEEKLY_CONTEXT_TOO_LARGE` | 完整内容超出当前单次输入上限，不裁剪；由维护人员处理 |
| 422 / `WEEKLY_INVALID_SOURCE` | 输入数据不符合冻结契约 |
| 503 / `WEEKLY_AGENT_NOT_CONFIGURED` | 该公司尚未启用周报 Agent |

`failed.error_code` 中 `WEEKLY_UPSTREAM_*` 为模型或传输失败；`WEEKLY_GENERATION_REJECTED` 为绑定/契约拒绝；`WEEKLY_WORKER_INTERRUPTED` 为队列任务中断。不要根据失败响应拼接一份“成功”周报。

## 部署与维护

数据库 V152 新增周报、草稿版本表和权限；本次 V153 增加报告归属周及素材截止时间。保留用户、密码、客户、商机、原有 Agent 绑定及小程序配置。内置销售、主管、总经理模板新增本人周报权限；自定义模板由管理员显式授予。

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

## Web 构建与部署

1. 在独立神码仓库执行 `cd business-web && npm ci --ignore-scripts && npm run verify`。
2. 提交并审核源码后，再次构建，使 `dist/build.json` 的版本与本次提交一致。
3. 使用 `python3 scripts/package_source.py --include-business-web --output-dir <仓库外目录>` 打包。源码包不含数据库数据、账号密码或模型 Key。
4. 按 `deployment/upgrade-sales.py` 从 V152 升级至 V153。该流程备份、恢复核对既有数据库，再迁移并切换 API／Worker；出错恢复旧版本。
5. 业务 Web 静态文件由 API 服务直接提供，无需在客户服务器新增 Node.js 服务。访问 `/workspace/`，登录后进入“周报”。本地开发的 Node 服务仅用于代理和调试。

## 本期边界

- Web 正式模式调用服务端周报与 Agent；显式 `?mode=preview` 仍为独立合成示例，不作为真实接口验收。
- 素材预览不等于生成快照。生成后左侧改为本次实际使用的素材；后续补录不会改写已有周报。
- 当前 `weekly.v2` 要求每条记录有客户且商机关联不歧义。无客户或关联多个商机的历史记录会明确阻止生成，不能擅自忽略或归并。须先确认业务归属规则再扩展契约。
- 神码新增商机／跟进字段、选项编码和 CRM 同步协议尚待正式映射，本次保留现有字段含义。
