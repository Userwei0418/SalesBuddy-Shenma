# SalesBuddy PC Web 1.2.0 · 本地后端联调

本目录是唯一维护仓库 `Userwei0418/Raccoon-SalesBuddy` 的 Web 源码，正在接入 PC Web 1.2.0 候选功能。当前工作包括工作台、跟进记录、工作报告、知识库及共享后端的配套接口。包内历史测试报告仅描述当时的合成数据和模拟后端结果；当前仓库的构建、真实本地 API、权限、统计口径与浏览器验收要分别记录，不能沿用交付包的“已通过”结论。

当前目录、分支、运行版本、镜像与未完成项统一登记在 [项目事实源](../../docs/PROJECT_FACTS.md)，本页维护使用方法。`VERSION.json` 和来源包旧报告不代表当前运行状态。

以下 Web 1.2.0 操作方法适用于事实源登记的日常开发工作区及其未提交增量；本轮 1.1.2 稳定发布分支没有包含这些新功能或启动脚本。不要在稳定发布工作区照此启动尚未合入的开发环境。

## 选择本地数据环境

当前双租户业务验收按 [Web 租户镜像流程](../../docs/WEB120_TENANT_MIRROR.md)选择商汤或演示公司镜像，使用 `tools/local/web120_snapshot.py` 管理；先核对已有服务，两个镜像共用 18800，不能同时启动。镜像账号沿用各自正常认证，不使用下面的合成账号指南。

下面的 `web120_profile.py` 仅用于独立合成测试环境，不能用它替代镜像、猜测真实公司账号或覆盖已启动的镜像服务。服务当前属于哪个 profile、库到哪个迁移版本，以事实源最近的核对和当前只读检查为准。

## 合成环境联调启动

要求 Node.js 22 或更新版本、Python 3，以及仓库的[本地开发环境](../../docs/LOCAL_DEVELOPMENT.md)。此合成测试路径使用独立的 `web120` profile，和原有 `salesbuddy_local` 数据库隔离。先在仓库根目录准备并启动配套 API、数据库、普通 Worker：

```sh
cd "/Users/zhouwei5/Developer/Raccoon SalesBuddy"
backend/.venv/bin/python tools/local/web120_profile.py up
backend/.venv/bin/python tools/local/web120_profile.py check
```

然后在另一终端构建并启动 Web：

```sh
cd "/Users/zhouwei5/Developer/Raccoon SalesBuddy/frontend/web"
npm ci --ignore-scripts
npm run dev:local
```

