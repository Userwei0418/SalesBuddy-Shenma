# 飞书同步消费者的连接复用与瞬时错误处理

本次只改变同步执行时的资源使用和保护，不改变业务投影、队列领取顺序、对象锁、租约、幂等键、历史数据通知抑制或数据库权限；不需要数据库迁移，也没有外部 API 契约变化。

## 客户端生命周期

- 每个 `FeishuWorker` 保留一个客户端，最长复用 10 分钟；切换连接、公司、配置 revision、凭据更新时间或加密 key ID 时，先关闭旧客户端再创建新客户端。
- 凭据更新时间和 key ID 来自已有 `worker_config` 元信息，不写入日志。实际 token、密钥只在原客户端内存中使用，关闭时清空。
- 同一客户端只复用 token 与 HTTP 连接。字段 schema 每条事件重新读取，不设跨事件缓存：字段 ID 绑定对应的是当次远端名称，避免旧名称被另一字段复用时静默写错列。配置验证也使用新客户端和新 schema。
- API 等一次性调用保留原先的用后关闭方式。常驻消费者收到 SIGTERM 后取消当前处理并执行清理；有界消费者也必须在 `finally` 中调用 `worker.close()`。

## 写入与错误处理

不同对象可以并发写入同一张表，不再用整表锁串行等待远端 HTTP。原有对象 advisory lock 仍以连接、对象类型、对象 ID 为键，跨消费者与进程阻止同一对象同时执行；同对象队列顺序、租约和 heartbeat 不变。实际 create/update 前继续检查租约和配置，创建请求继续使用稳定的幂等 token，重试不会因事件 ID、租约或配置 revision 变化而换键。

- HTTP 429、业务码 `1254290`、旧版 `99991400` 进入 `RATE_LIMITED`；业务码 `1254291` 进入 `WRITE_CONFLICT`。这些明确拒绝均由原队列退避重试，遵守 `Retry-After` 与 `x-ogw-ratelimit-reset` 中较长的有效等待值，最长 3,600 秒。
- Bitable 精确数值业务码 `1255001`（InternalError）保留为 `FEISHU_1255001`。官方未保证写操作没有生效，因此写请求标为结果未知；读请求可重试，均至少等待 10 秒并遵守更长的有效等待头。HTTP 层不直接重放写请求。原队列下一次执行先按稳定系统 ID 查重，已存在则更新，未找到才用相同 `client_token` 创建；重复系统 ID 仍拒绝写入。沿用原 10 次尝试上限，持续失败进入死信并调查，不自动换幂等键或重置次数。此分类不扩展到其他业务码或消息通知接口。
- 字段 ID/名称明确拒绝 `1254009`、`1254044`、`1254045` 交原事件重新读取源数据、schema 和远端 ID 后处理，字段结构本身不缓存。若配置字段已不存在，新 schema 校验会停止该事件；不在 HTTP 方法内部重放写请求。
- 其他业务错误不因此获得自动重试资格；网络写入结果未知等原有边界保持不变。token 显式拒绝最多刷新一次，两次请求都经过相同错误分类；刷新后的字段拒绝继续交原事件重试，未知写不盲目重放。未改变依赖等待或整体重试次数上限。

官方给字段列表和记录查询各 20 次/秒，单记录新增/更新各 50 次/秒；同表仍可能因并发读写触发冲突。消费者数量不代表安全 QPS，当前不提高有界消费者数量。发生明确写冲突时，仅该事件通过现有 `finish` 路径延期重试，不全表暂停或重置队列。停止器应观察 `RATE_LIMITED` 和 `WRITE_CONFLICT`，不能只看 HTTP 429。

2026-09-22 的同步窗口发现整表锁导致退化：相同 6 个附加消费者，前一窗口成功增量 3,220 / 1,502.86 秒（约 2.143 条/秒），加锁窗口前 510 秒成功增量 309（约 0.606 条/秒）。生产只读锁快照显示同一 advisory 锁 1 个持有者、5 个等待者。此次仅删除整表串行，不回滚客户端复用或错误分类；修复后的真实吞吐需部署后另行观察，不以单测结果代替。

## 验证与运行边界

`pytest` 覆盖跨事件 HTTP/token 复用、每事件 fresh schema、旧字段名称被其他同类型字段复用的静默错写防护、公司/连接/配置/凭据变更失效、客户端 TTL、验证刷新、token 刷新后的字段/限流/冲突/未知写组合错误边界、不同对象同表写并发、同对象在另一消费者中被对象锁延后、跨事件/revision 的创建重试幂等键、写前 guard 和远端写取消清理。冲突测试同时覆盖 create/update 不记成功、不通知，以及消费者通过实际 `repository.finish` 生成延期状态并释放对象锁；现有通知、历史数据、关联多值、查重与未知写结果测试继续运行。并发单测使用合成连接与远端响应，真实生产吞吐和同步完成状态必须在部署后另行回读，不由这些测试代替。

```sh
PYTHONPATH=backend/src backend/.venv/bin/pytest backend/tests/test_feishu_sync_transport.py backend/tests/test_feishu_sync_service.py backend/tests/test_feishu_sync_reuse.py backend/tests/test_feishu_worker_scheduling.py backend/tests/test_feishu_sync_policy.py backend/tests/test_feishu_sync_projection.py backend/tests/test_feishu_sync_api.py backend/tests/test_feishu_cards.py backend/tests/test_feishu_worker_provision.py -q
```

官方依据：[新增记录](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/create.md)、[查询记录](https://open.feishu.cn/document/docs/bitable-v1/app-table-record/search.md)、[列出字段](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-field/list.md)、[频控策略](https://open.feishu.cn/document/server-docs/api-call-guide/frequency-control.md)。
