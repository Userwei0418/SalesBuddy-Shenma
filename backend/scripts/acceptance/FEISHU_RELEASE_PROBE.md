# 飞书上线验收探针

这是现有飞书连接的**外部投影与通知传输验收**。工具不创建或修改 CRM 客户、商机、任务，不写同步队列、映射表或消息发送账本。它不能证明真实业务 API、数据库触发器与 Worker 的整条链路；该证据必须另用隔离数据库和演示租户的实际业务流程提供。

2026-09-28：v2 已完成真实只读预检，但 `run` 在首次配置检查时报 `LIVE_CONFIGURATION_CHANGED`；当时 `STATE.status=prepared`、`records={}`、`steps={}`，没有执行外部写入。本版修复配置集合序列化产生的跨进程假漂移；修复和离线测试不代表真实飞书验收已经通过。

同日 v3 创建客户后在读回阶段停止，状态保留 `customer:initial=unknown`。独立只读查询确认该合成客户存在，25 个映射字段及字段哈希与冻结计划一致；一次原读取流程复现确认字段查询和系统 ID 搜索成功，单记录 GET 返回 `FEISHU_99991672`。本次修复改为按同一系统 ID 搜索并读回映射字段，未改变 v3 计划、字段哈希或写入范围。12:08 CST 独立冻结验收包已将原客户接管为 verified，没有新增外部业务写入；线上 release 未覆盖。

12:09 CST 同计划剩余流程在商机初始投影读回停止，保留 `opportunity:initial=unknown`；任务、卡片和归档步骤未进入。只读诊断发现搜索接口将关联字段返回为 `{"link_record_ids": [...]}`，空关联为 `{}`，旧规范化只支持列表。现仅为类型 18/21 增加严格结构与合法 record ID 校验，未知结构仍拒绝。12:17 CST 对同一商机的只读核对确认新规则下 34 个字段全部匹配，关联客户确为原合成记录，PLAN/STATE 均未改动；这不等于商机已接管或整批验收完成，后续接管、任务、发卡及归档仍须按冻结计划单独授权执行。

## 固定范围

- 一个明确指定的公司、一个启用且已验证当前版本的连接；沿用其 App、Base、现有客户/商机/任务三张表和默认通知群。
- 计划中预先生成三个随机 UUID，各对应一条客户、商机、任务投影。名称均带 `【上线验收】` 和同一批次标记。
- 客户和商机不关联真实成员；任务仅关联本计划的合成客户和商机。任务负责人文本也明确为合成样本。
- 商机金额从创建到归档始终为 **0 元**，只把预计关单日期从计划基准时间之后第 14 天改为第 21 天，并逐次读回。最多发送两张卡片：新增和预计日期更新；两张只能发到同一个已配置的默认群。不测试按部门分群路由，也不创建原生飞书任务助手任务。
- 任务从创建开始即为 `cancelled`，验收状态为 `not_required`，不写待验收或已完成状态；四个计数从创建到归档始终为 **0**。结束后按任务、商机、客户顺序归档。消息保留以供核验，不撤回。
- CRM 核心数据和业绩始终零写入；本批飞书投影对金额求和、四项任务完成率计数均无增量。原始记录行数仍会增加，自定义 COUNT、日期分组或自动化可能看到这三条已标注记录；不承诺所有自定义报表无影响。记录以固定系统 ID 和批次识别、按原计划退役，不覆盖人工记录。本工具不修改既有公式、视图、仪表盘或自动化。
- 本轮只证明预计日期更新的投影和卡片，不声称验证了真实完成率变化或大额差值展示；金额格式/差值、任务待验收→最终完成计数沿用同候选隔离测试与演示业务流程的独立证据。

## 前提

在同一份已审查的候选发布目录及其运行环境执行；不要一边运行一边替换源码。运行身份使用现有专用 `FEISHU_SYNC_DATABASE_URL`，凭据解密沿用服务原有适配器，不允许将密钥、连接串或数据库内容写入命令、计划、控制台或仓库。CLI 强制 PostgreSQL 会话默认只读，并验证当前角色是无 CRM 写权限的专用飞书角色；不会取队列租约或调用 reconciliation。

