# 历史 CRM 离线预检契约

本工具只读本地、冻结后的源快照和当前库存快照，写一个新的本地方案文件。没有数据库连接、SQL、网络调用、清空、群通知、AI 任务或正式写入入口。`can_execute` 仅表示该行通过本轮资料预检，不代表已批准、已导入或可以绕过数据库权限/版本检查。

```bash
cd backend
PYTHONPATH=src .venv/bin/python scripts/crm_history_preflight.py \
  /outside-repository/source-snapshot.json \
  --inventory /outside-repository/current-inventory.json \
  --output /outside-repository/new-reviewed-plan.json
.venv/bin/python -m pytest tests/test_crm_history_preflight.py -q
```

真实原始记录、账号映射、源文件和库存必须放在源码仓库外。测试全部使用合成资料。输出采用独占创建，不覆盖已经审核的计划或输入文件。

## 源快照

顶层字段：

| 字段 | 含义 |
|---|---|
| `schema_version` | 固定 `1` |
| `workspace_id` | 明确目标公司，必须等于库存公司 |
| `source_system` / `base_token` | 源系统（缺省 `feishu`）与来源 Base |
| `as_of` | 本次快照业务日期，`YYYY-MM-DD` |
| `financial_year` | 已经人工确认的季度年份；不套用到历史跟进日期 |
| `accounts` | 确认的当前姓名 → 账号名，不能从创建人猜测 |
| `tables` | 必须包含 `customers`、`opportunities`、`visits`、`partners` |
| `owner_decisions` | 以商机源 record ID 为键的人工归属决定 |
| `quarantine` | 以源 record ID 为键的已知待核对原因列表 |
| `legacy_aliases` | 已审核的旧系统记录 ↔ 新源身份桥接 |
| `execution_contract` | 本批审核后的商机状态、销售渠道映射，不成为公司的运行时硬编码 |
| `format_preservation` | 逐记录、逐字段的富文本导出格式等价证明，仅用于保留现有历史正文 |

每个 table 包含 `table_id`、`complete: true`、`expected_count`、`visible_fields`、`records`、`records_sha256`。records 为 `{record_id, fields: {字段名: 原始值}}` 的列表；哈希使用模块提供的 `canonical_hash(records)`。请求分页必须全部完成再标 complete。显式期望数量必须和实取数量相同；不能把 17,852 个客户源记录通过删除空行凑成另一个数量。全空源记录也保留 source record ID 后进入待核对。

关系字段必须保留 `[{"id": "源recordID"}]`，不能把 Excel 中的显示名称当成关系主键。用户/选择字段可以是来源导出的数组。实际跟进日使用 `interaction_on`；`recorded_on` 只来自源创建时间的日期，不得混为同一个日期。源创建时间不冒充系统本次写入的 `created_at`。缺实际原作者的记录只暂存，不为通过校验而补造原作者。可见字段清单之外的业务值不进入 `raw_fields` 或字段投影；源 ID、哈希、旧编号桥接属于单独的内部追溯信息。

当前批准范围的独立检查基准为客户 17,852、商机 500、跟进 1,009（视图 994 加已批准历史 15）、伙伴 263。工具不把这些数字硬编码为所有公司的产品规则；采集者必须在冻结 manifest 中写入本次明确范围。

`owner_decisions[record_id]` 结构为：

```json
{"current_owner_account":"ALICE","is_provisional":true,"evidence":"人工确认的临时交接依据"}
```

推定的当前人不会写成源 `商机销售`。原字段为空会保留 `original_owner_name: null` 和 `source_owner_field_empty: true`；原跟进人另存文本，离职人员无需为了导入新增可登录账号。管理人优先从关联商机的当前管理人得出，仅伙伴跟进才从伙伴渠道经理得出。多商机指向不同人或部分人不明时保留待核对，不复制跟进、不转移个人完成次数。

## 当前库存与防覆盖

库存顶层必须是 `workspace_id`、`complete: true`、`objects`。objects 按四类分别列出当前对象：

```json
{
  "id": "system-uuid",
  "version": 8,
  "source_keys": ["feishu:base:table:record"],
  "values": {"name": "当前名称", "relations": {}},
  "last_imported_values": {"name": "上次实际导入的名称", "relations": {}}
}
```

`values` 和 `last_imported_values` 均使用本工具的标准化字段。后者必须有旧导入实际落库的证据，不能直接复制当前值伪造未修改。来源新值与当前相同为不变；只有当前值仍等于已知旧导入值时才提出更新。没有旧基准或后来人工改动均列 `conflict`。来源空值不删除现有业务数据。历史正文、下一步行动、历史对接人、实际原作者及实际跟进日即使仍等于旧基准，发生内容变化也须人工核对，不能悄悄改写历史。

商机额外执行现有数据库的“同客户规范业务名称唯一”检查：去除全部空白并转小写后比较，来源 ID 不同但触发同一业务键的整组记录均暂存，不擅自改名、合并或按第一条导入；与另一个已有目标冲突也阻断。它是唯一约束检查，不用作自动身份匹配。受影响跟进继续传递主体未就绪状态。

现有记录只能通过稳定 `source_key` 或显式 `legacy_alias` 匹配。同名仅为核对线索。库存可提供 `candidate_names`（如旧来源简称）；这些名称也只用于阻止重复新建、列出核对候选，不建立自动合并关系。`legacy_alias` 必须包含 `source_key`、`kind`、`target_id`、`expected_target_version`、`approved: true`、`evidence`，并且目标存在于库存，版本未变。一条源身份指向两个目标、两条来源覆盖同一目标都阻断。源键必须同时包含系统、Base、table、record，不能跨公司/表仅用一个 record ID 当唯一身份。

