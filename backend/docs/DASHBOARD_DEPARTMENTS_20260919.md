# 经营看板实际部门契约

本次修复已确认的团队组别增量，不改变数据库历史迁移或普通角色的业务明细权限。

- `/api/v1/dashboard/options` 的总经理团队列表使用当前有效且有有效业务成员的实际部门；`code` 为 `team:<uuid>`，`team_id` 为原部门 UUID，`name` 为实际名称。数据库没有 `team.kind`，兼容字段 `kind` 返回空字符串。
- 成员选项返回有效 `role`。总经理可选择 FDE/FDE 负责人，前端据此避免把 FDE 作为销售同级榜成员请求；主管目录仍限既有销售业务角色。
- 总经理团队视角默认返回实际部门排名。显式 `team:<uuid>` 多选只改变当前汇总选区，排名仍包含全部有效部门，零值部门也保留。`opportunity_acv.rows/groups`、`followup.rows/groups`、`active_opportunities.groups/region_groups` 使用同一部门 code。
- ACV 仅含所选季度预计关单的在推商机；近七天跟进按记录归属部门统计；活跃商机按所选季度存在确认/归档跟进去重，含 Won、排除 Lost。动态部门查询在仓储执行，使用调用方连接、原 RLS、显式总经理约束和绑定参数。
- 显式旧 `north_east` / `south_hkmo` 参数仍保持既有区域响应。单次请求混用旧区域及实际部门返回 422，避免事实和排名出现不同选区；销售/主管个人及排名接口继续沿用原有安全函数及权限。

验证：隔离 PostgreSQL、非 superuser 且不绕过 RLS 的测试角色运行 `test_dashboard_departments.py`、`test_dashboard_selection.py`、`test_dashboard_subject_rankings.py` 及既有业务排名回归；各测试事务结束后回滚，不写入生产。
