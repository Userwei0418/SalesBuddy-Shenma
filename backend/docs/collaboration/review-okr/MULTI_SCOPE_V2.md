# 同类多选复盘范围 v2

本增量用于在同一复盘中查看多个团队或多个人员。`overview / opportunities / activities` 三条 Review GET 支持重复的 `team_ids` 或 `member_ids` 查询参数，同次只能选择一种主体，最多 100 个，服务端按 UUID 排序去重。原 `team_id / member_id` 单值请求仍返回 v1，不能与数组混用。

数组请求返回 `review.overview.v2` 或 `review.page.v2`。`scope` 包括明确的主体、当前已授权成员并集、`scope_key` 与 `scope_revision`。后续请求可用 `expected_scope_revision` 验证同一范围；当前授权或成员变化返回 409，主体已不可访问时先返回 403。`self + expected_scope_revision` 进入 v2；旧单值请求附带修订号返回 422，提示使用数组，避免忽略修订检查。范围修订号不替代报告事实快照。

服务端在同一只读可重复读事务中解析所有主体并读取事实，先按业务 ID 取并集，再过滤、汇总、计数、排序与分页。共享拜访和任务只计一次；任务负责人、事件及来源引用按稳定标识合并。商机变动沿用 D04 的真实差异与历史覆盖规则。来源权限不完整时保留缺失状态；跟进来源不完整时不推断长期未跟进。

API 保留 `forecast_collection / collection_ppl / forecast_recognized` 三项指标。目标按去重后的同类主体分别求和：例如两个团队目标 100 与 300，合并目标是 400；完成率由合并后的业务金额除以 400 得出。团队目标不再叠加成员目标，也不平均完成率。

`target.aggregation` 固定为 `sum_distinct_subject_targets`。`components[]` 为 `scope_type / subject_id / target_id / version_no / status / value`；`status` 为 `effective / no_target / permission_denied`。拒绝的目标 ID、版本和值均为 null。任一分母缺失或拒绝时，整体 `target.value`、完成率和差额为 null；`known_subtotal` 仅给出已授权已知目标小计，`coverage` 区分完整、部分与不可用。只有单主体时外层目标 ID 与版本可保留，多主体时为 null。

多选报告生成通过工作报告入口配套，`generation.available=true`，原因码为 `work_report_multi_scope_v1`。客户端提交 `work_report.scope_selection.v2`，服务端在同一事务解析并冻结所有主体与成员范围，再按 ReviewFactsV2 生成、保存并支持刷新重入；禁止自动取第一个主体、客户端拼接旧报告或使用旧快照冒充多选结果。既有单值报告流程保留。

验证证据为 `DELTA04_D02_POSTGRES_R02.json` 的受限 PostgreSQL V243 / ASGI 六组、29 次 HTTP 与完整合成响应。新候选精确继承 D04 和范围聚合源码；OpenAPI 从应用生成。当前是源码和隔离验证，不表示生产或多选报告已经交付。
