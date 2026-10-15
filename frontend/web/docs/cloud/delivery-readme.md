# SalesBuddy PC Web 1.2.0 · PC 视觉 2.0 本地交付

> 本文是历史交付包说明。当前源码、运行入口及业务规则以仓库 `docs/PROJECT_FACTS.md` 为准；随包 `openapi-1.1.0.yaml` 仅为历史接口快照。2026-10-08 已确认客户按名称检索匹配，不要求外部编号；旧编号字段不得被解释为申请或审批前提。

本包只更新 Web 销售工作台：PC 为主要工作平台（侧栏一级模块 ＋ 子菜单、浅蓝主色、字号下调），新增工作台、跟进记录、工作报告、知识库四个模块。业务基线为小程序 1.1.0，`source/miniprogram/` 冻结校验与 `utils/apiClient.js` 未改，没有新增或修改后端接口；小程序、管理后台、服务端均未改动，没有上传或部署。

## 启动已构建页面

需要 Node.js 22 或更新版本。包内已有 dist，可直接启动，无需先安装依赖。

```sh
PORT=5188 node server.mjs
```

Chrome 打开 `http://127.0.0.1:5188/?mode=live#/pages/login/index`，macOS 也可双击「启动小浣熊SalesBuddy.command」。企业登录继续使用同源 `/api/v1`，本地服务代理到 `connection.config.json` 中的中台地址；包内没有账号凭据，需使用实际授权账号。

## 隔离演示

```sh
PORT=5189 node scripts/start_department_preview.mjs
```

打开 `http://127.0.0.1:5189/?mode=preview#/pages/index/index`，或双击「启动演示-合成数据.command」。此服务关闭真实中台代理，只用合成数据；顶栏「演示身份」可切换一线销售、销售主管、销售总经理、FDE、FDE 主管。

## 从源码重新构建

另外需要 Python 3。依赖版本全部沿用锁文件。

```sh
npm ci --ignore-scripts
npm run verify
```

修改后必须重新构建，不能只替换 dist。浏览器验证使用 Chrome 与外部 Playwright：

```sh
PLAYWRIGHT_MODULE=/绝对路径/playwright/index.mjs node --test tests/upgrade110.browser.mjs tests/customer-create-inline.browser.mjs tests/account-session.browser.mjs tests/v106-visits.browser.mjs
PLAYWRIGHT_MODULE=/绝对路径/playwright/index.mjs node tests/upgrade110-smoke.browser.mjs
```

## 交接资料

- 改造说明（模块、接口对照、需求收集表 28 条对照、后端建议、已知限制）：`docs/cloud/pc-redesign/改造说明.md`
- 验证记录：`docs/cloud/pc-redesign/验证记录.md`；截图：`docs/cloud/pc-redesign/evidence/`
- 知识库目录维护：`knowledge/README.md`
- 1.1.x 历史资料：`docs/cloud/upgrade-110/`、随包 OpenAPI `docs/cloud/openapi-1.1.0.yaml`

`source/miniprogram/` 为业务基线，`department-ui/` 为 React 展示适配（PC 新模块与设计变量在 `department-ui/pc/`），`scripts/web_*` 为 Web 运行适配，`runtime.js` 与 `browser-platform.js` 为浏览器宿主，`dist/` 为构建产物。不得直接修改冻结基线绕过构建校验。

未验证真实身份权限、真实数据量下的统计口径、Agent 质量、实际业务写入和跨端读回；模拟验证不代表生产验收。
