# 域名证书与续期

## 当前证书（2026-09-24）

客户提供的两份证书已安装，域名、证书私钥配对和系统 CA 证书链校验通过。公网访问销售管理后台返回 HTTP 200，中台跟随登录跳转后返回 HTTP 200；均未使用 `-k` 或临时信任白名单。

| 入口 | 证书签发者 | 到期时间（北京时间） |
|---|---|---|
| `https://salesbuddy.shenzhoukuntai.com:28899` | SanFront DV TLS RSA CA G2，北京国科云计算技术有限公司 | 2026-10-24 23:59:59 |
| `https://ops-salesbuddy.shenzhoukuntai.com:18899` | 同上 | 2026-10-24 23:59:59 |

应用前保留原证书备份，使用原子文件替换，Nginx 配置检查通过后重载。销售后端的 `AGENT_FDE_CA_BUNDLE` 已清空，访问中台恢复系统公共 CA 校验。私钥仅留在受限运行目录和受限备份中，不进入仓库及交付包。

| 项目 | 路径 |
|---|---|
| 销售 Nginx 证书及私钥 | `/etc/shenma-sales/tls/server.crt`、`server.key` |
| 中台 Nginx 证书及私钥 | `/opt/raccoon-agent/runtime/tls/server.crt`、`server.key` |
| 本次应用回执 | 各主机 `/var/lib/shenma-tls/provided-certificate-deployment.json` |
| 原证书受限备份 | 各主机 `/var/lib/shenma-tls/before-provided-20260924T*/` |

## 尚需完成：续签和自动更新

本次是客户提供的国科云证书，不是此前拟申请的 Let's Encrypt 证书。两机已有 `certbot.timer`，但 `/etc/letsencrypt/renewal/` 尚无续期配置；定时器不会自动续签这两份外部交付证书。

请客户运维确认当前证书的续签方式及负责人，在到期前完成续签，并明确新证书如何安全下发到服务目录、校验并重载。若当前签发服务支持自动签发，应由客户配置其自动化条件，再联合验证；不能把“已安装证书”记作“自动续期已验收”。无需向项目团队交付 DNS 主账号或扩大 DNS 管理权限。

已有 Certbot 部署钩子 `/etc/letsencrypt/renewal-hooks/deploy/50-shenma-https` 仅处理其约定的 Certbot lineage。若后续改用 Let's Encrypt DNS-01，可复用该钩子；若继续使用当前服务商，应接入对应的签发和部署流程。

## 微信验收

客户 AppID 为 `wx2824bdeb58528fd8`，已写入神码独立工程。微信开发者工具已使用该 AppID 编译预览成功并生成二维码；真机登录、录音及上传仍需验收。

客户小程序后台的请求、上传、下载合法域名须按实际使用配置 `https://salesbuddy.shenzhoukuntai.com:28899`，与客户端保持一致。预览成功不证明合法域名配置或全部真机功能已验收。

## 历史记录

此前 HTTP-01 测试因公网 80 超时失败，后改选 DNS-01，由客户运维持有 DNS 权限。此次提供证书已解除首次可信 HTTPS 的部署阻点，现有带端口方案继续使用，不要求新增公网 80/443。
