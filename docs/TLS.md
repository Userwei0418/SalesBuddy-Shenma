# 域名证书与自动续期

采用 Let's Encrypt + Certbot，两台客户服务器分别管理自己的证书。`certbot.timer` 每天检查两次、随机错峰执行，到续期窗口才申请更新；续期成功后自动校验并重载对应 HTTPS 服务，不依赖开发电脑。

## 已落地与剩余条件

2026-09-23 实机已安装 Certbot 1.21.0，两个 `certbot.timer` 均为 enabled/active；本机 HTTP 验证路径已返回正确内容。正式证书尚未签发，真实续期演练尚未执行：两个域名的公网 80、443 仍超时。仅启用定时器不代表证书已签发或续期已验收。

客户网关需配置：

| 域名 | 公网 HTTP 80 的验证请求转发到 | HTTPS 的实际目标 |
|---|---|---|
| salesbuddy.shenzhoukuntai.com | 172.22.9.234:80 | 172.22.9.234:443；现有公网备用入口 223.76.131.120:28899 |
| ops-salesbuddy.shenzhoukuntai.com | 172.22.9.233:80 | 172.22.9.233:443；现有公网备用入口 223.76.131.120:18899 |

80 端口按 Host 转发 `/.well-known/acme-challenge/`，保留原路径；该路径无需登录、不能被认证跳转或 WAF 拦截。其余 HTTP 请求会跳转 HTTPS。两台机器的 80 都由宿主机 Nginx 提供，中台 Docker Nginx 继续监听 HTTPS，端口不冲突。

443 建议按 SNI 透传到对应主机，以使用主机自动续期的证书。若客户网关终止 TLS，公网证书必须由网关侧管理并自动续期，仅更新后端主机证书不会更新网关证书。

## 首次签发与验收

部署脚本位于 `deployment/tls/`，在每台客户服务器用 root 执行 `install-renewal.sh` 安装验证站点、部署钩子和定时器。前置依赖：系统软件源安装的 `certbot`、`nginx`。脚本按主机名限制为本项目两台服务器。

网关配置后，先从外部访问两个域名的 `http://<域名>/.well-known/acme-challenge/shenma-readiness`，分别应得到 `shenma-sales-acme-ready`、`shenma-ops-acme-ready`。再到各服务器执行：

```bash
sudo shenma-issue-certificate
sudo shenma-issue-certificate --dry-run
sudo systemctl is-enabled certbot.timer
sudo systemctl list-timers --all certbot.timer --no-pager
```

中台签发前须先完成中台安装，以便将证书应用到运行实例。签发使用独立 ACME 账户，未设置通知邮箱；`admin@example.com` 仅为中台初始登录标识，不作为证书联系邮箱。首次签发完成后，Certbot 会保存对应续期配置，系统定时器会自动继续执行。

`--dry-run` 使用测试 CA 检验续期路径，不部署测试证书。最后从外网用常规 TLS 校验访问两个 HTTPS 域名，不能使用 `-k` 或临时证书白名单代替正式验收。

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

若已签发但部署钩子失败，修复实例后重新执行 `sudo shenma-issue-certificate`，会复用现有证书并重新应用。

验证方式依据：[Let's Encrypt challenge types](https://letsencrypt.org/docs/challenge-types/)；HTTP-01 必须从公网 80 验证，18899/28899 无法代替。若客户无法开放 80，应改用客户 DNS 的 API 做 DNS-01 自动验证；手工 TXT 不能满足无人值守续期。
