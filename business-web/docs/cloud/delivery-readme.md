# SalesBuddy Web · 交付说明

本包包含当前 Web 候选版的完整开发源码、设计规范、测试，以及已经构建好的网页。业务基线为小程序 1.0.6；具体代码快照见 `DELIVERY.json`。本次界面优化保持原业务流程和权限。

## 打开正式账号登录

需要 Node.js 22 或更新版本。解压后在本目录打开终端，运行：

```sh
PORT=5188 node server.mjs
```

打开 http://127.0.0.1:5188/?mode=live#/pages/login/index ，使用已开通的企业账号登录。macOS 也可双击 `启动小浣熊SalesBuddy.command`。

这条命令直接使用包内 `dist/`，**不需要安装 node_modules，不需要 Python 或重新构建**。本包保留已配置的企业后端接口，但不包含任何个人账号、密码或已登录状态。终端按 Ctrl+C 停止当前服务。

Windows PowerShell 先执行 `$env:PORT='5188'`，再运行 `node server.mjs`。

如果 5188 已被其他版本占用，请改用新端口，例如：

```sh
PORT=5190 node server.mjs
```

然后将浏览器地址中的端口改为 5190。

## 可选：无需账号的界面评审

需要仅评审界面时，另开终端启动独立的示例服务：

```sh
PORT=5190 node scripts/start_department_preview.mjs
```

打开 http://127.0.0.1:5190/?mode=preview#/pages/index/index 。示例使用合成数据，支持角色切换，不需要企业密码。此服务主动关闭后端连接，不能用于正式账号登录。

## 后端连接

`connection.config.json` 已保留本次 Web 使用的企业接口配置。通常无需改动；如果后端团队提供了新的环境，将 `apiTarget` 更新为已授权的 API 根地址，也可以用环境变量 `SALES_WEB_API_TARGET` 覆盖。地址里不要填入账号、密码或令牌，改完配置后重启 Node 服务。

真实登录需要网络能访问企业后端，并且账号已开通。页面连接正常只代表后端响应，账号权限和业务写入以实际接口结果为准。

## 继续修改

开发需要 Node.js 22+、Python 3，首次安装需要访问 npm 与公开组件包地址：

```sh
npm ci --ignore-scripts
npm run verify
```

`verify` 包含完整构建和默认单元／契约检查。仅更新页面可以运行 `npm run build`，随后刷新浏览器。**不能只执行 `python3 scripts/build.py`，因为它不构建 React 组件资源。** 浏览器检查另外需要 Playwright 和浏览器，见 `docs/cloud/claude-cloud.md`。

| 位置 | 内容 |
|---|---|
| `dist/` | 已构建的网页，可直接由 Node 服务运行 |
| `department-ui/` | 总览、客户、任务、创建任务的 React 展示层 |
| 根目录 JS/CSS、`scripts/web_*_layout.py` | Web 运行、交互和布局适配 |
| `source/miniprogram/`、`source/manifest.json` | 小程序业务基线及完整性校验 |
| `docs/cloud/design-standard.md` | 当前设计规范 |
| `docs/cloud/review-handoff.md`、`docs/cloud/templates/` | 共评方式与交接模板 |
| `tests/` | 单元、契约与浏览器检查 |
| `DELIVERY.json`、`CHECKSUMS.sha256` | 快照身份与文件校验 |

按功能开分支交回修改，不多人覆盖同一目录。界面调整优先改展示层，客户认领审批、任务确认／驳回、拜访质检归档及权限不自行改写。

## 再生成交付包

完成修改并运行完整构建与检查后，执行：

```sh
python3 scripts/package_department.py --output ../交付输出 --title SalesBuddy-Web-交付版本名 --create
```

输出目录中生成 ZIP、SHA256 和打包验证记录。省略 `--create` 只估算包大小。工具从 Git 已跟踪文件取源码；新文件先加入 Git，或用 `--extra 相对路径` 明确纳入。解压包没有 `.git` 时沿用 `DELIVERY.json` 内的源码清单，同样支持 `--extra`。工具不替你重新构建，也不根据旧报告声称本次业务测试通过。

## 包内边界

本包不包含 node_modules、Git 历史、本机缓存、真实 CRM 数据、个人登录凭据、历史截图和旧 ZIP。CRM 专项数据导入工具不在本包范围。源码文件保留第三方许可；构建依赖已打入网页，并附带许可说明。交付脚本只验证包完整性、独立启动及本地模拟代理，不使用正式账号、不读取真实客户。历史文档中的验收结果仍属于各自版本，不自动累计为本次正式业务验收。
