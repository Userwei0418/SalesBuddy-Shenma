# 域名证书与自动续期

用户已确认直接使用销售 `https://salesbuddy.shenzhoukuntai.com:28899`、中台 `https://ops-salesbuddy.shenzhoukuntai.com:18899`。证书采用 Let's Encrypt DNS-01；无需为此方案新增公网 80/443。

## 当前状态

- 两台已安装 Certbot 1.21.0，`certbot.timer` 为 enabled/active，每天检查两次；证书成功续期后由部署钩子校验并重载 HTTPS。
- 销售 28899 已通过临时公共证书验证并返回正确 API 版本；正式可信证书尚未签发。中台仍在安装。
- 曾运行 HTTP-01 测试，Let's Encrypt 测试 CA 对两个域名均返回公网 80 连接超时。现已改选 DNS-01，这一结果不再作为当前方案的阻断条件。
- 权威 DNS 查询为 `dns13.hichina.com`、`dns14.hichina.com`，对应阿里云/万网解析。DNS 验证及首次签发待对方运维配置，真实续期演练尚未通过。

## 对方运维负责证书与 DNS 自动验证

用户已明确不采用向我们交付 DNS 管理权限的方式。由对方域名/服务器运维申请两个域名的正式证书，在各客户服务器配置 DNS 验证和无人值守续期，凭据由对方保管；我们负责服务部署、提供已安装的续期重载钩子并联合验收。

验证名称为 `_acme-challenge.salesbuddy.shenzhoukuntai.com` 与 `_acme-challenge.ops-salesbuddy.shenzhoukuntai.com`。请对方复用 Certbot 时，分别以本机完整域名作为 `--cert-name`，将其自行管理的 DNS 验证参数写入对应续期配置。无需向我们提供 DNS 账号、API 授权链接或主账号权限。

对方可直接复用已有 `certbot.timer` 和部署钩子。若使用其他客户端，应配置等效的自动更新服务证书及 Nginx 重载，不能只完成首次签发。验收需包含真实续期演练。

## 首次签发与验收

`deployment/tls/install-renewal.sh` 安装服务部署钩子和系统定时器。现有 HTTP 验证站点可保留作为内网检查入口；`shenma-issue-certificate` 当前是 HTTP-01 的备选工具，不适用于新选定的 DNS-01 流程，不能用它代替 DNS 客户端配置。

DNS 客户端配置完成后，先通过测试 CA 验证自动创建/清理记录，再正式签发对应域名证书。Certbot 保存验证参数后，系统定时器将复用该参数自动续期。中台应先完成安装，才能把证书应用到运行实例。

正式证书签发后，在每台机器使用对应域名执行：

```bash
# 销售主机；中台将域名改为 ops-salesbuddy.shenzhoukuntai.com
sudo certbot renew --cert-name salesbuddy.shenzhoukuntai.com --dry-run
sudo systemctl is-enabled certbot.timer
sudo systemctl list-timers --all certbot.timer --no-pager
```

测试不加 `--run-deploy-hooks`，避免部署测试 CA 证书。最后从外网用常规 TLS 校验访问上述带端口 HTTPS 地址，不能用 `-k` 或临时证书白名单代替正式验收。

微信小程序的请求、上传、下载合法域名须按实际使用配置相同的带端口销售地址；客户端请求也须一致。客户 AppID 和真机验收仍待完成。依据：[微信网络文档](https://developers.weixin.qq.com/miniprogram/dev/framework/ability/network.html)。

## 证书应用与排障

| 项目 | 路径 / 行为 |
|---|---|
| Certbot 原始证书 | `/etc/letsencrypt/live/<域名>/`，私钥保留在服务器 |
| 销售 Nginx 使用的证书 | `/etc/shenma-sales/tls/server.crt`、`server.key` |
| 中台 Nginx 使用的证书 | `/opt/raccoon-agent/runtime/tls/server.crt`、`server.key` |
| 自动部署钩子 | `/etc/letsencrypt/renewal-hooks/deploy/50-shenma-https` |
| 成功应用回执 | `/var/lib/shenma-tls/last-deploy.json`，只含域名、时间和公开证书摘要 |

钩子校验域名、有效期和证书私钥配对，替换文件前保留临时备份；Nginx 配置检查通过才重载，失败时恢复原文件。中台以目录挂载证书，文件更新对容器可见。钩子只处理本机对应域名，不作用于其他证书。

```bash
sudo journalctl -u certbot.service --since '7 days ago' --no-pager
sudo tail -n 60 /var/log/letsencrypt/letsencrypt.log
sudo cat /var/lib/shenma-tls/last-deploy.json
# 销售主机；中台将域名改为 ops-salesbuddy.shenzhoukuntai.com
sudo openssl x509 -in /etc/letsencrypt/live/salesbuddy.shenzhoukuntai.com/fullchain.pem -noout -dates
```

若已签发但部署钩子失败，修复实例后，以 root 设置 `RENEWED_LINEAGE=/etc/letsencrypt/live/<对应域名>` 并执行部署钩子，重新应用已签发证书，不需要重复签发。

验证方式依据：[Let's Encrypt challenge types](https://letsencrypt.org/docs/challenge-types/)；HTTP-01 必须从公网 80 验证，18899/28899 无法代替。当前已选择 DNS-01，验证不依赖 Web 端口；手工 TXT 不能满足无人值守续期。
