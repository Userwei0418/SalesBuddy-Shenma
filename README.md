# 神码 SalesBuddy

神码销售系统、小程序及 Agent 中台的独立维护仓库。当前部署与微信发布状态见 [项目事实源](docs/PROJECT_FACTS.md)，日常维护见 [运维交接](docs/OPERATIONS_HANDOVER.md)。

| 目录 | 内容 |
| --- | --- |
| `frontend/miniprogram` | 神码小程序，AppID `wx2824bdeb58528fd8` |
| `frontend/web` | 当前销售 Web 源码与构建工具 |
| `backend` | API、管理端、Worker、Agent 适配器与接口契约 |
| `database` | 基线和迁移；当前交付迁移头 V449 |
| `agent-platform` | 保留的中台安装配置、源码及许可证 |
| `deployment` | 神码部署配置；已有环境升级必须先备份、核对兼容性 |
| `scripts` | 神码目标隔离和源码打包检查 |

本次交付整理已部署的业务 1.2.14、Mini 1.2.17 及后续兼容迁移。旧 `business-web` 已迁至 `frontend/web`；旧实现可在 Git 历史中追溯。源码来源及逐文件摘要见 [交付清单](docs/DELIVERY_SOURCE_MANIFEST.json)。本次 Git 提交不意味着重新部署服务器或重新上传小程序。

```sh
python3 scripts/check_isolation.py
python3 frontend/scripts/verify_frontend.py
cd frontend/web
npm ci --ignore-scripts
npm run build
```

后端使用 Python 3.12，依赖和开发依赖由 `backend/pyproject.toml`、`backend/uv.lock` 固定。Web 使用 Node.js 22 或更新版本。验证范围和未完成项目见项目事实源及本次源码交付记录。

中台原发行包位于本仓库 Release，完整 Web 编译产物不放入源码。运行凭据、证书私钥、数据库备份和私有账号交接材料不得提交。`docs/INSTALL.md`、`docs/UPGRADE_1.0.9.md` 等早期文档仅作历史参考，不能直接用于当前已有数据库升级。
