# Task alignment V1 — SB-REVIEW-20261014

状态：固定接口实施中，合成样例不是运行验收。V242 依赖 V240 指标字典和 V241 KR。

## 写入及兼容

既有 `POST /api/v1/tasks`、`POST /api/v1/tasks/batch` 中每个任务、`POST /api/v1/advice/suggestions/{id}/decision` 内嵌 task/tasks 增加可选 alignment。不使用客户端版本头。省略或 null 均保留旧契约，不创建指标/KR关系、不默认为财务类别；新增且为 null 的字段在幂等指纹前剔除，其余旧字段保留。

```json
{
  "alignment": {
    "metric_codes": ["daily_routine", "visit_count"],
    "key_result_ids": ["20000000-0000-4000-8000-000000000001"],
    "expected_contribution": {"metric_code":"visit_count", "value":"2", "note":"本周完成两次客户跟进"}
  }
}
```

metric_codes/key_result_ids 各最多20项，重复去重；至少一项指标或KR。选KR时服务端校验同租户/可见/有效状态，并将KR指标合并去重。无需个人目标即可关联授权团队KR。daily_routine是合法显式类别。直接指标及KR派生指标均须启用；客户端不能写单位、来源、财务入账标记。

expected_contribution 为 null 或单对象 `{metric_code,value,note}`；value 必须字符串，非负、至多16位整数和2位小数，计数指标不接受小数。metric_code 必须属于最终去重指标。单位及口径版本由字典给出。note 可空。贡献声明**不更新商机、实际回款/确收、生效目标或KR进展**。

`PATCH /api/v1/tasks/{task_id}/alignment` body 为 `{version_no:当前任务版本,alignment:上述对象}`，复用 Idempotency-Key。仅现有任务协调资格可改；completed/cancelled/pending_review 不可改。使用同一任务行锁和版本号，更新关系后 task.version_no 增1。关系不允许整体清空。存在当前不可见或归档KR关联时阻止替换，避免从已脱敏读回意外删除关系。

旧 `POST /api/v1/tasks/{id}/events` 只传 event_type/note/version_no 完全有效，保留全部指标/KR关系。`event_type=complete` 可追加 `actual_contribution:{metric_code,value,note}`，此时 version_no 和非空贡献说明必填；只记录 status=reported，继续走原完成/验收规则。验收通过不把声明自动改成已入账/已实现，不自动更新KR。财务事实仍来自原正式业务记录。

## 读回

原任务详情、常规列表、tab列表和management列表每个任务新增 alignment：旧无关系任务为null；其他原字段保持。对象示例：

```json
{
  "metric_codes":["daily_routine","visit_count"],
  "direct_metric_codes":["daily_routine","visit_count"],
  "key_result_ids":["20000000-0000-4000-8000-000000000001"],
  "restricted_key_result_count":0,
  "expected_contribution":{"metric_code":"visit_count","value":"2","unit":"次","definition_version":1,"note":"本周完成两次客户跟进","status":"planned","counts_toward_financial_actuals":false},
  "actual_contribution":null,
  "version_no":1,
  "updated_at":"2026-10-14T12:00:00Z",
  "counts_toward_financial_actuals":false
}
```

`metric_codes` 是去重后的最终集合；编辑 direct_metric_codes 才是用户直接所选集合。`key_result_ids` 仅含当前有权查看的KR；不可见关系仅返回restricted_key_result_count，不暴露ID/标题/目标值。所有旧任务总计继续按 workflow.task 每行统计，不通过多KR join重复计数；按指标/KR跨组汇总须按task_id去重。

格式错误422；无权限403；任务不存在/跨租户404；过期版本、完成状态冲突、已停用/无效指标409。旧期望版本仅任务既有兼容规则保持；新的alignment编辑和贡献声明必须提供版本。

## 数据库边界

新增 workflow.task_alignment、task_metric_alignment、task_key_result_alignment、task_alignment_history，外键均绑定workspace。API角色只SELECT前三表（RLS），通过3个受控函数读写；无直接INSERT/UPDATE/DELETE，无history读取，无内部校验函数执行。Worker/飞书角色无新增授权。记录历史仅供受控审计，不经任务权限泄漏私有KR。

发布须先应用V240/V241/V242，再切固定后端。旧后端可忽略这些附加关系表；禁止删除新关系/历史来回退。此模块不改旧迁移，不写财务表，不改变任务状态或通知规则。
