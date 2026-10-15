# SalesBuddy 神码版

独立维护神码销售系统和小浣熊 Agent 中台。当前源码候选采用内部完整 1.2.1 后端、Web 和小程序能力，并保留神码账号、数据、统一模型 Key 与飞书绑定。数据库目标 V399。实际服务器是否切换以[整套升级回执](docs/WHOLE_RELEASE_1.2.1_SHENMA.md)为准；源码更新不代表已经上线。

配套使用方式见[神码 1.2.1 使用指南](docs/USER_GUIDE_1.2.1_SHENMA.md)，测试实测与保留失败见[后端回归证据](docs/WHOLE_121_BACKEND_REGRESSION.md)。中台继续使用客户原来的独立部署，本次销售发布包不覆盖中台运行数据或配置。

- frontend：完整新版 Web 与客户小程序，AppID `wx2824bdeb58528fd8`。
- backend：销售 API、管理端、Worker、受控中台适配器。
- database：结构基线、迁移桥接及验证，不含业务数据。
- agent-platform：既有中台安装配置、API 源码、品牌资源和许可证，独立发布。
- deployment、scripts：服务器升级、隔离与交付检查。
- docs：用户指南、计划、状态和来源证据。

已确认销售入口 `https://salesbuddy.shenzhoukuntai.com:28899`；中台入口 `https://ops-salesbuddy.shenzhoukuntai.com:18899`。客户提供的可信 HTTPS 证书已安装；续签及自动下发由对方运维确认。

中台完整安装包存放在本公开仓库 Release 中，Web 编译产物不入 Git。下载后以包内 SHA256SUMS 校验；对源码的后续修改需要生成新的发行包和校验清单，不能冒用原版哈希。

见 [首次安装指引](docs/INSTALL.md)、[计划与状态](docs/PLAN.md)、[隔离边界](AGENTS.md)、[来源清单](docs/SOURCE_MANIFEST.json)。

模型接入与后续替换见 [模型配置](docs/MODELS.md)。

历史 1.0.9 同步及验收见 [1.0.9 神码同步记录](docs/SYNC_1.0.9_20260924.md)。源码版本、服务器运行版本、中台发布及微信预览分别记录；不以源码更新代替部署验收。

已有服务器的本次升级使用 [1.2.1 整套升级说明](docs/WHOLE_RELEASE_1.2.1_SHENMA.md)，不要重跑首次安装脚本。旧 [1.0.9 升级说明](docs/UPGRADE_1.0.9.md)仅供追溯。
