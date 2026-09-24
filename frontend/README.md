# 神码小程序开发与交付

在微信开发者工具中导入本目录。API 指向 `https://salesbuddy.shenzhoukuntai.com:28899/api/v1`；客户 AppID 为 `wx2824bdeb58528fd8`，不得替换为商汤 AppID。微信后台的请求、上传、下载合法域名须按实际使用配置相同的 `https://salesbuddy.shenzhoukuntai.com:28899`，不能省略端口。正式证书已安装并通过常规校验，2026-09-24 开发工具编译预览成功；仍需完成合法域名核对及真机登录、录音与上传测试。

日常修改在本独立仓库进行。测试：在仓库根运行 `node --test frontend/tests/*.test.js`。交付从干净、已提交的代码生成，并记录已核验的后端版本与数据库迁移版本。执行示例：

```bash
python3 frontend/scripts/package_frontend.py --output-dir ../output --backend-revision <已部署的完整提交SHA> --database-version V125
```

代码包包含程序与接口契约，不包含数据库业务数据、账号口令或运行密钥。

见 [部署记录](../docs/DEPLOYMENT.md) 和 [开发说明](docs/给AI的启动提示词.md)。
