# 详情与只读页面异步稳健性检查

本轮检查切换账号、权限版本变化、页面隐藏/卸载、切换业务对象时的迟到响应。只增强现有操作的上下文校验，不调整授权范围、业务计算、角色规则或保存流程。

## 覆盖与处理

| 页面/组件 | 实际问题或已有保护 | 本次处理 |
|---|---|---|
| `risk-detail` | 旧读取可以覆盖新账号；旧失败/解除完成的返回计时器可以影响后续页面；迟到确认可发起写入 | 请求序号、身份、风险标识和生命周期校验；离页取消计时器；确认和保存回调检查原上下文；解除请求在途离页保留锁，返回后结算补权威读取 |
| `risks` | 刷新、换账号和离页后，旧成功/错误/finally 均能改写页面 | 最新请求与身份检查；换身份清旧列表；离页使请求失效 |
| `report-detail` | 成功只检查公司/用户，未检查角色和权限版本；旧失败会清除新报告；隐藏后不重读 | 统一成功/错误的身份及序号检查；离页清详情，返回重新鉴权和读取 |
| `visit-detail` | 已有离页和身份保护，但重新加载另一条跟进未取消旧建议与商机跳转 | 新主记录加载使建议/链接请求失效；回包核对跟进标识或当前记录 |
| `customer-detail` | 已有 `DetailReadSession` 保护主档、汇总、分页、展开记录 | 保留现有会话实现；补主档响应与请求客户 ID 一致性检查 |
| `customer-assets` | 主档和多数列表已有会话/序号保护；Demo 失败/finally 未防旧响应；FDE 本人记录、资格与异步保存缺对象边界 | 按身份、客户、商机和页面代次检查；切对象重置相关读取；保护协助名单、登记、作废的确认与回包；在途保存跨 hide/show 保留锁，迟到结算按需重读 |
| `member-growth` | 画像读取、图表查询与返回计时器无离页/身份保护 | 序号、账号、权限版本、图表维度检查；离页取消计时器；返回重新读取；同身份 FDE 看板保留组件及筛选 |
| `fde-records` | 页面是 `fde-dashboard` 的记录模式壳；返回未重新核验入口 | 离页卸载记录组件，返回鉴权后重建记录上下文 |
| `fde-dashboard` | hide 只关人员选择器；旧看板/记录/周期响应仍有效，记录模式 show 不重读 | hide 使请求代次失效；show 恢复看板或从首屏重读记录；保留既有人员选择器和季度筛选；周期失败也检查身份 |
| `workbench` | 已有账号/请求序号保护，但 hide 不失效；返回可能复用被丢弃请求的 Promise | hide 清搜索计时器并使读取失效；清旧加载 Promise；有未完成请求则返回重读，已完成的同身份新鲜缓存保留 |

客户新建/编辑/认领、商机新建、跟进入口/确认以及任务页面不属于本文件的变更范围，由对应专项记录维护。

## 验证

新增 `frontend/tests/detail-page-lifecycle.test.js`，使用可控制完成顺序的 Promise 和计时器，覆盖旧成功、旧失败、旧 finally、迟到确认、卸载与重新进入，以及正常解除风险、保存协助名单和登记实绩。另覆盖写入未结束时离页返回的重复点击，确认原 POST 不重放、结算后只补权威读取；风险解除传输失败也先回读实际状态。当前新增 **25 项通过**。相关页面、FDE、经营数据与现有读取会话回归合计 **189 项通过**（已包含这 25 项，不能重复累计）。

在仓库根目录执行：

```bash
node --test frontend/tests/detail-page-lifecycle.test.js frontend/tests/detail-history-pages.test.js frontend/tests/detail-light-consumers.test.js frontend/tests/detail-read-requests.test.js frontend/tests/detail-read-session.test.js frontend/tests/history-readonly-fields.test.js frontend/tests/customer-assets-readonly.test.js frontend/tests/workbench-quarters.test.js frontend/tests/fde-identity.test.js frontend/tests/fde-progress.test.js frontend/tests/fde-efficiency.test.js frontend/tests/fde-company-rankings.test.js frontend/tests/fde-own-visit.test.js frontend/tests/data-integrity.test.js frontend/tests/demo-display.test.js
git diff --check -- frontend
```

以上是本地代码与前端页面行为测试，不代表微信真机网络切换、生产部署或小程序发布已验收。迟到写入如果已被服务器接受，前端不会伪装为取消；只丢弃过期页面回包。风险解除、协助名单、实绩登记/作废复用 `pageWriteContext` 保留同身份同对象的在途操作锁；若返回页面时请求仍在途，结算后补一次权威读取，不自动重放 POST。
