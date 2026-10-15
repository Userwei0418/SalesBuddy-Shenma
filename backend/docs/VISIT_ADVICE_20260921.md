# 销售跟进归档后的日常/客户待办建议

## 已确认规则

销售未关联商机的跟进记录仍可请求 `POST /api/v1/advice`（subject_kind=visit, section=tasks）。采纳必须提交 association_kind=daily，customer_id/opportunity_id 为空。建议自身仍关联原拜访和客户；正式日常任务通过 source_suggestion_id 保留来源，不变成客户任务。

已关联商机的拜访、客户/商机经营建议沿用客户任务约束。客户级建议无固定商机时仍需人工选择该客户下商机。前端 FDE/FDE主管归档不自动推荐的规则不变。建议不是正式待办，只有人工确认采纳才调用 TaskService 创建任务。

## 实现及兼容

去掉请求阶段对无商机拜访的拒绝。采纳阶段按持久化的建议 subject_kind/opportunity_id 决定允许的任务类型，不信任前端更改类型；原账号校验、事实指纹、RLS、建议版本锁与幂等回执继续执行。提示词说明无商机时生成日常行动建议；提示词指纹变化使旧缓存按既有机制失效。API 字段未增加，OpenAPI 更新行为说明。无需数据库迁移。

旧小程序不会自动请求无商机拜访建议；新小程序依赖此后端，新前端不能在旧后端下宣称验收完成。

## 版本与验证

开发基线 c66b8cea379727ce11b8a8551e7f886cd980a30c，分支 codex/visit-daily-advice。开发前公网 health/version、服务器 RELEASES.json 与 API/普通Worker/飞书Worker实际进程目录均确认为 org-affiliation-20260920-1bbfd98；本次不写真实业务记录。

隔离 PostgreSQL 回归覆盖无商机/有商机拜访的请求、生成、类型错误拒绝、人工采纳、同幂等键重放，以及实际任务关联字段和 source_suggestion_id。模型使用受控测试输出，未冒充生产 Agent 或真机验收。前端覆盖清理失败、推荐失败重试、准确详情跳转和 FDE 不自动推荐。

上述为 2026-09-21 开发批次状态。当时源码未部署、未上传；现已于 2026-09-23 随 1.0.7 发布补齐配套后端。线上 `278d7c5` / V124，三个服务同版本；实际经理账号对无商机真实跟进成功生成建议，表单正确为 daily 且不关联客户/商机，未实际采纳或创建正式任务。两种待办的采纳及幂等由隔离 PostgreSQL 全量回归验证，不能把经理只读/建议表单检查称为所有销售账号真机验收。小程序 1.0.7 上传成功，用户已确认设为体验版。完整证据见 [发行回执](../../docs/RELEASE_1.0.7_20260923.md)。

本地结果：前端728项、后端单元1775项、隔离PostgreSQL建议链路18项通过；OpenAPI一致性、前端工程校验及本次修改文件Ruff F检查通过。额外全仓Ruff扫描发现3项既有未使用变量/导入（test_company_admin_isolation、test_visit_attendance、test_tasks_contract），未扩大本轮范围修改。
