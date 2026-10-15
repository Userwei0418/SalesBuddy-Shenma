# 历史 CRM 导入写入器

`services/crm_history_apply.py` 承接冻结预检结果；`scripts/crm_history_apply.py` 是显式运行入口。代码和隔离验证已具备，**这不代表已执行生产迁移或导入**。真实材料、manifest、运行回执均留在仓库外；库内测试只使用合成数据。

## 输入与审批边界

输入 JSON 必须为以下包装结构，完整文件以与预检相同的 `canonical_hash()` 算法生成 SHA256；执行时必须明确传入经审核的该哈希、工作空间、运营／管理员账号 UUID 及角色。服务核实角色有效，数据库连接必须为无超级用户、无 BYPASSRLS 的运行角色，不读 `.env` 或自动选择数据库。

```json
{
  "schema_version": 1,
  "source_snapshot_at": "2026-09-22T12:00:00+08:00",
  "plan": {"schema_version": 1, "workspace_id": "目标公司 UUID", "records": []},
  "execution_contract": {
    "version": "crm-history-20260922-v1",
    "data_kind": "production",
    "financial_year": 2026,
    "visit_form_version_id": "现场核实的有效表单 UUID",
    "stage_map": {},
    "sales_channel_map": {},
    "allow_period_actual_snapshot_create": true,
    "allow_verified_legacy_forecast_update": true,
    "owner_team_by_account": {}
  }
}
```

示例中的空 records、stage_map 只是结构说明，不是可执行导入文件。实际 plan 含来源稳定 ID、原字段哈希、规范化值、changes/additive、预期目标版本、异常和关联；stage_map 必须来自本次审核的源状态映射。多任职账号如不能唯一确认主团队，需在 `owner_team_by_account` 提供本次已审核且仍有效的业务任职 ID，不能按名字猜团队。

## 默认仅暂存

```bash
backend/.venv/bin/python backend/scripts/crm_history_apply.py \
  --manifest /批准材料/apply-manifest.json \
  --approved-sha256 批准的完整文件规范化哈希 \
  --workspace 公司UUID --actor 操作员UUID --role operations \
  --host 明确数据库地址或socket --port 5432 \
  --database 明确数据库名 --user 非绕过RLS运行角色 \
  --report /受控目录/stage-report.json
```

该命令只写来源、批次和审核暂存账本，不写客户、商机或跟进。正式业务执行还须显式添加 `--apply`，前提是数据库迁移及导入清单已完成审核。它按伙伴、客户、商机、跟进顺序处理；每个对象和它的金额子记录是同一个事务，失败会回滚并记为 conflict，其余独立记录可继续。

## 确定性写入规则

- 每条源记录用系统、Base、表和真实记录 ID 识别；派生季度对象增加 `source_item_key`。名称不作合并键。源原哈希保存在 decision；幂等哈希包括原哈希、规范化业务意图和规则版本，不包括操作人、时间、before 值、版本号或 create/update 状态。
- `can_execute=false` 的记录仅暂存。依赖必须在本批已 applied/unchanged，不能借上次导入的旧 binding 绕过本次父对象失败。
- 插入客户不编造公司编号、核实时间、审批、跟进或活跃状态。只有源姓名严格唯一且账号在职的新增／明确未认领客户可以恢复归属；已认领客户保留。`history_restored` 事件独立留痕，无伪认领审批或通知。
- 普通关联伙伴写 `crm.opportunity_related_partner`；签单方式单独保存，源空保留 unknown。通用伙伴不写成具体转售方，不改已有 `partner_id` / `partner_name`。
- 商机保留未知 ACV、原销售空值、原负责人文本、暂时交接依据、年季度关单精度。确认当前负责人时同步核实主业务团队，不能沿用前任团队。
- 跟进原作者、正文、日期、表单和归档快照不被后续重导覆盖。已有跟进只处理 plan 显式 changes/additive 白名单中的管理人、首次原作者文本和缺失伙伴主体；空源关联不会擦除已有关系。`interaction_on` 写为原上海业务日；`recorded_on` 只取源创建日，系统 created_at 是本次真实写入时间。
- 跨商机跟进保存一个 visit 和多条关系；多商机的旧 opportunity_id 为空，跨客户的旧 customer_id 为空，个人次数仍只算一条。没有明确主体或实际原作者文本的记录留待核对。
- Q3/Q4 预测使用元金额，空白和零不同，回款信心度不加权。旧预测只有在真实历史导入基准与本次当前完整快照匹配时可更新，数据库事务再次加锁比较完整行；缺基准不覆盖。Q1/Q2 季度原值保存原金额、万元单位、年季度和税口径，不制造逐笔发生日或写入 customer_actual。
- 所有写入启用事务局部历史标记，仅产生历史同步事件，不调用日常 AI 编排或群通知。源 FDE 名单等未完成正式关系承接的字段保留 raw/normalized 和待处理状态，不声称已成为正式参与关系。

## 回执与验证

回执分别列出业务父对象 outcomes、逐条金融子对象结果、`object_counts`（本次新建／更新／未变化／冲突／隔离）和 `ledger_counts`（来源台账状态）。`business_data_written` 只统计本次成功提交的业务变更；仅建立来源绑定、重复执行、回滚不算写入业务。批次 `applied` 表示本轮执行结束，**不表示所有记录都成功**，仍需按 conflict/quarantined 清单处理。重复执行相同审批 manifest 不重复插入或覆盖后续编辑；修改材料应重新预检并生成新审批哈希。

导入表单须同时符合跟进对象类型、当前公司或全局范围、启用状态及当前有效期；仅有一个启用的表单 ID 不能通过校验。

```bash
PYTHONPATH=backend/src backend/.venv/bin/pytest -q backend/tests/test_crm_history_apply.py
PGHOST=/tmp/本机隔离PGsocket PGPORT=55438 PGUSER=postgres \
  backend/.venv/bin/python backend/scripts/verify_crm_history_apply_postgres.py
```

隔离验证会创建随机临时数据库和不绕过 RLS 的角色，验证结束删除；不读取生产配置、不连接生产。覆盖暂存无业务写入、批准源与租户校验、客户历史归属、伙伴与转售分离、多客户多商机、原作者和日期、未知金额、旧预测基准、重复导入与冲突保护、无 AI／通知。
