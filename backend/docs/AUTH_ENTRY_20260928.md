# 公司入口与平台两步认证接口（2026-09-28）

本轮仅本地实现与验证，依赖 V189；不代表生产已迁移或发布。

## 公司登录

- 既有账号、密码、岗位和公司内唯一性规则保持不变。省略公司码时仍只允许唯一匹配。
- 已验证的密码登录、刷新、`/auth/me` 和 `/console/auth/me` 返回的 actor 追加可选 `company_code`、`company_name`，从该身份所属公司读取，不采信客户端传入名称或公司选择 Header。
- 失败提示统一为账号、密码或公司入口不正确，不列出该账号存在的公司。
- `GET /api/v1/auth/company-entry?company_code=...` 精确查询启用公司，返回且只返回 `company_code`、`company_name`。未知、无效、未启用、暂停、关闭公司统一 404；无列表、前缀搜索或成员信息。
- 查询独立使用现有数据库限流：每网络 50 次／15 分钟，429 携带实际 `Retry-After`。此计数不占用成员密码登录计数。查询和所有认证回执禁止缓存。

## 平台两步认证

1. `POST /api/v1/platform/auth/start`：`account_code`、`password`。验证密码后签发仅供 MFA 的五分钟 Cookie，返回 `operator` 和实际 `expires_in`。此会话无公司管理权限。
2. 已绑定者向 `POST /api/v1/platform/auth/mfa/login` 提交六位 `otp`。未绑定者沿用 `/auth/mfa/enroll` 取得 `secret`、`uri`、本地生成的 `qr_svg`，然后 `/auth/mfa/confirm` 提交验证码。
3. 登录完成或首次绑定确认均在数据库事务中消费验证码、撤销旧 Cookie 对应会话并建立三十分钟新会话；响应更新 Cookie。错误、过期、重复验证码不授予平台权限。
4. 原 `/auth/login` 账号＋密码＋验证码一步接口保留兼容。敏感操作 `/auth/verify` 仅用于已经通过 MFA 的会话，不能绕过两步登录或免除旋转。
5. `operator` 追加 `expires_at`、`expires_in`；Cookie Max-Age 来自实际数据库有效期。公司 Cookie 和平台 Cookie 相互独立。

验证码来自本地 TOTP 验证器，默认三十秒刷新；不是短信。二维码使用锁定依赖 Segno 本地生成，不调用二维码服务。动态二维码与密钥只在当前绑定响应返回，不进入审计正文。

## 公司入口交付

- `GET /api/v1/platform/tenants/{workspace_id}/entry` 要求已通过 MFA 的平台身份。返回公司名称、公司码、固定 `/admin?company=...` 路径、独立 `/company-activate?company=...` 路径及入口二维码字段。
- `PUBLIC_WEB_ORIGIN` 应配置为可信 HTTPS origin，可含端口但不含路径、查询、凭据或片段。无有效配置时只返回相对路径，`web_qr_svg=null`，并提示 `PUBLIC_WEB_ORIGIN_NOT_CONFIGURED`。
- 仅 `development`、`local`、`test` 环境允许当前精确回环地址生成本地二维码；生产不采信请求 Host 或转发 Header 选择二维码目标。
- 小程序返回固定 `pages/login/index` 与独立 `company=...` query。公司码最多三十字符且仅含 ASCII 字母、数字、点、下划线、连字符时提供 `c=...` scene；超长、非 ASCII 或含查询分隔符的历史码暂为空。`mini_program_qr_available=false` 明确表示尚未生成官方小程序码；普通 Web 二维码不是小程序码。

## 验证

- `tests/test_local_auth_qr.py`：本地 SVG、可信 origin、防 URL 注入。
- `tests/integration/test_company_auth_entry.py`：公司入口信息最小化、状态隔离、限流、登录/刷新/me 规范公司名称、同账号跨公司精确登录。
- `tests/integration/test_platform_two_step_auth.py`：受限会话、首次绑定、错误/过期/重放、旧会话撤销、公司与平台 Cookie 隔离。
- 既有平台 MFA、平台控制、公司和小程序密码登录测试一并回归；只在一次性隔离 PostgreSQL 库中运行。
