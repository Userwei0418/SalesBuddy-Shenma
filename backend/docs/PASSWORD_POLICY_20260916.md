# 密码规则调整（2026-09-16）

账号初始密码、后台重置密码及用户修改密码统一接受 12–128 个字符，不再要求同时包含字母与数字。纯字母、纯数字及符号密码通过同一个 `auth/passwords.py` 编码入口校验；密码内容不会被裁剪或改写。

本次保留账号格式、原密码核验、首次登录强制改密、新旧密码必须不同、限流、权限与版本校验，以及重置后撤销旧会话的现有行为。密码仍使用原参数的 scrypt 和随机盐保存；没有数据库结构变更。

后台登录后的修改密码页、创建账号表单及重置密码表单同步显示“12–128 个字符，不限制字符组合”。`PasswordChange.new_password`、`AccountCreate.temporary_password`、`PasswordReset.temporary_password` 的接口说明同步更新，OpenAPI 由应用重新生成。HTTP 错误仍由既有服务及路由处理。

## 验证

- 离线定向测试 47 项通过：`tests/test_password_auth.py`、`tests/test_auth_configuration.py`、`tests/test_openapi_contract.py`、`tests/test_admin_account_unlock.py`。覆盖密码长度边界、纯字母/数字/符号/中文字符的编码与校验、现有认证配置和账号解锁交互。
- 真实 PostgreSQL 定向测试 13 项通过：`tests/integration/test_native_password_auth.py`、`tests/integration/test_account_login_limits.py`、`tests/integration/test_operations_api.py`。通过既有 `tests/system/run_integration_postgres.py` 的初始化流程应用全部迁移，在非超级用户且不绕过 RLS 的临时角色下执行；覆盖纯字母初始密码登录、纯数字改密、11/129 字符拒绝、新旧密码相同拒绝、纯字母/数字重置、旧会话撤销、首次改密和角色边界。
- 修改的 Python 文件定向 Ruff 通过；两个后台 JavaScript 文件通过 `node --check`；OpenAPI 与实际应用一致。

验证使用独立 `/tmp` Python 环境及只开放私有 Unix socket 的 PostgreSQL 16 临时实例。实例、数据库和角色已清理。隔离测试没有读取项目 `.env` 或凭证。后续运行核验见下文。

定向离线命令（从 `backend/` 执行，使用项目开发依赖环境）：

```bash
python -m pytest tests/test_password_auth.py tests/test_auth_configuration.py tests/test_openapi_contract.py tests/test_admin_account_unlock.py -q -p no:cacheprovider
python scripts/export_openapi.py --check
```

集成测试必须先由隔离库脚手架创建专用测试库和非绕过 RLS 的测试角色，再运行上述三个集成文件；未配置测试库而跳过不算通过。

## 已执行维护

密码规则修复通过 PR #16 合并，后端实际部署源码为 `f8905c3`。2026-09-16 按用户授权对演示公司 13 个、生态渠道公司 12 个可登录账号执行正常后台重置服务，重置后旧会话撤销、首次改密保留、角色及账号状态不变；平台托管身份未当作独立登录账号重置。当前 schema 为 V097，65 个迁移校验通过，无结构迁移。密码及账号分发表单不写入仓库，维护回执保存在私有交付目录。
