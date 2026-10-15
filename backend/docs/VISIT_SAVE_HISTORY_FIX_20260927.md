# 拜访保存与首页历史记录兼容修复

日期：2026-09-27。用户确认先修复，随后与本轮改动一起合并。本记录不代表已合并或已上线。

## 起始版本与范围

- 唯一源码：`/Users/zhouwei5/Developer/Raccoon SalesBuddy`，origin 为 `Userwei0418/Raccoon-SalesBuddy`。
- 起始分支：`codex/local-browser-acceptance`，HEAD `3a3af8f042cfe5a3f5a682a9eb086fbae5c481f3`，工作区干净。
- 故障排查时线上：`5d55f49eca809d3cc715f636c753d38dc039c60b` / `market-role-20260924-5d55f49`；本地 SaaS 候选与线上版本分开管理。
- 本次修改仅在 `backend/`：两处仓储、三份真实 PostgreSQL/HTTP 回归和本说明。接口、前端、数据库迁移及数据库约束保持原契约；没有修改生产数据、运行配置或 Agent。
- 本次不操作微信开发者工具、不上传小程序；原体验版保持。实际 IDE 当前工程未重新核查，不把后端测试视为真机验收。

## 商机具体日期取代历史季度

历史商机可能只有 `expected_close_year/quarter=2026/Q2`、没有具体日期。原 UPDATE 在保存 `2026-09-30` 时保留 Q2，触发 `opportunity_close_date_consistent`；拜访质检已通过，但归档事务因此失败。

统一商机仓储现在在**具体日期非空且发生变化**时，同时清空仅季度精度的两个补充字段。当前关单时间以人工确认的具体日期为准，列表筛选、地图、排序继续按该日期归季。日期未变或仍为空时保留原季度；不据季度猜测具体日期，也不给具体日期新增 2000–2100 年范围限制。

`import_meta.source_fields` 和导入台账原始证据不修改。名称、金额等修改不会清掉未变日期的历史季度。小程序独立商机编辑、拜访联动更新、后台商机编辑共用这处修复；创建和历史导入流程不改。

## 首页客户归档回执与伙伴历史分开投影

历史拜访可以只关联伙伴，但首页客户归档回执必须包含客户 ID、名称，其旧客户端入口固定跳转客户档案。原查询将仅关联伙伴的记录带入该响应，导致整个首页响应校验失败。

首页查询在排序和 `LIMIT 20` 之前限定为关联客户的记录，仍取当前录入人的最近 20 条客户回执。仅关联伙伴的历史记录保留在原表，原拜访详情 ID/接口仍可读取；不伪造客户、不删除记录，也不扩大人员、FDE 或租户范围。

## 验证

回归先复现日期约束冲突及首页空客户字段的响应校验异常，再验证修复。

- `test_opportunity_close_date_precision.py`：跨季度、跨年、同季度日期变化、日期未变、无季度、1999/2000/2100/2101 日期边界；原证据保留、旧版本拒绝、其他人员拒绝、季度筛选及未填日期的历史季度保留。
- `test_visit_close_date_archive.py`：通过质检后真实 HTTP 归档；同幂等键及新键复用同一质检均只生成一条拜访/确认、一次商机更新；版本冲突完整回滚，篡改质检快照拒绝，重新质检后正常归档。
- `test_assistant_archived_receipts.py`：25 条客户与 25 条伙伴历史混排，首页取满最近 20 条客户回执，伙伴详情继续返回；跨人及跨租户隔离。
- 所有数据均为一次性数据库中的合成数据，断言使用非超级用户、非 BYPASSRLS 身份；模型输出是明确的测试输入，没有调用真实模型或飞书。

本次新增 16 项 PostgreSQL/HTTP 回归全部通过（商机日期 11、归档 2、首页 3）；修改后的后端单元测试 2,615 通过。Ruff F、差异格式及 OpenAPI 一致性检查通过。完整数据库套件及本机启动验证的最终回执随交付包保存；不将定向结果当成整个发布门禁通过。

执行入口：

```sh
python backend/tests/system/verify_cross_module_robustness_postgres.py \
  test_opportunity_close_date_precision test_visit_close_date_archive test_assistant_archived_receipts
python -m pytest backend/tests --ignore=backend/tests/integration -q
python backend/tests/system/run_integration_postgres.py
PYTHONPATH=backend/src python backend/scripts/export_openapi.py --check
ruff check backend/src backend/tests --select F
```

本轮最终检查结果与本机运行回执记录在交付包 `incident-20260927-visit-save/`；合并门禁和生产发布审批分别执行，不用本地通过替代线上验收。
