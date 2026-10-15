# 后端上线准备审查（2026-09-27）

## 范围与结论

以 `5d55f49` 的后端接口契约对照当前工作区，检查旧客户端兼容性、身份复核、平台管理启动边界和已有公司迁移默认值。本轮没有修改业务实现、权限规则或数据库迁移；新增平台运行配置边界测试。

- 既有 OpenAPI 路径和方法未删除；既有参数的类型、默认值、约束及枚举未改变；既有请求体引用和 schema 属性未删除或收紧，也未新增必填项。新增目录、默认团队、负责人 ID、地图状态等字段为扩展。静态契约检查不代替旧小程序对新 API 的 HTTP 验收。
- `PLATFORM_DATABASE_URL` 未配置时，API 不创建平台连接池、不读取平台 MFA 密钥；原公司 API 的启动保持可用。平台管理接口返回 `503 / PLATFORM_NOT_CONFIGURED`。这不代表平台管理能力已部署。
- 平台数据库已配置但连接、受限角色验证或 MFA 配置失败时，启动失败并关闭两套资源，不会忽略错误启动一个缺少保护的平台管理入口。
- V156 对迁移前已存在的公司回填全部模块启用；新建公司才使用 AI、飞书和历史导入默认关闭的产品配置。V173 为已有公司回填共享 AI 允许；账号、存储、调用和 HTTP 额度默认未设上限。迁移默认值仍须以 V152 既有两公司升级隔离验收为准，不能用“最新 schema 后建测试公司，再手动启用模块”的测试代替。
- 原有 `reconcile_runtime_grants()` 在每次受管迁移结尾执行，但不能据此省略生产 API、普通 Worker、飞书 Worker、平台账号的角色核对；新增平台池和 MFA 必须另行配置。生产实际配置和上线状态由发布流程现场核实。

## 本轮验证

- `tests/test_platform_optional_runtime.py` 新增 5 项：未配平台池仍可启动、平台接口 503、配置后连接/权限/MFA 三类失败拒绝启动、独立平台池正常启动关闭。
- 上述测试与 `tests/test_auth_configuration.py` 合计 **28 项通过**。
- 上传接收、长耗时下载身份复核、HTTP 限流、错误回显脱敏、配置就绪和模型接口定向测试 **124 项通过**：`test_upload_admission.py`、`test_live_identity_download.py`、`test_http_limits.py`、`test_private_request_validation.py`、`test_configuration_readiness.py`、`test_model_api.py`。
- 新增测试的 Ruff 检查通过。两组测试范围不重叠；未重新累计此前跨模块、任务和地图验证数量。

测试使用合成配置和受控替身，不读取真实密钥、不启动模型请求。数据库专项已在隔离 PostgreSQL 16 中完成固定生产 V152 基线到 V186 的合成历史数据升级，验证 17 张历史表旧字段保全、两公司既有权限和配置、34 份增量、重复迁移及受限身份。详见 `database/docs/V152_UPGRADE_REVIEW_20260927.md`。它不代表生产接管、生产性能或真实集成已通过；旧小程序 HTTP 与完整 Linux 检查由发布负责人统一记录。

```sh
python -m pytest tests/test_platform_optional_runtime.py tests/test_auth_configuration.py -q
python -m pytest tests/test_upload_admission.py tests/test_live_identity_download.py tests/test_http_limits.py tests/test_private_request_validation.py tests/test_configuration_readiness.py tests/test_model_api.py -q
python -m ruff check tests/test_platform_optional_runtime.py
```