连接必须已启用、当前 revision 已验证，且公司和飞书模块可用；必须启用商机新增及更新通知，并配置默认群。三个表互不相同，系统 ID 字段为文本，关联字段指向已配置且启用的正确父表。下面的源字段必须已有映射：

| 表 | 必需源字段 |
| --- | --- |
| 客户 | `name`、`record_status` |
| 商机 | `name`、`record_status`、`customer_id`、`amount`、`currency`、`expected_close_date` |
| 任务 | `title`、`record_status`、`status`、`customer_id`、`opportunity_id`、`batch_id`、`batch_name`、`owner_name`、`verification_status`、`active_count`、`completed_count`、`pending_review_count`、`ontime_completed_count` |

任务四个计数字段必须为数字类型。预检仅检查并拒绝缺项，不自动创建字段、修改连接配置或开放权限。飞书应用还需具备现有多维表格读写、群信息读取、发卡和消息读取能力；缺权限即停止，不能将发送成功视为已完成读回验收。

记录回读使用精确系统 ID 条件的 `POST records/search`，仅请求系统 ID 和当前映射字段；该接口支持细粒度的 `base:record:retrieve`。不再额外调用要求 `bitable:app` 或 `bitable:app:readonly` 的单记录 GET，也不为探针扩大应用权限。两种接口的权限要求见飞书官方[搜索记录](https://open.feishu.cn/document/docs/bitable-v1/app-table-record/search)和[获取记录](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/get)。

## 操作步骤

以下命令中的路径及 UUID 必须换成此次审批范围内的实际值。先在仓库外建立仅当前用户可访问的绝对路径目录，权限 `0700`；目录及路径不能是软链接。保留该目录直至完成回执归档。

```bash
PYTHONPATH=backend/src python backend/scripts/acceptance/feishu_release_probe.py prepare \
  --directory /absolute/private/probe-directory \
  --workspace WORKSPACE_UUID --connection CONNECTION_UUID
```

`prepare` 只读取现有配置、表字段、默认群与三个新系统 ID 的存在性，并生成本地 `PLAN.json` 和 `STATE.json`，没有外部业务写入。计划冻结 workspace、connection、完整配置指纹（含 revision）、凭据版本指纹、表和字段指纹、三个 UUID、默认群及基准时间。配置指纹只对 `notification.on_create`、`on_update` 两个无序集合排序；部门路由、群列表等有序值及全部映射和 revision 仍原样参与核验，不忽略真实变更。确认计划后，用返回的完整 SHA256 执行：

```bash
PYTHONPATH=backend/src python backend/scripts/acceptance/feishu_release_probe.py run \
  --directory /absolute/private/probe-directory --plan-sha256 REVIEWED_PLAN_SHA256
```

每次写入前重新核对现有配置和字段，先持久记录 `unknown` 再发请求。每次记录变更均通过固定系统 ID 精确搜索并读取完整映射字段，核对目标记录 ID 和字段。搜索限定 `page_size=2`，多条结果或 `has_more` 均拒绝，返回的系统 ID 必须一致，已有记录 ID 不得被替换。消息收到稳定 ID 后再读回核对群和卡片类型。成功终点为 `passed_and_retired`，不只是 HTTP 成功。

本地状态可随时读取，不连接数据库或飞书：

```bash
PYTHONPATH=backend/src python backend/scripts/acceptance/feishu_release_probe.py status \
  --directory /absolute/private/probe-directory
```

## 中断与恢复

同一目录用非阻塞文件锁拒绝并行执行，检查点通过同目录原子替换与 fsync 保存。不要删状态、手改 UUID、复制计划开第二次运行或更换目录重发。

- **记录请求结果未知：** 重跑同一 `run` 只先按固定 ID 读回；内容完全吻合才采用原结果，不再次创建/更新。未找到或内容不一致仍保留未知，需人工核对，不能宣称没有写入。
- **读取接口修复后接续 v3：** 保留原 PLAN、STATE 及故障回执，核对修复仅改变读取方式、没有改变冻结投影或写入语义；使用经审查并明确授权的独立验收候选，保持同一目录、UUID 和原计划哈希。由工具再次核对配置、字段和远端内容后接管未知结果，不手改 STATE，不重新 prepare 或覆盖在线 release。修复通过离线测试不等于原验收已恢复。
- **消息结果未知但没有 message ID：** 禁止再次发送；保留未知并核查群内回执。若有稳定 message ID，只重试读回，不重发。
- **计划准备阶段崩溃且缺失 PLAN 或 STATE：** 尚未执行任何外部变更，但工具会拒绝继续；保留故障目录，检查后重新准备独立计划，不能手造检查点。
- **旧版计划：** 本版 `PLAN.version=3`，严格拒绝 v1 和 v2。v1 的金额/完成率写入语义不同；v2 的配置指纹受进程哈希种子影响，不能按 v3 指纹重新解释。不得手改 version、config_hash 或 plan hash，不通过固定哈希种子绕过检查。先保留旧目录和冻结工具核对原 PLAN/STATE：确认仍为 `prepared` 且 `records={}`、`steps={}` 的零写计划，可在新的私密目录重新 prepare，并核对新计划摘要；已有记录、步骤或未知请求结果的旧计划必须先对账，必要时按原计划精确退役，不得新建计划重发替代。当前版本升级不是同一计划可以换代码重跑的许可。
- **配置、凭据版本、映射、字段或已记录的远端内容改变：** 停止而不覆盖。需要负责人核对原计划和变更；不得通过替换 plan hash 绕过冻结约束。

放弃后续测试但需要归档已经产生的投影时，使用同一计划：

```bash
PYTHONPATH=backend/src python backend/scripts/acceptance/feishu_release_probe.py retire \
  --directory /absolute/private/probe-directory --plan-sha256 REVIEWED_PLAN_SHA256
```

`retire` 先尝试对未知记录作精确读回，再只归档已证实属于本计划且未被人工修改的记录，不发送待发卡片。一次失败仍继续处理其余对象。`retired` 只表示投影归档完成，可能仍保留未知消息，不能写成完整验收通过；`retirement_incomplete` 必须继续核查。已放弃并进入归档的计划不能重新从头运行。

全部通过后保留私密原始 PLAN/STATE，另写脱敏回执：候选提交、配置 revision、三条记录读回结果、预计日期前后值、金额始终为零、任务四计数始终为零、两张卡片状态及最终归档结果。不将环境变量、密钥、凭据密文或真实业务数据复制进回执。浏览器视觉展示、真实业务触发器和 Worker 链路仍须分别记录；原生飞书任务助手不属于本期范围。

## 本地验证

此前七个专项测试文件共 **227 passed**，是收敛投影范围前的历史回执；v2 探针自身历史回执为 **29 passed**，v3 指纹修复后的单文件历史回执为 **35 passed**。读取权限修复阶段为 **44/244 passed**；补齐关联读回结构后探针自身 **87 passed**，下列七文件联合复测 **287 passed**（2026-09-28，本机离线，包含前述 87 项，不累加）。新增缺少单记录 GET 权限、关联读回结构不匹配时创建已提交的故障复现，验证沿用原 v3 计划接管未知结果且没有新增外部写入；覆盖空关联、合法关联、非法结构/ID、关联客户被替换或清空仍拒绝。另覆盖搜索重复、分页仍有结果、系统 ID 错误或缺失、记录 ID 非法或被替换、冻结字段缺失或被修改时继续拒绝。保留独立进程 `PYTHONHASHSEED=1/2/3` 的 prepare→guard、真实配置变化拒绝、v1/v2 提前拒绝、全程零金额/零任务计数、预计日期更新卡、消息禁止重发、只读连接、文件锁、检查点损坏和归档恢复覆盖。测试全部使用离线替身，不调用外部飞书或生产数据库。

```bash
PYTHONPATH=backend/src:backend python -m pytest \
  backend/tests/test_feishu_release_probe.py \
  backend/tests/test_feishu_sync_service.py \
  backend/tests/test_feishu_sync_projection.py \
  backend/tests/test_feishu_sync_policy.py \
  backend/tests/test_feishu_sync_transport.py \
  backend/tests/test_feishu_update_service.py \
  backend/tests/test_task_import_feishu_projection.py -q
```
