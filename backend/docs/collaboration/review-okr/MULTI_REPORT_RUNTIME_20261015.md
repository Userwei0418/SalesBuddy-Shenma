# 多范围工作报告真实 PostgreSQL 取材与持久化

本次修复针对实际 ASGI → PostgreSQL 路径发现的两处阻断：

- 多范围快照创建在写事务中调用只允许只读事务的范围解析器，导致首次创建始终 403。快照创建改为可重复读写事务；事实取材仍强制只读可重复读。冻结范围后续授权检查继续校验真实 workspace、user、role 和 RLS，不把 scope hash 当授权。
- 普通来源取材访问旧单范围字段 `member_ids/team_id`，v2 范围不存在这些字段。现在逐一查询当前解析出的同类主体，按业务 ID 合并后再交给统计组装；同一 ID 数据冲突时拒绝，不重复计算共享拜访。

另修正无来源 v2 动作权限检查的 SQL 参数编号及异步生成器误用。

可执行验证：

```sh
PYTHONPATH=backend/src /tmp/sb-report-qa-venv/bin/python backend/scripts/verify_work_report_multi_postgres.py --socket /tmp/sb-review-pg.ef7crm --port 55432 --output /Users/zhouwei5/Developer/SalesBuddy-deliveries/review-okr-20261014-01/control/multi-final-20261015/result.json
```

该脚本仅创建和销毁自己的随机隔离数据库及非超级用户、NOBYPASSRLS 角色。隔离数据库直接迁移至 V250，API 和 Worker 角色均按当前 runtime_permissions.json 精确配置并运行 reconcile，没有 fixture 额外授权，再调用真实 HTTP 路由、持有真实数据库 job lease 的 snapshot/generation handler；团队和成员均为两个主体，首次不传 revision，共享拜访只纳入一次，保存后重新 GET 必须一致，收窄授权后已保存文档必须拒绝。

证据限制必须保留：API 身份由隔离 fixture 注入，未验证真实登录；运行的是实际 leased handler，不是独立常驻 Worker 调度器。模型端显式设为不可用，周报仅验证真实 `basic_summary/provider=none` 回退保存，不证明中台 Agent 或模型成功；日报不能用这个回退代替 AI 生成验收。回执记录权限清单 SHA256、空的额外授权列表，并确认隔离数据库及 API、Worker 两个角色均已清理。未改 LOCAL、生产、数据库迁移或权限清单。

## 通用多选报告补充

scope v2 只描述取数范围，不隐含 Review 模式。日、周、双周、月、季通用报告均保留原方案；只有显式 daily/weekly 才准备 Review facts，销售六节事实仍仅适用于周报。补充 monthly-result.json 以无 review_mode 的双团队月报执行真实快照、生成回退、保存和重新读取；完整冻结主体在读取及写入、共享、导出动作时重新授权，包括当期没有贡献来源记录的主体。
