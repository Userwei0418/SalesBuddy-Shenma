# 神码业务 Web

此目录是神码独立仓库的业务 Web，与小程序共用现有销售账号和 `/api/v1/auth/*`。线上入口：`https://salesbuddy.shenzhoukuntai.com:28899/workspace/`；运营后台为 `/admin`。

来源：用户交付的《神舟数码后端及Agent交接包-1.0.9-20260925》中的 Web 工程，源前端提交 `01ee1e0`。`source/manifest.json` 与 `source/miniprogram/` 保留交接校验；浏览器适配在 `scripts/`、`department-ui/` 和 `web-pages/` 中维护。相邻商汤仓库不参与本项目构建或部署。

## 开发与验收

```sh
npm ci --ignore-scripts
npm run verify
npm run dev
```

本地 Web 默认代理神码域名。真实模式的业务写入会进入当前登录账号所属的神码公司；演示请用 `npm run preview` 和显式 `?mode=preview`。完整联调口径见 [神码周报接口](../docs/神码周报后端接口与Web联调.md)。

周报浏览器验收使用完全合成 API，通过 `PLAYWRIGHT_MODULE` 指向已安装的 Playwright，再运行 `npm run test:weekly-browser`。不会访问客户或商汤网络。测试覆盖共享登录、生成、保存、重开、冲突核对、重试、取消和窄屏。

生产采用构建静态资源，由现有 API 提供 `/workspace/`，无需 Node.js 常驻进程。提交后重新构建并用根目录打包器 `--include-business-web` 生成部署包；`dist`、`node_modules` 和测试截图不进 Git。`build.json` 记录独立仓库提交版本。

## 来源工程说明

下面保留交接时的原说明作为开发参考；其中历史状态、环境和待办不代表神码当前部署状态。

---

---
status: 1.0.9前端同步已验证，正式联调未实施
updated: 2026-09-25
执行方: Codex
---

# 神舟数码 Web UI

当前包已按完整小程序 **1.0.9** 同步共享业务核心和 Web 交互，保留已确认的神舟定制。仅修改前端 Demo，后端、中台、Agent 均未修改。

主要更新：新版权限与数据范围、多人独立待办、客户认领筛选、活跃地图与完整资产分离、待评估客户、人均跟进排名与成员明细、历史跟进与季度实绩、跟进归档后续及失败恢复。Web 周报按周展示，采用近14天上传记录生成本地规则草稿，尚未接入正式周报 Agent。

完整范围、字段说明、接口依赖和未实测项目见[修改交接文档](修改交接文档.md)。证据见[本轮验收记录](验收/1.0.9同步-20260925/验收记录.md)。

## 打开 Demo

已包含预构建 dist。需要 Node.js 22 或以上，在本目录执行：

```sh
node scripts/start_shenzhou_preview.mjs
```

打开[本地预览](http://127.0.0.1:5191/?mode=preview&ui=109#/pages/workbench/index)。启动器仅监听本机5191、关闭真实后端代理。新增的跟进类型和关联业务仅保存本浏览器演示草稿。

修改源码后，使用现有锁文件安装和构建：

```sh
npm ci --ignore-scripts --no-audit --no-fund --cache .cache/npm
npm run build
npm test
```

Python 3 用于构建。浏览器回归还需 Playwright 和本机 Chrome；具体命令见验收记录。

## 基线与代码

- 共享业务来源：用户确认的完整小程序1.0.9工作副本，源修订 `de08496ec861716a4d9ce026534abfee5d0ec099`。该修订表示来源，不是本轮Web提交号。
- 259个受清单保护的共享文件，与来源工作副本逐字节一致；Web config保持同源 `/api/v1` 代理配置。
- 构建包含27页（26个小程序业务页＋Web周报）、14个组件。
- Web展示工程及npm包版本保留0.6.12；它表示既有展示架构，不表示业务仍为1.0.6。React、Ant Design及公司组件库未升级。
- `source/miniprogram`：业务权限、状态与处理事件；`department-ui`：Web展示与交互；`scripts/web_*_layout.py`：原模板适配。
- `preview-api.js`、`preview-workflow.js`：浏览器合成演示；`web-pages/weekly-report.js`：Web周报读取。
- `dist`：构建产物，禁止手改。账号、运行时私密配置、缓存、node_modules与真实CRM文件不随包交付。

本轮完整构建、134项默认测试、23项隔离浏览器用例通过。真实账号授权、正式API写入、审批、录音设备和Agent效果尚未联调；历史docs中的旧版本验收不代表本轮已验收。当前交付以本文件、VERSION.json和本轮交接为准。
