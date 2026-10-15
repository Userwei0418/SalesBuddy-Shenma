# DELTA04 D01 / D04-A 后端增量合同 v1

状态：本次隔离源码及受限角色验证已完成，待父任务审查/提交/发布。本合同不包含 D02 多选或 M3 报告。

## D01 顶部指标

`GET /api/v1/review/overview` 的 `metrics` 保持原三项 `forecast_collection`、`collection_ppl`、`forecast_recognized`，兼容既有 Web matchingReview、折叠更多及会议补充页。UI 顶部只展示前两项：`forecast_collection` 的 `target.kind=collection`，`collection_ppl` 的 `target.kind=collection_ppl`，各用各自生效目标。不得为了顶部两卡删掉 API 第三项。`metric_code` 不重命名，不新增 `card_code=collection`；实际回款字典 `collection` 不变。

`services.review_overview.read_metric_projection` 继续返回原三项，因此自动 KR 的预测确收能力保持；`GET /api/v1/review/opportunities?metric_code=forecast_recognized&filter=all` 保留第三指标下钻。预测、缺数、目标权限、无基线与 Decimal 字符串口径均保持。

## D04 七类活动与变更列表

七类 `activity` code 保持原值。`updated_opportunities` 的用户界面名称为“商机变动”。请求仍为：

```http
GET /api/v1/review/activities?activity=updated_opportunities&scope=self&anchor_date=2026-10-14&period_type=day&limit=50&offset=0
```

本批仍只有原单 `team_id` / `member_id` 选择。此页分页对象是去重后的商机，`known_total` 也是商机数量，非事件/字段数量。

后台按以下顺序处理：读取真实 `business_change / opportunity_state / opportunity_forecast` → 规范化字段差异 → 删除 same-value 和维护理由/系统版本时间字段 → 删除空事件和空商机 → 按实际剩余事件时间排序 → 统计与分页。A→B→A 保留两次变化。无权对象经过原来源授权与 RLS 过滤。

`business_change` 与 state 仅在同一商机、同一版本、字段/单位/季度及 before/after 均可证明相同的时候合并该字段来源，不因存在 business_change 就遮住其他 state 字段。forecast 缺少可靠的跨源关联标识，保持独立真实来源，不靠时间接近或值相同合并。`event_count` 是规范化后展示的来源事件数，不是人工保存次数。

## 字段契约

每个商机 `items[]` 保留原 `id/opportunity_id/name/customer_id/customer_name/version_no/events/source_refs` 等定位字段。`occurred_at` 和排序使用过滤后最新真实变化时间。

每个 `events[]`：

| 字段 | 含义 |
| --- | --- |
| `kind` | 原兼容字段，`business_change / opportunity_state / opportunity_forecast` |
| `id` | 原兼容来源标识，保留已有前缀 |
| `event_id` | 标准事件标识，目前与 `id` 相同 |
| `occurred_at` | 来源记录的时间，ISO8601 带时区 |
| `version_no` | 来源版本；forecast 没有对应商机版本则 null |
| `actor_id` | 当前有权查看的人员 UUID，否则 null |
| `actor_display_name` | 同租户且 `authorization_subject(work_report.read,person,actor)` 允许的当前姓名，否则 null |
| `actor_status` | `available / restricted / not_recorded` |
| `actor_name_basis` | 可用时 `current_authorized_directory`，否则 null；不是历史姓名快照 |
| `changes` | 标准字段差异数组，见下表 |
| `source_refs` | 原始来源引用；准确合并字段时保留全部已验证来源 |

state / forecast 表没有实际操作者，因此这两类返回 `actor_status=not_recorded`、人员 ID/姓名为空；不能拿创建人、商机负责人或 `source_ref` 代替。Web 可显示“未记录”或“不可见”，不可另查未授权人员补齐。

每个 `changes[]`：

| 字段 | 含义 |
| --- | --- |
| `field` | 稳定字段键；旧事件只能确认 label 时为 null |
| `label` | 实际留存标签或确定性字段标签，Web 可直接展示 |
| `before / after` | 原值，支持字符串、null、数组、对象；不加 AI 解释 |
| `before_known / after_known` | 本批只展示已有两端证据的差异，因此均为 true；显式 JSON null 有证据，缺失键无证据；forecast 可证明的整条记录不存在也属于已知状态 |
| `unit` | 有证据的金额单位元/万元，否则 null；旧万元保持原数值，不与元直接相等去重 |
| `year / quarter` | 有证据的季度预测定位，普通字段为 null |

未填写不转成0；任一端缺少证据的字段均不进入 `changes`，只通过历史 coverage 提示；仅含这类字段的事件及商机在计数分页前排除，其他双端可证字段继续保留。数字仅对已知金额/概率字段作精确 Decimal 等值判断。无法解析旧字段键时保留实际 label 与 before/after；不由模型猜字段。

新写入最小修复：`domain.opportunities.differences` 只有渠道本身变化才写 `field=sales_channel`；伙伴 ID 变化单独写 `field=partner_id` 的真实 ID 前后值。既有 label/before/after 兼容字段保留；不改旧事件，不回填虚构历史。

## 历史覆盖

`overview.activities[code=updated_opportunities].history_coverage` 与活动页顶层 `history_coverage` 返回：

```json
{
  "status": "partial",
  "complete_from": null,
  "reason_codes": [
    "history_field_coverage_unverified",
    "legacy_state_subset",
    "forecast_confidence_not_captured",
    "event_actor_not_recorded"
  ],
  "message": "仅显示已留存且可核验的商机变化；现有历史不能证明全部业务字段和操作人均完整记录"
}
```

