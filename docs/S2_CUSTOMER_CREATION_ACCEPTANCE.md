# S2 客户建档申请与运营审批验收

## 当前状态

- 验收日期：2026-10-15；范围为神码独立仓库的 S2 增量，迁移编号 `V160`。
- S2 代码、接口、双端入口和本机隔离验收已完成；尚未部署 S2、执行线上 V160 迁移或完成客户业务验收。
- 2026-10-15 15:32 左右线上只读复核：`GET /api/v1/health/version` 返回 revision `094841a60c446d88febfdc199be5d0877424193a`、`expected_schema=V159`；就绪检查 `status=ok`，database/model_gateway/auth 均为 true。本轮无线上写入。
- 隔离测试只使用本机 PostgreSQL、新建临时库、合成人员和业务数据；没有使用正式公司账号、模型 Key、CRM 或飞书连接。
- API/仓储验收在事务内 `SET LOCAL ROLE`，断言真实运行角色 `NOSUPERUSER`、`NOBYPASSRLS`，开启行级权限；维护连接仅初始化、迁移和清理临时库。

## 使用流程与角色

- 申请人：销售、主管、销售总经理的标准角色模板获得本人范围 `customer.create_request`。填写客户名称、公司客户编号 / 审批单号、所属团队和联系人等资料后提交。
- 审核人：运营、管理员的标准角色模板获得本公司范围 `customer.create_review`。在销售后台核实编号和资料后通过或驳回；驳回必须填写原因。
- 认领人：通过后客户进入待认领客户池，再走已有“客户认领申请 → 运营审批”流程。建档通过不代表客户已归属申请人。
- 公司客户编号 / 审批单号必填；填写和审核不等于已接通集团 CRM 自动校验。
- 自定义角色、已自定义的角色授权及账号 override 保持原样，不自动套用新增标准权限。现有运营直接创建客户的权限与入口保留。
- 提交时只新增建档申请，不写客户主档。审批通过在同一事务创建一条正式客户及关联资料，并完成申请状态；失败整体回滚。
- 销售不能直接建档或审批，不能查看、撤回其他销售的申请；跨公司不能读写。前端隐藏入口之外，API 和数据库均执行权限限制。

## 界面入口与异常处理

- 小程序：客户页 → “客户建档 · 申请与进度” → “填写申请 / 我的申请”。保存草稿、提交、查看审核结果和撤回待审申请均在此入口。
- 业务 Web：客户页进入同一建档申请流程，支持桌面和窄屏布局；有直接建档权限的运营仍进入“创建客户”。
- 销售后台：进入建档申请审核页，默认查看待审队列，可切换历史状态、查详情、通过或填写原因驳回。无审核权限时不显示入口，伪造页面地址也不能进入。
- 待审申请可由本人撤回，状态变为 `cancelled`，不会生成客户；驳回后状态为 `rejected`，同样不生成客户。
- 已通过、驳回或撤回的原申请不再修改或重复审批；需要补充资料时重新发起申请。
- 提交或审批失败保留输入以便重试；相同幂等键重放返回已有结果，不重复建档。重复名称或公司编号须先核实已有客户/申请。
- 我的申请中通过记录提供“前往客户认领”，不会自动认领；审核结果包含状态、时间和审核原因。
- 上述为本地候选实现的入口说明，不能据此认为线上已出现这些入口。

## 验收结果与证据层

