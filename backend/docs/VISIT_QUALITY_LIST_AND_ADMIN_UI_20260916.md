# 跟进质检列表与后台测试入口修复

## 跟进列表

`GET /api/v1/visits/{id}` 已返回归档质检结果，但客户和商机共用的分页接口没有投影评分和等级。前端若继续读取旧 `expectation_code`，会将已有质检结果的记录显示为“待评估”。本次只补后端读取契约；小程序展示由独立前端修复对接。

`GET /api/v1/visits` 的每条记录增加：

```json
{
  "follow_up_score": 75,
  "quality_review": {
    "follow_up_score": 75,
    "grade": "合格",
    "next_action_passed": true
  }
}
```

- 顶层分数读取正式评分列，摘要读取已保存的质检 JSON。两份分数不互相覆盖；不一致时保留证据供前端提示核对。
- 等级沿用归档值，不按当前策略重算。评分表示质检结果，不表示销售目标达成。
- 未评分的历史记录继续返回 `null`；空 JSON 归一为 `null`。摘要中的缺失或类型不正确的字段返回 `null`，不把字符串 `"false"` 转成布尔值。
- 摘要只包含以上三个键，不携带完整提示词、公司规则快照或冗长评估正文。
- 保留原有 RLS、筛选、排序、游标和 offset 行为；在已截取的一页中一次查询，无逐行详情请求。
- 客户及商机复合详情的历史列表已经从 `customer_records.related_records` 返回正式评分列和质检结果，保持现有契约。
- 不修改业务数据、数据库结构或小程序文件。

集成回归覆盖客户与商机筛选、默认及录入时间排序、每一页的游标/offset、历史等级、零分、空值、两个分数不一致、无效 JSON 字段和越权读取。原有大规模列表测试继续检查分页上限及摘要体积。

## 后台布局

智能体配置卡片的“测试连通性”移至能力标题右侧，与“前往中台配置”并列，使用 32px 高的紧凑按钮；“查看运行审计”移至分隔线下方右侧。窄屏允许自然换行。仅调整管理网页布局，管理员操作权限、测试请求和结果弹窗保持原逻辑。

本地浏览器已核验常规窗口及 390px 窄屏；窄屏页面宽度等于视窗宽度，无横向溢出。模型接口配置页原有底部操作组保持一致布局。

## 验证命令

```bash
PYTHONPATH=backend/src python -m pytest backend/tests/test_admin_ai_labels.py backend/tests/test_admin_static_assets.py backend/tests/test_connectivity.py backend/tests/test_detail_read_contract.py backend/tests/test_detail_paging.py -q
PYTHONPATH=backend/src python backend/tests/system/run_integration_postgres.py
PYTHONPATH=backend/src python backend/scripts/export_openapi.py --check
ruff check backend/src --select F
```

真实 PostgreSQL 验证只在一次性测试数据库运行。生产部署后只读对比指定历史记录的列表与详情字段，不触发重新评分。具体线上回执随交付保存。
