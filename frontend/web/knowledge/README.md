# 知识库目录（方案与定价）

企业工作区的「知识库 · 方案与定价」读取本目录下的 `catalog.json`。它随 Web 静态文件发布，不经过业务后端，也不新增接口；演示工作区（`?mode=preview`）使用内置的合成示例，不读这个文件。

## 怎么维护

1. 按下面的格式在 `items` 里增删条目，保存为 UTF-8。
2. 同一方案出新版本时，新条目 `status` 写 `current`，旧条目改为 `archived`（页面默认只显示当前版本，可切到「含历史版本」）。
3. 附件放在 `knowledge/files/` 下，`files[].url` 写相对路径（例如 `knowledge/files/智能质检方案_v2.1.pptx`）。
4. 重新执行 `npm run build`（或直接替换已部署目录里的 `knowledge/`），刷新页面即可。

价格、折扣规则属于对内信息，只放已审批、可在销售团队内使用的版本；对客材料以审批后的报价为准。

## 字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `version` / `updated_at` / `owner` | 否 | 目录整体的版本、更新日期、维护人，显示在页面上 |
| `categories[]` | 否 | 分类，默认解决方案、产品定价、案例与资质、模板与话术 |
| `items[].id` | 是 | 唯一编号 |
| `items[].category` | 是 | `solution` / `pricing` / `case` / `template` |
| `items[].title` / `version` | 是 | 标题与版本号；同标题的不同版本会在详情里列为「其它版本」 |
| `items[].status` | 否 | `current`（默认）或 `archived` |
| `items[].updated_at` / `owner` | 否 | 条目更新日期与维护人 |
| `items[].product_line` | 否 | 产品线；与商机的 `product_line` 一致时，Playbook 与方案生成会自动推荐 |
| `items[].tags[]` / `summary` | 否 | 标签与摘要，参与搜索 |
| `items[].highlights[]` / `scenarios[]` / `steps[]` / `deliverables[]` | 否 | 方案要点、适用场景、实施路径、交付物；「方案生成」按这些字段出草稿 |
| `items[].price_items[]` | 否 | 价格行：`name`、`unit`、`price`、`note` |
| `items[].effective_from` / `effective_to` / `approval` | 否 | 价格有效期与折扣审批规则 |
| `items[].files[]` | 否 | 附件：`name`、`url` |

## 示例条目

```json
{
  "id": "sol-example-1",
  "category": "solution",
  "title": "示例解决方案",
  "version": "v1.0",
  "status": "current",
  "updated_at": "2026-10-06",
  "owner": "产品方案组",
  "product_line": "示例产品线",
  "tags": ["示例"],
  "summary": "一句话说明方案解决什么问题。",
  "highlights": ["要点一", "要点二"],
  "scenarios": ["场景一"],
  "steps": ["范围确认", "配置与对接", "试点", "复盘与推广"],
  "deliverables": ["试点验收清单"],
  "files": [{"name": "示例方案.pptx", "url": "knowledge/files/示例方案.pptx"}]
}
```

## 后续接口建议（不在本次改造范围）

在线维护、权限分级与附件上传需要后端提供知识库接口，例如 `GET /api/v1/knowledge/items`（按分类、产品线、关键词查询）与对应的写接口。接口上线后只需把 `department-ui/pc/knowledge-data.js` 里的 `loadCatalog` 改为调用该接口，页面不用改。
