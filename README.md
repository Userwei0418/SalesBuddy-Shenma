# SalesBuddy 神码版

独立维护神码销售系统和小浣熊 Agent 中台。销售系统派生自商汤 1.0.8，源码来源提交 `3e5b904645fdcafda46e177eedae7f9e2dd1d957`（业务发行 `7a414b4`，数据库 V125）；中台基于用户提供的 20260923 定制 Dify 1.17.0 安装包。

| 目录 | 内容 |
|---|---|
| frontend | 客户小程序，AppID 待提供 |
| backend | 销售 API、管理端、Worker、受控中台适配器 |
| database | 结构基线、系统种子及迁移，不含业务数据 |
| agent-platform | 中台安装配置、API 源码、CLI、品牌资源和许可证 |
| deployment | 神码服务器部署与初始化配置 |
| scripts | 隔离与交付检查 |
| docs | 计划、状态和来源证据 |

已确认销售入口 `https://salesbuddy.shenzhoukuntai.com:28899`；中台入口 `https://ops-salesbuddy.shenzhoukuntai.com:18899`。证书采用 DNS-01 自动验证，待对方运维完成 DNS 验证与证书配置；证书和实际业务能力按验收记录判断。

中台完整安装包存放在本私有仓库 Release 中，Web 编译产物不入 Git。下载后以包内 SHA256SUMS 校验；对源码的后续修改需要生成新的发行包和校验清单，不能冒用原版哈希。

见 [首次安装指引](docs/INSTALL.md)、[计划与状态](docs/PLAN.md)、[隔离边界](AGENTS.md)、[来源清单](docs/SOURCE_MANIFEST.json)。

模型接入与后续替换见 [模型配置](docs/MODELS.md)。

2026-09-24：销售运行 7c7a376 / V125，中台已发布 12 个销售 Agent，4 项核心能力限管理员试点；尚待可信证书、客户 AppID 和其余业务验收。详见 [部署记录](docs/DEPLOYMENT.md)、[核心能力验收](docs/CORE_AGENT_ACCEPTANCE.md)、[辅导建议验收](docs/COACHING_ACCEPTANCE.md)。
