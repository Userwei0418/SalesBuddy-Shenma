# 任务完成验收闭环

按 2026-09-19 用户确认及会议逐字稿 11:03–13:40 实施。

## 行为

指定接收人拒绝：必填原因，通知发起人并结束原任务。岗位候选人拒绝只放弃本人领取；一人领取规则保持。接受后双方收到站内任务卡片。

接收人调用 `POST /api/v1/tasks/{id}/events`，`event_type=complete`，必须填写 `note`。他人任务进入 `pending_review`，不设置 completed_at、不计为完成；原发起人收到 `task_completion_submitted`。自己创建且自己领取的任务直接完成，无需自我审批。

只有原发起人能调用 `approve_completion` 或 `reject_completion`，必须传 `version_no`。确认后 completed；驳回必须填写 note，回到 in_progress，接收人无需重新接受，可再次提交。每轮 task_event 保留说明/验收意见/操作者/时间；通知去重键包含事件版本，第二轮不会被第一轮吞掉。

待验收阶段先验收或驳回，再转交/取消。旧 completed 不回退。所有写入在行锁事务内，并沿用现有租户/RLS/幂等机制。原发起人的权限不会授予其他主管或管理员代验收。

## 契约和显示

- `pending_review` 新状态；`completion_submitted_at` 最近提交时间；`completion_review_note` 最近验收意见。
- `verification_status` 复用已有 pending / passed / rejected / not_required。
- requires_action 在待验收时只对原发起人 true。
- 全部待办和客户任务统计仍包含 pending_review；已完成仅 completed。
- 小程序、后台任务详情新增验收动作；通知卡片回读最新任务状态。
- 今日待办 Agent 的执行建议仍针对待执行任务；验收通过站内卡片、任务列表和首页入口处理，避免催接收人重复执行。

## 发布顺序与边界

数据库迁移 V104（负责人已只读审核通过）→ 后端 → 配套小程序。旧客户端虽不能直接结单，但旧按钮可能仍提示已完成，因此需配套更新体验版后验收。

本轮仅开发及隔离测试，未部署生产、未上传体验版，也没有创建真实业务任务。前端开发者工具视觉测试使用临时页面数据，已还原运行时守卫与页面；不作为真实通知投递证据。

后端分支 `codex/task-completion-review-20260919` 基于生产 84437a7；前端分支 `codex/task-review-ui-20260919` 基于 a36003d，保留邮箱/可选手机号/记住密码/账号补全。