打开 [本地 Web 登录页](http://127.0.0.1:5188/?mode=live#/pages/login/index)。已完成当前源码构建时，可执行 `npm run start:local`。

| 组件 | 固定地址 | 用途 |
| --- | --- | --- |
| Web | `http://127.0.0.1:5188` | 当前工作区构建的 Web 页面 |
| API | `http://127.0.0.1:18800/api/v1` | 本地后端与本地合成数据 |
| 运营后台 | `http://127.0.0.1:18800/admin` | 本地运营与审批验收 |

`start:local` 直接建立同源 `/api/v1` 代理，只监听 `127.0.0.1:5188`，固定转发到本地 `18800`。它不读取 `connection.config.json`，也不使用父进程的 `HOST`、`PORT` 或 `SALES_WEB_API_TARGET`，本地 API 不可用时不会切换到线上。端口已占用会报错退出；先核对原进程用途再处理，不会自动改端口或停止其他服务。`Ctrl+C` 停止 Web；后端由 `tools/local/web120_profile.py` 管理。停止后端使用 `backend/.venv/bin/python tools/local/web120_profile.py down`，该命令只停止本 profile 的 API/Worker，保留隔离数据库。

本地模型和飞书尚未配置时，API readiness 可能显示 degraded；以本地工具的 `verify` 结果区分“数据库/认证可用”和“真实 AI 未配置”。页面可打开或合成演示通过，都不代表 Agent 质量、飞书通知或生产验收通过。

## 登录与可选的一键入口

本地使用正常账号密码认证。`backend/.venv/bin/python tools/local/web120_profile.py paths` 会给出本 profile 的私有账号说明位置；本地合成账号不适用于生产。不要把账号说明、密码、会话或 Token 写进源码、报告或交付包。

现有 `local-login.mjs` 可在本机登录页提供“一键进入 · 一线销售”，它仍向本地后端执行正常密码登录，不绕过身份、权限或首次改密流程。新启动器预留私有配置 `frontend/web/.runtime/local-backend-login.json`，未配置时使用正常登录；本轮没有自动创建账号或读取凭据。

配置由本机操作者准备，字段如下（占位值需替换，配置本身不含密码）：

```json
{
  "apiTarget": "http://127.0.0.1:18800/api/v1",
  "accountCode": "<本地合成一线销售账号>",
  "role": "sales",
  "guidePath": "/绝对路径/本地私有一线销售账号说明.md"
}
```

`guidePath` 指向的私有 Markdown 表格必须含“企业账号”“密码”“已分配角色”三列；目标账号只能出现一次，角色文字应含“一线销售”。现有本地工具生成的 `TEST_ACCOUNTS.md` 是“公司码 / 账号 / 角色”格式，密码位于正文，**不能直接作为这个兼容表格**。可另建仅含该合成账号的兼容说明，并保存在私有运行目录；人工改密后应同步该私有说明。不得将线上账号说明接入本地启动器。

配置的 `apiTarget` 必须与固定本地 API 完全相同，角色必须为 `sales`，否则启动器拒绝启动。快捷登录只接受本机同源请求；点击前仍需同意隐私与数据使用说明。多角色验收使用各自正常授权的本地账号，当前快捷入口不扩展为管理员免登录。

## 合成界面演示

只看界面时可使用独立演示入口，数据来自内置合成示例，不调用本地或线上业务 API：

```sh
npm run build
PORT=5189 node scripts/start_department_preview.mjs
```

打开 [合成界面演示](http://127.0.0.1:5189/?mode=preview#/pages/index/index)。演示身份切换仅用于界面检查，不能证明真实后端权限。

## 构建和验收

```sh
npm run verify
```

`verify` 构建当前源码并运行 Web 单元/契约检查；真实本地后端的登录、范围权限、跟进数据、统计口径和页面交互还需在上面的 `5188` 入口验收。修改源码后重新构建；后端运行源码有变动时，在仓库根目录按所选合成/镜像 profile 的工具重新执行 `up`，避免浏览器连接旧后端快照。本地通过后才准备线上同步和飞书文档的确认变更记录。

## 参考资料与历史边界

- [PC Web 1.2.0 改造说明](</Users/zhouwei5/Developer/Raccoon SalesBuddy/frontend/web/docs/cloud/pc-redesign/改造说明.md>)：候选包模块与接口说明，后端实现范围以本轮确认及当前契约为准。
- [候选包验证记录](</Users/zhouwei5/Developer/Raccoon SalesBuddy/frontend/web/docs/cloud/pc-redesign/验证记录.md>)：历史合成数据/模拟后端记录，不代表当前工作区已验收。
- [知识库目录格式](</Users/zhouwei5/Developer/Raccoon SalesBuddy/frontend/web/knowledge/README.md>)：当前静态目录的维护方法；企业内容与正式知识库接口另行验收。
- [本地开发与验收](../../docs/LOCAL_DEVELOPMENT.md)：API、数据库、Worker、本地账号与运行指纹。
- [1.1.x 历史验证记录](docs/cloud/upgrade-110/验证记录.md)与[中文输入修复说明](中文输入修复及使用说明.md)：保留追溯，不覆盖本轮状态。

`source/miniprogram/` 为共享业务快照，`department-ui/` 为 React 展示层，`runtime.js` / `browser-platform.js` 为浏览器适配，`dist/` 为构建产物。普通 `npm start`、`node server.mjs` 和旧启动命令会按各自配置选择上游，当前本地后端验收统一使用 `npm run dev:local` / `npm run start:local`。
