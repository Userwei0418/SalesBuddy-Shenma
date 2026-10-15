# 当前 API 契约入口

核对日期：2026-09-11。完整且可验证的路径、参数、响应以 [openapi.yaml](../openapi/openapi.yaml) 和 API 源码为准；该文件由 scripts/export_openapi.py 导出，契约测试检查漂移。

## 通用语义

- 前缀 `/api/v1`，认证为 Bearer token；主体、工作空间和权限由服务端解析。
- 正式提交使用 `Idempotency-Key` UUID；同一次操作及不确定响应后的重试使用同一键，同键改内容返回409。旧客户端可省略该 Header，但不具备统一请求级防重保证。
- 乐观版本由具体请求体中的 `version_no` 等字段表达，不存在统一必填 `If-Match` 协议。
- 分页依接口而定，现有 offset/page_size 等参数不能当作统一 cursor 协议；地图与经营汇总使用完整权限事实，不能用列表第一页代替。
- 错误通常为 FastAPI 的 detail；幂等冲突另有 code。不要假定所有响应都有 request_id/retryable 或同一种自定义错误体。
- 日期字段按各业务定义：拜访/录入日期为日粒度；系统时间保留时间戳；展示时按北京时间。

## 已实现的主要动作

| 动作 | 路径（省略 /api/v1） |
|---|---|
| 登录与首页 | POST /auth/session；GET /assistant/home |
| 客户查看、创建、修改 | GET/POST /customers；GET/PATCH /customers/{customer_id} |
| 客户认领 / 运营审批 | POST /customers/{customer_id}/claims；POST /console/claims/{request_id}/decision；旧assignments返回410 |
| 商机创建/修改 | POST /customers/{customer_id}/opportunities；请求体action区分create/update，更新须指定opportunity_id |
| 文件提取任务 | POST /visit-imports；GET /visit-imports/{import_id}；POST /visit-imports/{import_id}/retry |
| 直接短音频转写 | POST /audio/transcriptions |
| AI 草稿、审核、报告 | POST /conversations；POST /conversations/{conversation_id}/messages；GET /agent/runs/{run_id} |
| 拜访字段 / 归档 / 补充 | GET /visits/form-schema；POST /visits；GET/PATCH /visits/{visit_id} |
| 任务与事件 | GET/POST /tasks；GET /tasks/{task_id}；POST /tasks/{task_id}/events |
| 经营读取 | GET /dashboard、/workbench、/customer-assets、/customer-assets/map |
| 地图缺评分补齐 | POST /customer-assets/map/refresh；同 GET 的范围参数，只用当前身份补评 |
| 个人与团队指标 / 目标 | GET /profile/evaluation、/profile/performance；POST /profile/sales-targets |
| 通知 | GET /notifications；POST /notifications/{notification_id}/read |

商机保存复用同一POST接口，当前没有PATCH /opportunities/{opportunity_id}。也不存在文档早期设想中的visit-drafts/confirm、customer-drafts/confirm或workbench/overview接口，调用方不得据旧设计拼接地址。

## 正式写入与候选

AI 产物是待确认输入。拜访归档核对服务端已保存审核、客户范围、可选商机和当前字段，严格高于60分且下一步审核通过；同事务保存归档及联动。客户创建不自动生成初始商机；管理建档后下发是第二步请求，各自使用请求回执。

任务创建写真实任务、接收人、事件和通知；接受/拒绝/完成由角色及状态机校验。通知已入库表示应用内可读，不表示已发送微信订阅消息或第三方通知。

任务列表（包括 `tab` 分页及旧列表）每项与详情顶层返回 `can_cancel` 布尔值：当前任务的 `task.cancel`、任务协调资格同时成立，且状态不是 `completed`、`cancelled`、`pending_review` 时为 true。前端据此展示取消入口；提交仍走既有 `POST /tasks/{task_id}/events`，传 `event_type=cancel`、非空原因和当前 `version_no`，服务端重新校验。取消保留任务、关联负责人和事件历史，移出未完成计数、不计入已完成；`rejected` 分类兼容包含拒绝和取消。无删除或重新打开任务接口，取消不会撤回 AI 建议采纳。

显式转交（`event_type=reassign`）继续使用人工选择的 `assignee_account_code` 作为新负责人，不由 Agent 重选。转交事件与通知的 `payload` 新增操作时姓名快照 `actor_name`、`previous_owners[].name`，旧负责人的 `user_id/role/team_id` 与新负责人的 `new_owner.user_id/display_name/account_code/role_code/team_id` 保留。通知标题说明谁转交给谁，正文仍为任务标题；接收者仍为原负责人、新负责人和发起人的去重集合，排除操作者。姓名只是历史展示信息，权限及通知接收者按稳定 ID 判断；旧事件没有姓名字段时不可推测补写。同一负责人重复转交或旧版本提交返回409，任务、历史、通知不变。详见 [转交证据说明](TASK_TRANSFER_EVIDENCE_20260927.md)。

当前 ChatBI 由受权限限制的数据库事实装载后交给模型生成回答；不让模型输出 SQL 并执行。早期“查询计划编译器、统一游标、Sunset Header”等设想未作为已交付接口保证。细节见 [Agent 流程](03-agent-architecture.md)。

运营管理接口、认证和前端联调变化见 [运营后台](../../docs/OPERATIONS_CONSOLE.md)，参数以OpenAPI为准。

## 详情与历史分离（2026-09-14源码）

新客户端使用客户/商机overview读取完整summary和profile，拜访、任务、联系人、时间线与运营历史分别按页读取。季度实绩通过`GET /customer-assets/quarters`从真实账本聚合。完整路径、返回结构、权限边界和旧契约兼容见[详情读模型](../../docs/DETAIL_READ_MODELS_20260914.md)；此处描述源码能力，实际发布版本以CURRENT_STATUS为准。

## 2026-09-27 当年活跃地图（本地候选，未发布）

地图以北京时间当年1月1日至业务当天的正式跟进去重判定活跃，使用实际跟进日期；与是否关联商机、是否已有评分无关。GET 保留全部授权活跃客户，新增 `active_customer_count` 与 `map_status`（ready/updating）；读取无队列写入。新前端只展示完整点位与相同客户列表，不显示待评估名单；缺坐标时显示整图更新/失败状态。

POST `/customer-assets/map/refresh` 复用 `battle_map.read` 和客户级 RLS，返回 `status`（ready/updating/unavailable）、`enqueued_count`、可选中文 `reason`。支持 Idempotency-Key；按租户/访问者串行检查缺项，复用活动任务，终态十分钟内不重新入队；不覆盖成功评分或重置历史失败任务。普通个人视角不代替他人评分；FDE范围参数仅筛协作客户，补评仍属于当前身份。客户聚合视角不让空主体行抹掉有效可见评分；分数、政策、评估ID须来自同一行。

前端首次发现缺评分后只发起一次补评，随后每3秒最多10次查询；隐藏/卸载/新加载中止轮询，迟到响应不回写。窗口不为当年1月1日时拒绝混用旧半年后端。上线须先部署兼容后端、补齐历史覆盖再发布小程序，详见 [本次验收记录](../../docs/ACTIVE_CUSTOMER_MAP_20260927.md)。
