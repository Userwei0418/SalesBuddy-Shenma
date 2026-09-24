# SalesBuddy 神码版

独立维护神码销售系统和小浣熊 Agent 中台。销售系统最初由商汤 1.0.8 派生，本次同步到内部 1.0.9 及后台性能补丁，来源提交 `be421b12b9dce38aa940e8b546dde1796c3bdef5`，数据库迁移头 V151；中台基于用户提供的 20260923 定制 Dify 1.17.0 安装包。

| 目录 | 内容 |
|---|---|
| frontend | 客户小程序，AppID `wx2824bdeb58528fd8` |
| backend | 销售 API、管理端、Worker、受控中台适配器 |
| database | 结构基线、系统种子及迁移，不含业务数据 |
| agent-platform | 中台安装配置、API 源码、CLI、品牌资源和许可证 |
| deployment | 神码服务器部署与初始化配置 |
| scripts | 隔离与交付检查 |
| docs | 计划、状态和来源证据 |

已确认销售入口 `https://salesbuddy.shenzhoukuntai.com:28899`；中台入口 `https://ops-salesbuddy.shenzhoukuntai.com:18899`。客户提供的可信 HTTPS 证书已安装；续签及自动下发由对方运维确认。

中台完整安装包存放在本公开仓库 Release 中，Web 编译产物不入 Git。下载后以包内 SHA256SUMS 校验；对源码的后续修改需要生成新的发行包和校验清单，不能冒用原版哈希。

见 [首次安装指引](docs/INSTALL.md)、[计划与状态](docs/PLAN.md)、[隔离边界](AGENTS.md)、[来源清单](docs/SOURCE_MANIFEST.json)。

模型接入与后续替换见 [模型配置](docs/MODELS.md)。

本轮同步及验收见 [1.0.9 神码同步记录](docs/SYNC_1.0.9_20260924.md)。源码版本、服务器运行版本、中台发布及微信预览分别记录；不以源码更新代替部署验收。

已有服务器升级使用 [升级说明](docs/UPGRADE_1.0.9.md)，不要重跑首次安装脚本。
