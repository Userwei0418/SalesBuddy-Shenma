# 历史空值与关联字段一致性复查

2026-09-27，接续 `39ed81f` 的拜访保存及首页历史记录修复。用户要求检查类似问题并修复，随后一起合并。本记录是本地候选交付，不代表已合并、上线或完成真机验收。

## 版本与修改范围

- 唯一仓库：`/Users/zhouwei5/Developer/Raccoon SalesBuddy`；origin `Userwei0418/Raccoon-SalesBuddy`。
- 起点：`codex/local-browser-acceptance` / `39ed81f4e58cefd4825ed2c69cfcda706367dcdf`，接续时工作区干净。
- 本轮读取公网版本仍为 `5d55f49eca809d3cc715f636c753d38dc039c60b` / `market-role-20260924-5d55f49`，版本接口报告源码迁移头 V152；这不是重新查验数据库迁移账本。本轮不修改线上。
- 修改仅在 `backend/` 的读取投影、商机委派输入校验及相应测试、说明。保持现有接口字段、数据库结构、任务状态流和权限范围。
- 不修改前端、数据库迁移、Agent、真实业务数据或集成配置；未打开微信开发者工具核对工程或上传新版本。

## 已复现并修复

| 场景 | 原问题 | 修复后的行为 |
| --- | --- | --- |
| 客户历史跟进缺少下一步行动 | 数据库允许 NULL，但首页字符串契约拒绝，整个首页失败 | 首页投影为空字符串；详情和数据库仍为 NULL，未补造行动、评分或质检 |
| 历史拜访缺客户名称快照，后续客户引用权限撤销 | 两个名称来源均为空，首页响应校验失败 | 有快照仍优先，否则读取有权引用的名称，再回退空串；保留本人历史，不绕过权限读客户名 |
| 客户资料等正式列已清空，旧扩展/导入元数据仍有值 | 返回 attributes 时旧值重新出现 | 正式列已选取就以它为准；空值移除返回的兼容键；未选取列继续原兼容逻辑；0 与 false 仍是明确值 |
| 任务已人工转交，旧元数据包含原负责人账号或旧退回原因 | 正式负责人正确，但兼容字段仍可能显示旧人；简表可能重新显示已清空的退回原因 | 当前负责人同步到兼容账号；没有确定负责人就不返回旧账号；列表、详情、概览对当前退回原因保持一致 |
| 后台为他人创建商机时，表单团队与当前主团队不同 | 保存静默使用新的主团队，违背明确提交的团队 | 显式团队不匹配则返回 422、提示刷新且不创建；刷新后可提交，省略团队的旧客户端保留默认主团队行为 |

导入元数据、原始 attributes 和历史事件均保留。它们是历史证据，不能覆盖已经更新的当前字段。任务取消仍保留历史负责人；转交不改变发起人。商机自建团队与兼任团队选择规则未扩展。

## 扩查结论与边界

- 客户、商机、拜访、任务主要详情与列表，以及后台关联入口已作代码链路审查；前端历史只读、客户跳转、归档回执、详情历史定向测试 50 项通过，没有为假设输入增加前端补丁。
- 新增历史商机实际 HTTP 读测试：金额、概率、具体日期全未知；只有年度季度；明确金额 0。覆盖 header、overview、browse、timeline、旧详情与后台详情/列表，共 4 例、35 次 GET；未知仍为 JSON null，季度精度不被猜成具体日期，读取不修改原行版本或时间。
- 客户认领/释放的归属事务、账号任职更新、任务接受/完成/验收/取消、伙伴 ID 与渠道、商机关闭状态已核对。保留客户原业务团队、伙伴名称人工保存快照属于现有口径，不当作陈旧字段自动回写。
- 后台商机列表 `total_amount` 目前仍是既有“已知金额合计”口径，全未知时顶层合计为 0；条目金额保持 NULL。本轮未改变该汇总口径，也不把接口读取通过表述为汇总口径已统一。
- 一次性 PostgreSQL 合成数据验证不代表逐条检查生产历史，未调用真实 AI 或飞书。线上恢复仍需后续合并、发布及生产验收。

## 回归入口

所有数据库用例通过一次性 PostgreSQL 运行器执行，测试结束删除数据库。业务断言使用非超级用户、非 BYPASSRLS 角色。

```sh
python -m pytest backend/tests --ignore=backend/tests/integration -q
python backend/tests/system/run_integration_postgres.py
python backend/tests/system/verify_cross_module_robustness_postgres.py \
  test_assistant_archived_receipts test_attribute_projection_consistency \
  test_opportunity_owner_consistency test_legacy_opportunity_reads
PYTHONPATH=backend/src python backend/scripts/export_openapi.py --check
ruff check backend/src backend/tests --select F
```

新增回归先复现再修复：历史空行动/空名称分别命中 Pydantic 字符串错误；客户清空后回读仍有旧值；任务转交后兼容账号仍是旧人；显式团队漂移实际错误返回 201。历史商机读矩阵没有发现新的业务异常。

完整验证结果、失败和修复日志、源码提交与本机运行回执单独保存于交付目录 `incident-20260927-visit-save/similar-issues/`。仅最终未改动版本的验证回执用于合并门禁。
