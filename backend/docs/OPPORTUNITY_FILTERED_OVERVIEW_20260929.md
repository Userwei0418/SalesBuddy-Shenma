# 商机分页与总览共用筛选（2026-09-29）

## 最小修复生产回执

已发布运行提交 `12e85fd0e5e0da3a7bdfef7c19a3c9d4b540788e`，API、普通 Worker、飞书 Worker 三个实际进程均使用此 release，`RELEASES.json` consistent=true。数据库原 V195 + V197 账本保持不变，没有运行 V196 或其他迁移；原 paced v2 和 5 秒历史消费间隔已保留。

候选基线下 75 项契约/日期/OpenAPI 检查与 48 项独立真实 PostgreSQL 测试通过，零跳过。生产正常演示销售账号的全年两页、Q3、跨年、空搜索五组响应通过 Web 实际校验函数；总览与列表总量一致，翻页不改变统计；旧列表无强制新增字段，旧总览仍 HTTP 200。业务数据与通知未做验收写入。

配套 Web 运行提交 `7149d66`，修复年份索引及季度切换映射，124 项测试通过；7 项关键静态文件公网 HTTP 200 且逐字核对一致。浏览器工具被现有扩展界面阻挡，未将接口与构建结果表述为浏览器视觉验收通过。后续仅文档提交不改变该运行版本。

## 生产兼容修复候选

Web 已使用下述可选统计契约，而生产后端仍为 `89e11b9a5a64df12ff867416c3d8dbf1810f30e6`。线上正常身份请求返回 HTTP 200，但没有 `overview`，前端按契约拒绝将缺失统计当成零，因此显示“商机筛选统计暂不可用”。

本次在生产提交上建立 `codex/opportunity-api-release`，只移植 `d1d8c85` 的两个运行文件、对应测试与接口说明，重新导出 OpenAPI。数据库、依赖、授权和旧总览接口不变。实际生产迁移账本为 V195 加 V197（没有 V196），不能用健康接口的 `expected_schema=V195` 代替实际账本。本修复不执行迁移，也不改变历史同步限速。

候选代码的路由/仓储/业务日期测试 73 项及 OpenAPI 一致性测试 2 项通过；隔离数据库与生产发布结果另行记录。以下为原实现说明与原始验证，不能代替本候选的线上验收。

## 目标与边界

本地基线 `9834008`，分支 `codex/phone-first-login`。本次后端修改仅限 `backend/`；前端由配套任务实现。未修改数据库迁移、授权规则、业务事实或旧 `/opportunities/overview` 契约，未部署线上。

目标：同一页面的业务筛选应确定同一组可见商机，列表和总览使用该集合；翻页不得改变总览。预计关单日与实际业务发生日期分别表达，不把预计关单日当成实际成单日。

## 接口增量

`GET /api/v1/opportunities`

| 参数 | 默认值/限制 | 含义 |
|---|---|---|
| `include_overview` | `false` | 为 `true` 时额外返回 `overview`。 |
| `overview_year` | 2000–2100；启用总览时必填 | 总览业务统计年份。 |
| `overview_quarters` | 0–4 个整数，值 1–4 | 空数组为跨年；四季度全选为所选年全年；重复值去重。 |

原有 `year/quarters/close_period` 继续控制预计关单时间筛选。团队、成员权限范围、负责人、阶段、等级、关键词、产品线、客户、预计关单时间及 `include_closed` 共同确定结果集合；`overview` 使用同一 SQL `filtered` CTE，无独立授权拼接、不从分页 items 计数。启用时，主聚合查询一次返回 summary、分页 ID、facets 和 overview；统计保持同一语句快照。

未启用时无新增响应字段，也不查询正式跟进或演示场景聚合。原调用方的目录、排序、分页与 summary 语义保持不变。

```json
{
  "items": [],
  "summary": {"total": 25},
  "overview": {
    "metrics": {
      "total": 25, "won": 3, "active": 12, "newCount": 5,
      "missingCloseDates": 0, "missingWonDates": 1,
      "missingCreatedDates": 2, "demo_scene_count": 0
    },
    "year": 2026,
    "quarters": [3],
    "definition_version": "opportunity_filtered_overview_v1"
  }
}
```

此片段为字段示例，并非真实业务数据或完整响应。

## 指标语义

| 字段 | 统计规则 |
|---|---|
| `total` | 完整筛选结果中的当前商机数量，严格等于同响应 `summary.total`；不受 offset/page_size 影响。 |
| `won` | 结果集合内状态为 won、实际成单 `closed_at` 在业务统计期间的数量；跨年全部时间包括缺实际成单日的 won 记录。 |
| `active` | 结果集合内有正式 confirmed/archived 跟进且非 lost 的商机；按 `interaction_at` 期间、商机去重。 |
| `newCount` | 按真实业务建单日期统计；历史导入沿用源建单日解析，缺失/非法值不回退为导入时间。 |
| `missingWonDates` | 选择具体期间后，集合内 won 但缺少实际成单日的记录数。 |
| `missingCreatedDates` | 集合内缺少有效真实业务建单日期的数量。 |
| `missingCloseDates` | 选择具体期间后，集合内非 lost、既无预计关单日也无预计年份的记录数；保留既有总览定义。 |
| `demo_scene_count` | 结果集合内，按场景创建日期统计的未删除演示场景数。 |

所有时间转换为上海业务日；赢单、活跃、新增可能重叠，不能相加。内部源日期投影只用于统计，不返回原始导入字段或额外权限外信息。

## 验证

- 路由与仓储契约、业务日期解析及 OpenAPI：75 项通过。
- 新增真实 PostgreSQL 用例：`tests/integration/test_opportunity_filtered_overview.py`，包括跨分页 25 条全量、组合筛选、实际/预计两类日期、上海边界、历史缺日期、同商机跟进去重、sales/FDE/member/tenant 授权及 HTTP 正常身份调用。
- 独立一次性 PostgreSQL 16（完整迁移至 V196，164 项 migration checksum）运行三份集成文件与路由契约：41 项通过、0 跳过、0 失败；执行身份为无 superuser/bypassrls 的受限角色。旧本地 V193 校验冲突库未修改。
- OpenAPI 已从应用导出并通过一致性检查；新增集成文件及所有增量代码行 Ruff 通过，`git diff --check` 通过。基础文件中已有 import/长行 Ruff 问题，本次不做无关格式重排。
