# Web AI 建议恢复与显式生成

本增量基于 Web `991b2d7`，共享代码来自 Mini 固定提交
`a9b938ce25a11a3fedfac612a9b6682f9384337f`。当前运行和发布状态由主仓
`docs/PROJECT_FACTS.md` 维护；本文件只说明实现和验证边界。

- 商机详情、拜访详情、归档建议和知识库 Playbook 进入或刷新时只读取已有建议。
- 生成是明确的用户动作。商机业务只读不禁止独立的建议生成，仍检查建议读取、类型和请求权限；业务编辑与建议决策的原限制保留。
- Playbook 仍使用有详情读取权限的客户目录，并明确筛选客户身份；实际 overview 为纯伙伴时显示不适用，不发建议 GET/POST，双身份客户仍可使用。
- 排队、处理中、失败和失效分别显示。拜访详情可以切换“拜访建议／待办建议”，归档后没有内存中的建议编号也能进入恢复。
- Playbook 请求绑定公司、用户、权限和客户；迟到回包不能覆盖新客户或新身份的内容。POST 回执丢失或为空时先通过 GET 查明结果，避免立即重复提交。
- 共享请求池对已经失效的旧 GET 允许重新读取，旧请求清理不能删除新请求。共享代码通过原导入程序生成，不手改副本。

## 配套接口

必须配套 `GET /api/v1/advice/latest?subject_kind=&subject_id=&section=`，
其返回为 `{state, analysis}`。只有明确的 `none/null` 视为没有建议。
POST 沿用 `/api/v1/advice`，不以浏览器等待时长终止后台任务。

Web 自有合成演示实现了同形状 GET，不能作为真实 API 或模型验收证据。

## 有针对性的验证

构建器检查所有共享清单字节；额外检查共享 JavaScript 相对依赖闭包。
本次完整导出比对发现 Web 旧清单缺少新商机页依赖的
`utils/opportunityIntentHistory.js`，已从固定导出精确导入。

隔离浏览器测试（全部阻断真实服务和模型请求）：

- `knowledge-advice.browser.mjs`：9 个读取、生成、权限、身份、失效、异常回执及纯伙伴/双身份场景。
- `opportunity-advice-ui.browser.mjs`：3 个只读业务与独立建议权限、操作绑定场景。
- `opportunity-advice-readonly.browser.mjs`：2 个实际构建/runtime/共享处理器的读取与生成场景。
- `visit-advice-recovery.browser.mjs`：2 个实际构建的详情分栏恢复和归档入口场景。

`account-boundary.test.mjs` 与 `workflow-completion.test.mjs` 共 18 项窄回归；`preview.test.mjs` 补 1 项最新建议与账号隔离契约检查，`knowledge-customer-scope.test.mjs` 补 1 项客户搜索接口与身份参数检查。
这些检查不替代最终配套 LOCAL 的真实入口恢复和一次显式生成验收。