2026-09-18 旧导入的商机编号、跟进序号可通过旧源表唯一编号映射到原 record ID。旧客户 `CRM_V3:customer-row:N` 是旧 Excel 的行号，不能映射为新副本的第 N 行。客户仍需明确来源桥接；直接删除原 267 个客户会损失后来添加的联系人、跟进与审计，预检没有删除动作。

## 字段投影与新增信息

`persisted_projection` 将字段分为正式对象字段、证据和独立子记录。客户到商机、伙伴到商机／跟进的反向关联仅供证据，不当作客户主体的覆盖字段。商机的源 `所属伙伴` 是普通关联，由 `related_objects.associated_partner_source_ids` 指向独立关系表；它不代表转售伙伴，不能据此改签单方式或覆盖旧 `partner_id`。其他正向主体关系必须核对，一旦关系不一致仍须三方比较。

`schema.evidence_sha256` 与 `schema.verified_absent_fields` 必须来自真实 `information_schema` 快照。库存每条 `absent_fields` 只允许列出确实尚不存在、且处于工具白名单的新增来源说明、季度精度或管理关系字段，可作为 `additive` 补充。不得把旧 owner、amount、name、body、date 冒充新字段以绕过基准检查。旧具体关单日期保留；如果与源季度不一致，整个商机待核对。

商机 `stage_code`、`status`、`probability` 根据本次 `execution_contract.stage_map` 显式映射，未知／空状态进入待核对，不能默认 open。销售渠道同理由 `sales_channel_map` 映射。伙伴有明确字段才投影负责人、级别、区域、签约日等，未知原文仍保留。客户 `公司全称` 不映射为 `company_reference`，后者在系统中表示客户编号／审批号；客户状态也不擅自塞进不同语义的生命周期枚举。

客户 `source_owner_names` 保留源原姓名数组；仅严格一个原姓名且命中当前账号，才给出 `current_owner_account`。多原姓名即使只有一人在职，也不自动选人。已有客户通过实际 `ownership.is_assigned` 与目标版本核对，已归属一律保留；只有明确当前未归属，才提出本次历史来源恢复，写入历史导入事件，不制造认领审批。没有明确归属的客户仍保留档案，可搜索认领；“近期有跟进”和“已有当前负责人”分别判断，不把来源近期客户数直接承诺成地图可见数量。

可见的商机 `FDE`、跟进 `参与FDE` 全部保留原文，标准化为 `source_fde_names`、可解析账号及 `fde_participation_status`。当前标记 `pending_relationship_reconciliation` 仅表示待与已有参与关系核对；不能据此宣称已经创建正式 FDE 参与关系，离职姓名不创建可登录账号。

少量导出格式变化可附 `format_preservation[record_id].follow_up_record`：冻结目标 ID／版本、两边字段值哈希、原生富文本 segments／哈希及导出证据。程序要求原生 segments 拼出的纯文本仅经过全文首尾空白比较后与旧正文相同，且以原始链接／mention 渲染出的 Markdown 与源正文相同；内部换行、空白、文字、链接标签都不能变化。满足时输出 `preserve: format_only_preserve_existing`，仍逐字保留旧正文，不用去链接正则归一化，也不能借此更新正文。新增或真实内容变化不适用此例外。

## 结果与数据库交接

每行输出 `create`、`update`、`noop`、`quarantine` 或 `conflict`，四类对象分别汇总。隔离和冲突都保留完整本次可见原值、来源、缺项原因。已知错配保留源关联，不能自动“修正”。受影响的商机/跟进继承主体未就绪状态；客户/伙伴的反向查询字段不因关联业务待核对而被视为新主体。

- `source_hash` 是本次可见原字段哈希；`snapshot_hash` 和 `inventory_hash` 固定本轮输入。
- `normalized` 中保存当前管理账号与原作者/原负责人，不能把账号名直接当数据库 UUID。
- 关单来源只提供季度时保存已确认的 year/quarter，不造季度末日期；已有 `expected_close_date` 保留且检查与源季度是否一致。
- 预测按原字段保存季度金额/信心度。金额以十进制字符串完成万元→元，空与零分开。
- Q1/Q2 为 `period_actual_snapshot`，保留原金额、单位、来源字段和税口径。Q1 标签为 inclusive，Q2 确收 unknown；不创建逐笔 `customer_actual`。
- `can_execute` 为 false 的记录只能进暂存/待核对区，不能冒充正式商机、客户或跟进。

`related_objects.forecast_reconciliation` 按年／季度列出 `target_id`、完整 `current_snapshot`、有真实旧导入证据的 `last_imported_snapshot`、非空 `source_values` 与 `action`。只有旧原金额基准仍与当前相同，才允许规划该季度更新；来源未提供的金额或整个季度均保留，不能清空。没有已有子记录时仅在完整库存证明下规划 create。`can_execute_children` 同时要求父对象就绪且预测子记录全部无冲突；历史季度实绩保留为独立 `period_actual_snapshot`，不转为逐笔实绩。日期和 UUID 在冻结快照中为字符串，金额比较采用十进制。

后续写入器应把方案放入数据库导入账本，稳定绑定保存在独立 binding 中；应用时重新读取公司、权限、绑定和 `expected_target_version`，在同一事务内锁定并重检，再更新正式记录和账本。不能只信数小时前的本地 `can_execute`。`ops.crm_import_record` 可承接原值、标准化值、异常、目标及版本；季度历史金额的 `source_record_id` 引用该账本 UUID，而不是直接放飞书字符串 ID。当前工具不实现这一步，也不证明 V4 已到达。
