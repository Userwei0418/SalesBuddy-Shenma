# 神码业务后端

Python 3.12；依赖由 `pyproject.toml`、`uv.lock` 固定。业务代码在 `src/sales_backend`，接口契约在 `openapi/openapi.yaml`。运行配置由神码服务器受限配置提供，不从本仓库生成生产密钥或账号。

当前运行版本、迁移状态与Agent绑定见 [项目事实源](../docs/PROJECT_FACTS.md)。`agent_platform`仅包含通用提示词/契约模板，不携带原环境发布凭据或发布记录。当前神码报告提示词版本4见 `../deployment/work-report-agent/system_prompt.txt`。

`deploy`中保留了通用契约验证模块；实际神码部署以根目录`deployment`和受限运维手册为准。不要把其他环境历史部署脚本用于神码。
