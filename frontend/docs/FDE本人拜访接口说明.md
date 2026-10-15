# FDE 本人拜访录入与统计

2026-09-13 确认口径：FDE 成员和负责人均需要自行填写、确认归档拜访记录。只统计该 FDE 本人填写并确认归档的记录，新老客户均计入，不按短期或长期分类。多人分别填报时分别计算记录数，不推测物理出访次数。

## 录入路径

- 总览、客户详情可以进入共用拜访页；选择客户后，通过专用接口选择本人当前参与的商机。
- 商机详情提供“录入拜访”，携带 `customer_id`、`opportunity_id`；原文页重新精确验证，名单失效时提示重新选择，不默换其他商机。
- `fde-visit-opportunity` 组件用于原文页和确认页，统一搜索、分页、恢复草稿及身份变化时的校验。
- FDE 商机必填，销售仍然选填。FDE 不提供新建商机、商业字段修改；本人拜访确认页不代选他人或维护协助名单。FDE 负责人在独立商机详情中仍可按既有授权维护本团队名单，普通成员禁止。
- 登录会话的 `visit.create` 由后端决定，前端不自行授予；开通后的 FDE 显示正常录入入口。

## HTTP 契约

| 接口 | 前端用途与参数 |
| --- | --- |
| `GET /fde/visit-opportunities` | 只返回本人当前可录入商机，不使用负责人团队读取范围。支持 `customer_id`、精准 `opportunity_id`、`q`、`limit`、`offset`；返回 `items/total/has_more/next_offset`。商机至少包含 `id/name/customer_id/customer_name`。 |
| `POST /conversations` | FDE `visit_entry` 增加 `opportunity_id`。API 适配器 `runAgent(mode, text, customerId, opportunityId)` 的第 4 参数可选，原销售调用保持兼容。 |
| `POST /visits` | `fields.opportunity_id` 为最终选择商机，审核 run 也必须绑定该商机。FDE 不提交代选 `fde_participant_ids`，由服务端确定本人归档身份。 |
| `GET /fde/activity` | 商机“我的拜访记录”使用 `scope=self&period=all&opportunity_id=…`，不遗漏往年记录。保留分页和 `can_read_detail` 权限标记。 |
| `GET /fde/dashboard` | 看板和“我的”展示服务端 `period_visits`（本人归档记录数）、`active_recorders`（实际填报人数）；不从前端参与名单或记录列表推算。 |

FDE 确认页同时保留 `reviewedContent` 与 `reviewedOpportunityId`。正文或商机变化后重新审核，避免旧商机审核结果被用于另一商机。后端仍需验证实际关联、审核 run、模型结果和归档事务；前端校验只是交互反馈。

## 读取与展示边界

商机详情默认展示“我的拜访记录”，另保留“全部跟进”用于客户业务全貌只读。商机或客户可见不代表可录入：本人当前参与关系通过专用接口单独核验。负责人查看团队商机时，也仅能在本人参与的商机录入。

地图继续展示参与商机关联客户的完整信息；总览继续复用已有通知与任务；“我的”展示本人拜访和协作数据。参与销售拜访的通知可以保留，但不能因此自动增加 FDE 本人归档次数。

## 本轮验证范围

新增前端回归覆盖本人/负责人入口、精准关联核验、移出后不默换商机、晚到响应、分页、AI 商机上下文、换商机重审、本人全历史记录、商业写限制及实际 HTTP 参数。原有销售无商机录入保持回归。

运行：`node --test frontend/tests/*.test.js`、`python3 frontend/scripts/verify_frontend.py`。自动化通过不代表微信开发者工具的实际编译或视觉验收完成；原生验证由主任务另行记录。
