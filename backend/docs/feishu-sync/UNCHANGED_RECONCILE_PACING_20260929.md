# 历史对账的本地快速结案

状态：候选实现已完成本地回归，尚未部署。本次仅修改后端消费行为；V198 定向触发器修复、历史队列清理和生产发布分别验收。

## 问题与变化

既有同步服务已通过 `source_fingerprint` 比较当前投影、目标表、字段映射和关联记录：内容未变化时不请求飞书。但历史消费入口此前对每条后台对账都等待 5 秒，本地已经确认无需远端处理的事件也重复等待。

现在只有满足下列全部条件的事件返回 `source_unchanged`：

- 命中现有 `source_fingerprint` 相等分支，目标表一致，且不是强制远端核验或重试。
- `queue_origin=reconcile`、`historical=true`，`first_formal_create=false`、`notification_planned=false`。
- 没有 `notification_snapshot`，执行原有配置/租约校验和通知处理后，没有任何投递记录。
- Worker 已凭当前租约成功写入完成回执。租约丢失、处理异常或完成回执失败均不产生快速结果。

处于原后台消费范围的上述事件，在释放对象锁和数据库连接后等待 0.1 秒，本地处理持续速率不超过每秒 10 条。其他后台事件继续沿用 `FEISHU_HISTORY_INTERVAL_SECONDS`，默认 5 秒；没有改变现有配置项或其取值范围。业务事件、业务事件的前驱优先级和原有节流豁免保留。

`source_unchanged` 表示当前本地投影与上次成功同步的本地回执一致，不表示本轮读取并核实了飞书内容。管理员强制核验及失败重试仍检查远端，不能借快速分支跳过修复。

## 保留的约束

- 不改指纹算法、映射、通知内容、租约、对象锁、领取顺序、每第 11 次 FIFO 公平调度或原有紧急对象文件。
- 首次通知、更新快照、任何已存在的投递回执，都继续走原处理流程；成员直接分配任务的既有通知规则不变。
- `refresh` 全量展开、旧投影 hash 补指纹、映射停用、来源缺失、依赖未就绪、限流、未知远端写入结果、强制核验及重试均不能加速。
- 每轮清空结果，空队列、配置校验或失败不能继承上轮的快速状态。
- 成功完成日志只记录事件 ID、对象类型和结果，不输出业务正文、配置或凭据。入口幂等安装本 Worker 专属 INFO 输出，不提高全局或 HTTP 客户端的日志级别。
- 不清理、重排或合并现有队列，不新增并发消费者。外部 API 和 OpenAPI 合同未变化；无新增数据库迁移或权限。

## 本地验证

```bash
cd backend
.venv/bin/python -m pytest -q tests/test_feishu_unchanged_outcome.py tests/test_feishu_history_paced_worker.py tests/test_feishu_worker_scheduling.py tests/test_feishu_event_driven.py
# 65 passed

.venv/bin/python -m pytest -q tests/test_feishu*.py tests/test_task_import_feishu_projection.py
# 532 passed；2 条既有依赖弃用提示
```

新增回归覆盖：无远端调用的快速分支、首次通知仍规划、四类投递回执、快照拒绝、force/retry 仍查远端、旧 hash 和 refresh 不快、guard/notify 异常、真正完成后才记录结果、lease 丢失无成功回执、远端错误保留原回退、逐轮重置、0.1 秒预算与释放连接后等待，以及实际 stderr 输出一次安全回执且不改变其他 logger。

改动文件静态检查中，Service、paced 入口和测试通过 Ruff；Worker 保留一项既有 `B023`（`main()` 的 LISTEN 回调闭包引用 `wake`），在原 `12e85fd` 代码上同样复现，与本次修改无关，本次未改动该监听逻辑。`git diff --check` 通过。

以上为本地替身和原测试环境验证，未处理生产队列、未请求真实飞书、未部署或重启服务。发布时必须确保唯一消费者运行的是包含本次 Worker、Service 与 paced 入口的一致代码版本；不能只替换入口而遗漏配套实现。