- 小程序源码自动化：869 项通过；覆盖申请模式、权限、必填字段、历史、撤回、失败恢复等，不等同于微信真机验收。
- 业务 Web `verify`：142 项通过，包含构建及测试。
- 业务 Web 浏览器：3 项通过，覆盖销售 1440/390 宽度及运营直接建档；浏览器使用本地合成 API 回应，验证交互而非真实数据库链路。
- 后台浏览器：5 项通过，覆盖审批/驳回、错误重试、历史、1440/390 宽度及无权访问；同样使用本地合成 API 回应。
- HTTP + 真实 PostgreSQL：60/60 通过，其中 4 项 S2 接口测试及 56 项已有客户、认领、认证与后台会话回归。通过真实 ASGI 路由、认证、仓储、事务与 RLS；未启动公网服务或调用外部模型。
- SQL/仓储：13 组通过，覆盖角色矩阵、本人/跨公司隔离、重复申请、并发同申请、同名/同编号竞争、两处故障注入回滚及旧认领审批。
- V159 → V160 升级：6 组通过；103 张既有 workspace 数据表的行数及内容指纹不变。标准角色只增加预期授权，自定义角色/override、旧客户团队及合成飞书配置保留；幂等重放与后建运行角色授权回补通过。
- OpenAPI 已重新导出；合同、权限及 OpenAPI 相关 4 个测试文件合计 21/21 通过。
- S2 独立验收先在临时库 V160 通过；永久 HTTP 脚本复跑时工作区已存在下一批 V161，兼容复跑 60/60 通过（9.97 秒），临时库与角色清理后均为 0。两者均不是线上迁移证据。
- 结构化证据见 [S2_ISOLATED_20261015.json](evidence/S2_ISOLATED_20261015.json)；提交号及后续发布结果由独立发布记录补充。

## 复跑入口

- 在本仓库根目录执行。先按 `backend/uv.lock` 准备开发依赖；以下 `PYTHON` 指向该 Python 环境，`PGHOST/PGPORT/PGUSER` 仅指向本机可创建临时库和角色的 PostgreSQL 维护连接。
- HTTP、SQL 和升级脚本分别自行创建并清理临时数据库/角色；不要把正式库 DSN 作为测试参数。HTTP 子进程限时 180 秒，超时先终止本次测试进程组，再由 runner 清理数据库和角色。
- 小程序源码测试：`node --test frontend/tests/*.test.js`。
- 业务 Web 构建与单测：`npm --prefix business-web run verify`。
- 业务 Web 浏览器：`node --test business-web/tests/customer-creation-s2.browser.mjs`。
- 后台浏览器：`node --test business-web/tests/customer-creation-console.browser.mjs`。
- 浏览器测试需要本机 Chrome 与 Playwright；可通过 `PLAYWRIGHT_MODULE` 指向既有 Playwright 模块。
- HTTP + PostgreSQL：`"$PYTHON" backend/tests/system/verify_customer_creation_requests_api.py`。
- SQL/仓储：`"$PYTHON" backend/tests/system/verify_customer_creation_requests_postgres.py`。
- 升级保留：`"$PYTHON" backend/tests/system/verify_customer_creation_requests_upgrade.py`。
- OpenAPI 一致性：`"$PYTHON" backend/scripts/export_openapi.py --check`。
- HTTP 永久入口固定执行以下 8 个 integration 文件，无需 `/tmp` 包装脚本：
  - `test_customer_creation_requests_api.py`
  - `test_operations_claims_sql.py`
  - `test_authorization_customers.py`
  - `test_authorization_api.py`
  - `test_customer_claim_pool.py`
  - `test_claim_concurrency.py`
  - `test_customer_profile_management.py`
  - `test_console_session_binding.py`
- 本轮实际 Python 为 `/tmp/shenma-s2-backend-venv/bin/python`，缓存目录 `/tmp/shenma-s2-isolated-pycache`；这些是本轮环境位置，不是永久入口的硬编码依赖。

## 尚未执行与下一批门槛

- 尚未执行 S2 线上迁移、服务更新、线上真实业务写入或客户业务验收；没有修改正式公司的账号、配额、现有客户或飞书目标。
- 尚未进行微信开发者工具上传、体验版更新、真机录入及微信审核/正式发布；源码自动化不能替代这些步骤。
- S2 不新增集团 CRM 同步、飞书推送或周报能力；已有连接/config 保留的证据来自合成升级夹具，不代表重新验收了外部服务。
- S3 已获准进入独立本地实现：先固定 S2 提交与验收证据，再接入共享路由/权限文件；S3 使用独立 V161 迁移和角色/隔离/升级回归，不混入 S2 发布包。
- 上线前仍须记录最终提交与产物指纹，核对正式环境备份、运行角色和迁移计划，再按对应发布范围完成部署与回读。
