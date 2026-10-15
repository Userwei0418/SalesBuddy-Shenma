# 商机财务概览与来源核对（V200）

本次在商机主表增加可计入的累计实际确收、实际回款、小计和数据质量。既有逐笔实绩、历史季度原值、季度预测三张明细表保留。不会把历史季度汇总与同季度逐笔金额相加，不改小程序/Web既有资产接口的统计规则。

## 口径

- 按公司、商机、年、季度、类型（确收/回款）核对。累计指全部已登记期间，按逐笔真实发生日或历史原季度归期，不随当前日期暗中排除历史期间。
- 仅有效逐笔记录参与逐笔合计；已作废记录保留展示并参与证据指纹。相同金额的两笔独立记录仍是两笔真实事实，不按金额去重。
- 一个期间仅有逐笔来源，或只有一条历史汇总时，自动采用该来源，状态为“唯一来源”。无数据保持空值；原始 0 保持 0。
- 同期存在历史和有效逐笔，或多条历史汇总时，默认待核对，即使金额相同也不自动相加或挑选。管理员/运营必须选择逐笔合计，或者明确选择一条历史证据。
- 后续任何影响期间证据的变更，使旧确认失效。API和同步消费者每次按当前完整证据重新计算指纹，不把上次的选择永久套到新记录。
- 某金额类型只要一个期间有冲突，该类型正式累计为空；另列“可计入小计”和待核对期间数。小计包括唯一来源以及已人工确认期间，**不代表已全部人工核验**。
- 金额用 Decimal 计算，API返回元的十进制字符串。仅元/万元换算，不推算税额；历史未知税口径、混合含税/不含税、未记录税口径的逐笔确收均显示提示。
- 客户级实绩没有商机ID时不分摊给该客户的任一商机。没有登记的其他期间不虚构零，也不声称所有期间财务已经完整。

## 接口

`GET /api/v1/opportunities/{id}/financial-overview` 与管理后台同名路径
`GET /api/v1/console/opportunities/{id}/financial-overview` 返回同一口径：

| 字段 | 含义 |
| --- | --- |
| totals.recognized / totals.collection | 每类的 amount、confirmed_subtotal、conflict_period_count、resolved_period_count、tax_unknown_period_count |
| periods | year、quarter、kind、amount、source、status、source_fingerprint、entry_amount、entries、history、decision、tax_basis、tax_unknown |
| decision | 选择的来源、历史证据ID、确认理由、确认人、时间、version_no；失效后仍保留上次确认记录 |
| can_confirm | 当前真实管理身份与金额操作范围共同判断的确认能力 |
| review_path | 不含凭据的同站受控核对入口 |

读取必须同时获得商机读取和客户/商机财务读取范围。仓储以一条SQL读取三组证据，RLS仍生效。

`POST /api/v1/console/opportunities/{id}/financial-sources/confirm` 必须携带 `Idempotency-Key` UUID。
请求仅接受 `year/quarter/kind/source/selected_history_id/expected_fingerprint/expected_version/reason`。
历史来源必须指定同公司、同商机、同期间、同类型的一条历史记录；逐笔来源不得提供历史ID。
请求不接受金额、公司ID、确认人ID，身份来自已登录会话。

真实有效管理员/运营身份、`access.console`、客户和商机双范围 `actual.create` 缺一不可；普通销售、主管或经理即便获批录入实绩，也不因此获得来源核对权。只允许保存来源决议，不改原始单据，不新增群通知。

确认在同一事务内锁商机 `FOR NO KEY UPDATE`，与V200财务变更触发器的锁兼容；再次核对完整证据指纹及当前决议版本，冲突返回409。幂等回放前重新检查当前对象与管理权限，权限被撤回后不能通过旧回执读取数据。保存和回执共同提交，理由和前后决议进入现有不可变审计记录。

## 管理后台与飞书

后台“商机管理 → 详情 → 财务核对”展示每个期间的原始明细、采用来源、质量和上次核对人/理由。点击核对只选择证据来源，不输入新金额。

可复制深链格式：`/admin?company=<公司代码>&finance=<商机UUID>#opportunities`。先走正常后台登录和公司核验，再受控打开核对界面；无登录票据或跨公司访问捷径。

飞书商机源字段仅新增：

- `actual_recognized_total`、`actual_collection_total`
- `actual_recognized_confirmed_subtotal`、`actual_collection_confirmed_subtotal`
- `finance_conflict_period_count`、`finance_quality`

源数据由V200补充三组raw数组，映射投影调用与API相同的纯聚合模块。金额变化/来源失效会改变现有映射指纹；冲突空值显式清除远端旧金额。实际/历史/决议变更只排对应商机事件，保留子表自身事件，不增加父读子关系导致的同步循环。

本年预测与明细反查入口由飞书公式配置，使用现有预测行的年/季维度与 `YEAR(TODAY())`，避免跨年无写入事件时复制的“本年”金额过期。财务核对绝对链接也由部署时的V4公式配置，不把某个部署域名硬编码进后端。公式、字段配置和线上映射验证由单独V4配置交付覆盖。

## 验证边界与命令

运行基线为当前源码，依赖数据库V200。旧本地演示库未改动。独立PG验证脚本只接受显式本地Unix socket，创建随机空库，依次应用V195与V197～V200（不要求V196），使用安装清单中的受限API角色ACL，结束销毁数据库和角色。脚本不会读取保存的密码或生产DSN。

```bash
backend/.venv/bin/python -m pytest backend/tests/test_opportunity_finance.py backend/tests/test_opportunity_finance_console.py backend/tests/test_route_permissions.py backend/tests/test_openapi_contract.py -q
PYTHONPATH=backend/src backend/.venv/bin/python backend/scripts/export_openapi.py --check
PGHOST=<本地socket> PGPORT=<端口> PGUSER=<本地维护角色> PYTHONPATH=backend/src backend/.venv/bin/python backend/scripts/verify_opportunity_finance_postgres.py
```

本地单测/隔离PG/管理表单脚本通过，不代表生产数据库已升级或远端V4公式已配置；生产发布与实际飞书验收需另记证据。

本次本地结果：规则/后台表单/OpenAPI/权限覆盖及原商机金额表单共 23 项通过；既有飞书投影、消费、无变化结案和周任务通知 120 项回归通过；受限真实 PG 专项 12 组通过、0 跳过。PG 中 HTTP 使用合成已验证身份依赖与真实RLS仓储，认证登录本身由既有鉴权测试覆盖，不冒充生产真人登录验收。回执见 `backend/docs/evidence/opportunity-finance-v200-20260929.json`。
