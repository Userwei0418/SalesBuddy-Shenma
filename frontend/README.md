# 神码前端

小程序工程为本目录的 `project.config.json` 与 `miniprogram/`，AppID以本目录工程配置为准，API为神码HTTPS服务。当前Web入口为 `web/`，执行 `npm ci --ignore-scripts && npm run build` 生成静态文件。开发代理配置已指向神码，不携带登录凭据。

版本与发布依据见 [项目事实源](../docs/PROJECT_FACTS.md)。验证：`python3 frontend/scripts/verify_frontend.py`，以及 `node --test frontend/tests/*.test.js`。小程序上传包不包含Web构建与本地依赖；请使用独立发布目录并核对实际包内容、AppID和合法域名。
