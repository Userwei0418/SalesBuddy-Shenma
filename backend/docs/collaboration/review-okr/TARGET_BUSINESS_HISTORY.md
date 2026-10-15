# 业务目标历史入口修复

普通业务页面原先调用 `/api/v1/console/targets/{target_id}/history`，即使本人已有 `target.history`，也会被后台入口所需的 `access.console` 拒绝。

新增 `GET /api/v1/targets/{target_id}/history?limit=50&offset=0`。普通业务登录 `/api/v1/auth/password/login` 服务端仍签发原 `wechat-mini-program` session channel；此渠道同时供当前业务 Web 与小程序使用。接口采用原 `get_identity` 和全局 `target.history` action 检查，复用 `TargetRepository.history` 与 `security.target_history_rows`，按 `target.read` 可见范围与 `target.history` 主体范围交集读取。保留原 console 路由、入口权限与数据库授权，不新增迁移。

响应完全复用原 `{items, limit, offset}`；每条 `items` 为 `id / occurred_at / actor_name / action_code / before_snapshot / after_snapshot`。历史按时间及 ID 倒序；`limit` 为 1–100，`offset` 不小于 0。不存在或不可见目标返回 404；缺少历史操作或主体权限返回 403。不能通过历史接口获取技术审计范围。

配套 Web 只替换业务页面的历史请求 URL，运营后台仍用原路径。OpenAPI 从应用重新生成。隔离验证脚本 `backend/scripts/verify_target_business_history_postgres.py` 使用 V240、受限 API 角色、真实路由门禁与 RLS；仅身份来自合成 fixture，不覆盖管理身份或权限依赖。成功回执保存在本轮交付证据目录。源码与隔离验证不代表已部署或已完成真实页面验收。
