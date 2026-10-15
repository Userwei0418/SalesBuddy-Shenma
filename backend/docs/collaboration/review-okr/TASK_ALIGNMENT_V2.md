# DELTA04 D03：任务关联 V2

协议初始基准：core `ab24218a67966adcdffd5e3a1af1d923c28edfe1`。当前独立候选接在
D02 `b07158a2fc90f51d77c9940055ee589ddf848a67` 之后。数据库增量为
`V244__task_alignment_object_basis.sql`，前置 V242/V243；历史 V240–V243 文件不改。
本文件描述源码契约，不代表生产已迁移或新 Web 已验收。

## 输入

在单任务、批量每项任务、建议采纳的 `task/tasks` 和关联 PATCH 顶层显式传
`alignment_contract_version: 2`。PATCH 仍须携带当前任务 `version_no`。

```json
{
  "alignment_contract_version": 2,
  "alignment": {
    "target_ids": ["10000000-0000-4000-8000-000000000001"],
    "key_result_ids": ["20000000-0000-4000-8000-000000000002"],
    "other_reason": null,
    "expected_contribution": null
  }
}
```

或明确选择其他：

```json
{
  "alignment_contract_version": 2,
  "alignment": {
    "target_ids": [],
    "key_result_ids": [],
    "other_reason": "客户临时审计要求，需整理已交付资料",
    "expected_contribution": null
  }
}
```

两个对象数组各最多 20 项，UUID 规范化后排序去重。对象至少一项与有效原因严格互斥；
其他模式不接受期望指标贡献。V2 不接受 `metric_codes`、客户端周期、归属或单位。
未知协议版本、没有版本标记却带 V2 字段、V2 缺省关联均拒绝。

目标指 `crm.sales_target.id`，周期、归属来自该生效行；待审批的新目标没有有效 ID，
申请 ID 和 `row_key` 不能代替目标。已有目标正在申请变更时仍关联原有效 ID。
KR 使用其真实 ID，周期和归属来自所属 O。多个已授权对象可属于不同周期；不根据任务
截止时间猜目标。KR 自身引用的目标不会被自动写为用户直接选择的 `target_ids`。

服务端在同一任务事务中读取对象并校验同租户、真实对象权限、KR/O 有效状态和指标启用。
目标指标使用其 `kind`，KR 使用其 `metric_code`，合并去重形成只读指标集合。
期望与实际贡献继续使用精确字符串数值，仅为 `planned/reported` 声明，不能增加财务或 KR 进展。

## 原因规范化

前端在冻结提交/重试意图前执行 ECMAScript `String.trim`；后端和 SQL 使用同一明确字符集合：
U+0009–000D、0020、00A0、1680、2000–200A、2028、2029、202F、205F、3000、FEFF。
只移除首尾字符，保留正文中间空白。trim 后长度为 1–2000 个 Unicode 码点；前端应按
`[...reason].length` 计数。仅由空白组成的原因无效，不使用模型判定必要性。

规范化发生在幂等指纹之前。首尾空白等价的 V2 请求可重放，同 key 的不同原因仍冲突。
保存值和详情、普通列表、tab、management 读回值一致。

## 读回和权限

原 `alignment` 字段继续返回旧指标、贡献、版本字段，并新增：

- `contract_version: 2`、`basis: objects|other` 和规范化 `other_reason`。
- 当前可读的 `target_ids`、`targets`；对象摘要含 ID、指标、版本、真实周期和归属。
- 当前可读的 `key_result_ids`、`key_results`；摘要另含 `objective_id`。
- `restricted_target_count` 和原 `restricted_key_result_count`，只暴露数量，不暴露隐藏对象 ID、周期或归属。

V2 对象的 `scope` 增加可空 `user_name`、`team_name`；KR 列表使用相同字段。
名称来自当前已授权目标或 Objective 的同租户真实归属，不取任务负责人或 KR 负责人。
名称不可读时返回 `null`，不影响原已授权对象、顺序或受限数量；归档 KR 的列表行为保持原样。
此处仅补充当前展示元数据，V1 原始 JSON、存储关系和业务版本均保持不变。

任务可读不等于目标/KR 可读。存在当前不可见的既有目标/KR 关系时，禁止整体替换，避免
意外删除隐藏关系。全局任务计数仍按任务表记录计数，不 JOIN 多对象增加行数。

## 旧客户端和能力

`GET /api/v1/tasks/alignment-capabilities` 是已认证的静态路由。代码和实际 V244 登记
均就绪时返回 `supported_versions: [1,2]`、`preferred_version: 2`；数据库尚未迁移时只宣告 1，
读取失败返回 503。新 Web 必须先确认 2，不能自动降级为无关联或旧指标关联。

未标记的旧创建、批量、建议采纳、会议确认保持 V1 行为。新增版本字段在缺省时不进入旧
模型 dump；兼容指纹只剔除新增且为 null 的字段，其余原字段保留。存量无关联任务仍为 null；
存量 V1 不补造目标或原因，读回标为 `contract_version: 1` / `basis: legacy`。

V1 关联替换不能覆盖已存 V2，返回 409 `alignment_contract_upgrade_required`。
旧 accept/complete/review/cancel 等生命周期不强迫补原因，保留 V2 对象与原因。
新 Web 编辑旧 V1 任务时须显式选对象或填原因后升级。

协议标记用于选择契约，不是权限凭证；兼容无标记客户端意味着它不能证明请求来自哪个
客户端版本。具体任务和对象权限始终由当前数据库授权判断。关联 PATCH 在幂等回执前
复核任务读取和具体协调资格；已提交回执不因旧版本或完成状态被重新执行业务校验。

## 数据库及交付

V244 新增 `workflow.task_target_alignment`、nullable 协议版本和原因列；旧行不回填。
新增目标复合唯一键与同租户外键，启用并强制 RLS。使用原三个受控函数入口，不授予新表
直接 SELECT/DML 或新函数 EXECUTE。API 原 `task_alignment` 表 SELECT 自然包含两个新列；
角色表/函数权限名单不扩展。原历史表仍对 runtime 不可读。

原 W 的报告 V244/V245 草案及历史证据原样保留，不属于此 L 增量。
总控已将后续报告冻结/Worker权限分别安排为 V245/V246。
发布仍需父冻结源码、Agent 固定新 ledger/RLS policy、精确发布门禁及实际环境验证；不删
新关系或原因来回滚。旧应用不能覆盖 V2 关联，但旧生命周期仍可继续。
