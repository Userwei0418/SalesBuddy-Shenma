# 看板与经营指标的数据口径

更新：2026-09-11。页面名为“看板”；路由仍为pages/bi。/dashboard读取完整权限事实，页面按选择的季度分组；不以列表第一页代替聚合。服务端入口为repositories/dashboard.py，展示计算在frontend/miniprogram/pages/bi/index.js。

| 项目 | 当前来源与口径 |
|---|---|
| 季度与倒计时 | 自然季度；默认系统当前季度，可切换已有年度/季度 |
| 活跃商机ACV及阶段 | crm.opportunity中未删除的open商机，按预计关单季度和标准阶段分组；不是已确认收入 |
| 产品线占比 | 明确登记的商机product_line等授权返回字段；未填写不能靠客户名推断 |
| 销售商机排名 | 商机owner_user_ref_id归属，权限范围内汇总，不因共享客户重复算入多个销售 |
| 季度预测确收/回款 | crm.opportunity_forecast的year/quarter记录，关单日期修改不迁移预测季度 |
| 实际确收/回款 | crm.customer_actual，按occurred_on季度；排除已作废和未来数据，不用ACV比例推算 |
| 近7天跟进 | 有效、已确认/归档拜访，按实际跟进日期/记录人聚合 |
| 我的经营目标 | crm.sales_target，年度＋个人/团队/部门＋指标独立保存，不自动跨年 |
| 我的效率/成熟度辅助 | /profile/evaluation与/profile/performance读取完整授权事实、实绩、目标及比例 |
| 六维销售画像 | /profile/sales-growth读取已持久化模型复盘，与收入/利润指标分别解释 |

未登记值为未知，明确登记0为0，请求失败为错误与重试，三者不能互相代替。年度目标表不等于独立季度签约目标；没有单独财务事实的毛利、利润或尚未确认的综合成熟度权重不编造。

当前已有实绩和年度目标表，不能再按旧设计建议重复新建sales.target或finance.collection来实现同一事实。新增业务口径先明确归属和周期，再扩展统一表/服务。[数据源清单](../../docs/DATA_SOURCE_MATRIX.md)和[当前验收](../../docs/MAINTENANCE_VALIDATION_20260910.md)提供更完整边界。
