# 小程序写入页面异步健壮性核对（2026-09-27）

本轮在现有业务流程上补充并发与生命周期保护。审核范围为七个页面，实际修改六个页面；`visit-entry` 现有草稿动作标识与账号隔离保护保留，只增加回归。任务状态、权限、分配规则、客户及商机字段、AI 调用口径不变。

## 修改范围

| 页面 | 本轮保护 |
| --- | --- |
| customer-create | 确认窗及在途写入共用锁；账号或已确认内容变化不提交；隐藏后未完成确认作废；迟到回执不影响新账号；隐藏期间成功后不再次建档，返回页面完成原返回流程；语音转写与 Agent 两阶段均校验原账号/草稿。 |
| customer-assign-confirm | 建档和下发保留两阶段现有流程；建档成功即记录原账号草稿和内存中的客户 ID；切账号后不继续用新账号下发；失败后只补未完成阶段；重复确认、隐藏及成功后的重复提交受保护。 |
| customer-edit | 保存锁定客户 ID 和账号；重载不提前解锁在途写；隐藏页不弹成功/失败提示或延迟返回其它页面。 |
| customer-claim | 确认只消费一次；隐藏时未提交确认作废，已经发出的写保持锁；返回页面先读到旧名单时，迟到写结束后再读一次权威名单；网络结果未知也读回，不自动重发 POST。 |
| opportunity-create | 商机表单异步准备/关闭确认完成后再次核对账号、客户、商机及页面可见状态；旧回执或 finally 不覆盖新对象、不解除新操作锁；原有完整保存回执校验保留。 |
| visit-confirm | 质检、归档、补充信息和建议各自绑定原账号、客户/记录及版本上下文；旧 finally 仅清本次计时器及锁；隐藏后确认不新发请求；切账号清旧同事和商机选择缓存后恢复新草稿；建议回读有序号，不让旧未采纳状态覆盖新采纳状态。 |
| visit-entry | 源码未改；补测切账号后的迟到结构化/转写不能回填、污染新账号草稿或导航。 |

新增公共工具 `miniprogram/utils/pageWriteContext.js` 区分“同一账号/对象仍有效”和“页面当前可见”：在途写可以完成并保留本页结果，提示和导航只发生在当前可见页；尚未提交的确认必须重新核对可见状态。旧请求的 finally 不能释放另一次写入的锁。

传输层继续复用已有 `apiClient/requestIdentity` 幂等策略。网络超时或未知结果不自动再次发送 POST，保留原请求幂等键供人工重试；本轮不新增服务端幂等协议。现有 VM 测试只补真实 helper 与会话注入，不减少业务断言。

## 本地验证

```bash
node --test frontend/tests/page-write-context.test.js frontend/tests/business-write-robustness.test.js frontend/tests/customer-claim.test.js frontend/tests/visit-entry-draft.test.js frontend/tests/customer-create-fields.test.js frontend/tests/customer-edit-name.test.js frontend/tests/opportunity-names.test.js frontend/tests/visit-archive-advice.test.js frontend/tests/visit-split.test.js frontend/tests/visit-flow.test.js
git diff --check -- frontend
```

定向测试 118 条通过；涵盖正常流程、重复确认、确认后换账号/对象/内容、隐藏再返回、旧请求晚成功/晚失败、原账号草稿保留、旧 finally 不解锁新请求、建议新旧回读交错和新账号同事目录延迟返回。

这是本地代码和自动化回归证据。完整前端测试、本地配套环境构建/验证由主任务统一执行；本轮没有部署、上传小程序或修改生产数据。
