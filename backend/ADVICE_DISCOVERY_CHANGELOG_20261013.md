# AI 建议只读恢复接口

基准：`4fdaed99a773ec93545e1bf1490143e3cd24ef47`。范围为后端、OpenAPI 和离线测试；不增加迁移，不改模型生成或人工采纳流程。

详情页丢失内存中的分析 ID 后，原接口无法按业务对象找回已有建议，客户端容易把“没有 ID”当成“没有建议”，或通过 POST 重新生成。新增 `GET /api/v1/advice/latest`，供重新进入详情、切换页签和恢复未完成轮询时查询。

参数：`subject_kind=customer|opportunity|visit`、`subject_id=<UUID>`、`section=overview|tasks|visits|opportunity`（默认 overview）。覆盖拜访 overview 和 tasks；伙伴专用 Agent 不在本次范围。

返回 `{state, analysis}`：

| state | analysis |
| --- | --- |
| none | null；仅表示当前权限可读集合中没有匹配记录 |
| queued / running / succeeded / failed | 既有按分析 ID 查询的完整对象 |
| stale | 同一对象，保留既有 `status=superseded` |

按最后一次显式 POST 请求顺序选择最新可读批次，不跳过失败或未完成批次回落到旧成功结果。SQL 显式限定工作区、用户、角色、对象类型、对象 ID、页签，继续受数据库 RLS 控制。旧按 ID GET 也增加工作区、用户、角色限制，跨角色缓存 ID 返回 404；原正常响应结构不变。

独立审查在初版 dccaf6d 复现 A→B→A 缓存复活反例：POST 返回旧 A，而按创建时间发现会取到过期 B。修正为在既有 configuration_snapshot 中保留服务端 `_request_sequence` 元数据。每个成功 POST 返回路径在原对象锁和原写事务内，对选中批次赋同六维可读集合 `MAX(sequence)+1`；新建、成功缓存复用、历史复活、失败重试和返回既有处理中批次都覆盖。GET 按序号降序，再按 created_at、id 降序；无序号旧历史按 0 回退到创建顺序。

序号不参与原 configuration_fingerprint 或 cache_key，不进入 facts_snapshot、模型配置解释或提示正文。Worker、数据库失败分支及人工建议决定均不修改该元数据。不能改用普通 updated_at：V222 的 invalid-actor 分支会更新时间；也不能仅优先非 superseded：Python Worker 的晚到错误会重写旧批次状态。无需为本次修复增加迁移、权限或接口字段。

先核验当前身份和 advice.read 的对象权限。无记录仍通过现有 facts loader 检查对象授权、存在状态和纯伙伴排除，不加载模型配置、不生成。存在记录时复用完整事实、授权版本和模型/提示词/规则配置指纹；配置加载后再次检查身份、可见记录和读取权限。permission-version RLS 已隐藏的旧记录不会通过绕过 RLS 的方式暴露。

整个发现及按 ID 读取链路使用只读事务，不调用 request、enqueue 或 Inference。无权或对象不存在返回 404，身份变化 403，参数或不适用对象 422；配置和读取异常正常传播，不能转换成 none。客户端只在明确操作时 POST，并保留返回的 queued/running ID。

离线验证覆盖新增发现接口、原 advice 规则、OpenAPI 和权限目录有效性。新测试在内存 SQLite 上实际执行六维 SELECT、请求排序 SQL 和显式 POST 服务路径，并以只读事务断言及禁止额外生成/入队的 mock 检查控制流；这些测试不等于 PostgreSQL RLS 或真实客户端验收。HTTP 测试覆盖路由先后、参数、状态 envelope 和错误传播；事实、权限、配置指纹、配置加载期间撤权、缓存复活、重试、后续 queued/failed、旧 Worker 晚到状态/V222 时间更新及人工决定均覆盖。具体通过数量见固定源码交付回执。

基准上复现两处旧失败：FDE facts 测试桩缺少 authorization_snapshot 响应，本次仅补测试桩后原断言通过；全量 route coverage 仍因 risks.decide_risk 未登记失败，4fda 基准同样失败，本次不扩大修改风险接口。新增 latest_advice 使用 advice.read，定向权限检查通过。不能据此声称全仓测试通过。

实际 PostgreSQL/API、页面重入、持续轮询及真实生成验证须在主控安排的配套窗口执行。源代码和离线通过不表示当前生产 r4 已包含本接口。

判断来源：Codex；回退原因：复杂的权限与缓存新鲜度联动。公共工作流交接记录 629582b3-796a-47f7-9515-cdf59409222e，未调用业务模型。测试和部署状态由实际执行结果确认。
