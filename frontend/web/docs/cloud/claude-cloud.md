# 用 Claude Code 云端改这个仓库

## 首次连接

1. 打开 https://claude.ai/code ，连接有仓库权限的 GitHub 账号。
2. 授权 Claude GitHub App 访问私有仓库 `shandianT/raccoon-salesbuddy-web`，选择该仓库和 `main` 作为起点。
3. 使用包含 Node.js 22+、Python 3 的云端环境。环境的 Setup script 可填 `bash scripts/cloud-setup.sh`；它按锁文件安装公开依赖、构建和跑本地单元测试，不需要业务账号或 API 密钥。
4. 发起一个范围明确的任务。让 Claude 读根目录 `CLAUDE.md`，在任务分支修改，交 PR 与验证结果，评审后再合并。

GitHub 上传和 Claude 账号授权是两件事；本仓库配置不会替你完成 Claude 账号内的连接。若仓库列表看不到本仓库，核对所连 GitHub 账号和 Claude App 的仓库访问范围。

官方操作依据（2026-09-20核对）：[云端 GitHub 访问与运行方式](https://code.claude.com/docs/en/claude-code-on-the-web)、[CLAUDE.md 项目说明](https://code.claude.com/docs/en/memory)。账号是否具备云端功能以 Claude 当前界面为准。

## 可直接发给 Claude 的第一条任务

> 先读 CLAUDE.md、docs/cloud/design-standard.md 和 docs/cloud/review-handoff.md。以当前 Web 为基线，为【填写页面】优化交互和布局，先简要说明具体问题与改法，再在当前任务分支实施。保持小程序 1.0.6 的字段、接口、权限、状态和审批逻辑；复用现有组件。使用合成示例，覆盖加载、空、失败和无权限状态，检查1366×768、1024×600及窄屏。执行 npm run verify，并尽量跑该页面的隔离浏览器测试；交回改动清单、前后对比、实际结果和未验证项，创建PR供评审，不自动合入main。

推荐第一轮分别做总览、客户图表与列表、创建任务三个小任务。它们可能共同修改样式；分别出分支，由一个集成人处理共同组件和冲突，先评审再合并。

## 运行与测试的区别

| 要做什么 | 命令与依赖 |
|---|---|
| 首次安装 | `npm ci --ignore-scripts`，需要 npm 与 GitHub 公开包下载访问 |
| 构建与单元检查 | `npm run verify`，Node.js22+和Python3，先完成依赖安装 |
| 部门组件库候选版 | 从 `codex/department-design-20260920` 开始，读 `docs/cloud/department-design-20260920.md`；`npm run preview:department` 默认5188 |
| 合成界面预览 | `npm run preview`，默认5187；真实后端与快捷登录关闭 |
| 页面入口 | `http://127.0.0.1:5187/?mode=preview#/pages/index/index` |
| 规范示例册 | `http://127.0.0.1:5187/design-system/index.html` |
| 浏览器回归 | 另备Playwright和系统Chrome；按所选测试的import要求设置 `PLAYWRIGHT_MODULE`，只跑有关的 `*.browser.mjs` |
| 真实业务联调 | 另行获得测试环境、账号与授权，设置 `SALES_WEB_API_TARGET`；与合成预览分开验收 |

本地地址仅在运行服务的环境可用，不是公开部署地址。云端如何预览端口，取决于所用云端环境；不能把本机127.0.0.1链接当成同事的在线地址。

`crm-preview.browser.mjs`、`crm-quarter.browser.mjs`、`mature-components.browser.mjs` 的 CRM 部分依赖本机私有数据或5196服务，不纳入干净云端的默认验证。CRM准备脚本还可能需要openpyxl。普通页面开发、合成测试不需要它们。

仓库内有构建与单元检查的 GitHub Actions，无自动部署。共享业务源完整性由构建检查；通过不等于真实业务、Agent或跨端验收。
