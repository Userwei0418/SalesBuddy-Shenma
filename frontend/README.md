# 神码小程序开发与交付

在微信开发者工具中导入本目录。API 指向 `https://salesbuddy.shenzhoukuntai.com:28899/api/v1`；客户 AppID 为 `wx2824bdeb58528fd8`，不得替换为商汤 AppID。微信后台的请求、上传、下载合法域名须按实际使用配置相同的 `https://salesbuddy.shenzhoukuntai.com:28899`，不能省略端口。正式证书已安装并通过常规校验，2026-09-24 开发工具编译预览成功；仍需完成合法域名核对及真机登录、录音与上传测试。

日常修改在本独立仓库进行。测试：在仓库根运行 `node --test frontend/tests/*.test.js`。交付从干净、已提交的代码生成，并记录已核验的后端版本与数据库迁移版本。执行示例：

```bash
python3 frontend/scripts/package_frontend.py --output-dir ../output --backend-revision <已部署的完整提交SHA> --database-version V153
```

代码包包含程序与接口契约，不包含数据库业务数据、账号口令或运行密钥。

见 [部署记录](../docs/DEPLOYMENT.md) 和 [开发说明](docs/给AI的启动提示词.md)。

## 2026-09-28 小程序 UI 1.0.11

当前版本在下述 1.0.10 适配结果上接入 review9 的 10 个文件增量：状态标签恢复红黄绿，个人页六维评分条统一蓝色，首页移除无状态时的“数据已同步”提示。保持神码现行业务逻辑、客户连接及销售/FDE 视角切换。详情见 [1.0.11 更新与验收记录](../docs/MINI_UI_1_0_11_20260928.md)。

### 1.0.10 适配基线

将用户提供的 1.0.8 UI 包适配至神码现行 1.0.9 业务基线，采用统一蓝黑白主题及本地 UI 组件。保留神码登录、可配置权限、团队归属、多人任务和后端接口；Web 与后端继续使用原版本。

UI 已随源码冻结在 `miniprogram/ui/`，本机无需安装 npm 或执行生成脚本。`scripts/vendor_ui.mjs` 仅用于持有对应组件源库的维护环境，不是运行/上传前置步骤。详情与验收范围见 [本次更新记录](../docs/MINI_UI_20260928.md)。