`status` 与本次有权可展示记录对应：有记录是 `partial`，无记录是 `unavailable`。`complete_from=null`，因为 V235 安装时间不能证明全部业务字段历史完整。现 state 仅覆盖概率/状态/阶段以及金额/币种/归属图片；forecast 只覆盖两个金额，没有回款信心或操作者。业务变化数组可展示其实际留存的渠道、产品线、人员、物料等业务字段，不承诺全部写入通道过去均有留存。

因此本批商机变动卡 `count=null`，活动页 `total=null`；`known_count/known_total` 是经过过滤及授权的商机数。没有可见事件时 `known_total=0`，仍不可写成“当天没有变化”。其余活动不因本改动改变统计口径。

`not_updated` 筛选保持 `available=false`、`total=null`；对应 filter availability 增带 `history_coverage`。`updated_in_selected_day=true` 仅由可核验真实变化事件证明，没有事件则 null，不依靠 `updated_at/forecast_updated_at` 推断 false。`all/stale/high_risk/key_collection` 原能力保持，七天 stale 与重点回款规则未改。

## 隔离真实响应样例

以下 UUID/姓名/金额来自本次合成隔离数据库。原 `kind/id` 与新 `event_id/changes` 共存：

```json
{
  "id": "2b55d759-9cff-4f3d-b694-8514456a9adf",
  "opportunity_id": "2b55d759-9cff-4f3d-b694-8514456a9adf",
  "name": "roundtrip",
  "customer_id": "a297e8ea-e917-4cae-ae53-105a3c6b8c02",
  "customer_name": "own_customer",
  "owner_id": "ef2c646b-42a5-4d7b-9ba4-34df68b9301a",
  "version_no": 3,
  "occurred_at": "2026-10-14T22:09:01.947015+08:00",
  "event_count": 2,
  "events": [
    {
      "id": "business_change:64894829-5eaa-445e-96be-a25e467f931c",
      "kind": "business_change",
      "changes": [
        {
          "field": "sales_channel",
          "label": "销售渠道",
          "before": "direct",
          "after": "partner",
          "before_known": true,
          "after_known": true,
          "unit": null,
          "year": null,
          "quarter": null
        }
      ],
      "actor_id": "ef2c646b-42a5-4d7b-9ba4-34df68b9301a",
      "version_no": 2,
      "occurred_at": "2026-10-14T22:09:01.945102+08:00",
      "actor_status": "available",
      "actor_name_basis": "current_authorized_directory",
      "actor_display_name": "own_owner",
      "event_id": "business_change:64894829-5eaa-445e-96be-a25e467f931c",
      "source_refs": [
        {
          "kind": "business_change",
          "id": "business_change:64894829-5eaa-445e-96be-a25e467f931c"
        }
      ]
    },
    {
      "id": "business_change:29f796b4-90f9-4166-be24-2b99a5e81c34",
      "kind": "business_change",
      "changes": [
        {
          "field": "sales_channel",
          "label": "销售渠道",
          "before": "partner",
          "after": "direct",
          "before_known": true,
          "after_known": true,
          "unit": null,
          "year": null,
          "quarter": null
        }
      ],
      "actor_id": "ef2c646b-42a5-4d7b-9ba4-34df68b9301a",
      "version_no": 3,
      "occurred_at": "2026-10-14T22:09:01.947015+08:00",
      "actor_status": "available",
      "actor_name_basis": "current_authorized_directory",
      "actor_display_name": "own_owner",
      "event_id": "business_change:29f796b4-90f9-4166-be24-2b99a5e81c34",
      "source_refs": [
        {
          "kind": "business_change",
          "id": "business_change:29f796b4-90f9-4166-be24-2b99a5e81c34"
        }
      ]
    }
  ],
  "source_refs": [
    {
      "kind": "business_change",
      "id": "business_change:64894829-5eaa-445e-96be-a25e467f931c"
    },
    {
      "kind": "business_change",
      "id": "business_change:29f796b4-90f9-4166-be24-2b99a5e81c34"
    }
  ]
}
```

## 验证和交付边界

- 契约纠正：初版只返回两卡的 backend 改动已按父/QA要求撤回；API三项保持，只有UI顶部两卡。纠正前PG回执单独保留，不作为最终字节证据。
- 差异单测：`test_review_changes.py` + `test_review_overview.py`，32 passed。
- `verify_review_delta04_postgres.py`：隔离 V240，实际 NOLOGIN/NOSUPERUSER/NOBYPASSRLS API role、RLS、只读 repeatable-read，5 组 / 11 HTTP passed。
- 验证包含独立目标分母、第三指标投影/下钻、假差异过滤后计数分页、A→B→A、额外 state 字段不被遮住、同版本字段来源合并、forecast独立来源、操作人授权/缺失、跨租户记录不可见、同actor撤来源权限不可读。
- UUID 自有数据库与角色已 finally 清理，未停止 cluster。前两次 fixture 前置失败分别为渠道伙伴约束和迁移后 RLS session 设置，已修 fixture 后取得完整通过回执，未放宽产品权限。
- 不含生产、浏览器、真实模型或报告链路验收。无新迁移、无 ACL 改动、无 D02；未改 main/route/OpenAPI/共享报告。

实际回执：`DELTA04_D01_D04_POSTGRES_RESULT.json`（交付 backend 目录）。OpenAPI 导出和应用整合由父任务统一完成。
