# 神码 SalesBuddy Web

现行Web维护目录。运行 `npm ci --ignore-scripts`、`npm run build`，输出到 `dist/`；测试运行 `npm test`。生产由神码Nginx提供同源API，`connection.config.json`只用于本地代理，不属于线上静态产物。

固定导入的共享小程序源码位于 `source/miniprogram`，配套哈希在 `source/manifest.json`，构建会校验。不要把当前Mini工作目录覆盖该固定快照。构建时自动生成Web提交身份，未提交的目录会如实标记dirty。

当前环境及验收边界见 [项目事实源](../../docs/PROJECT_FACTS.md)。
