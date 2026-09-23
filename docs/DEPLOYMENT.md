# 神码部署记录与操作入口

销售主机：salesbuddy / 172.22.9.234 / SSH 32222。中台主机：opsbuddy / 172.22.9.233 / SSH 12222。共用公网 223.76.131.120。操作前核对 hostname 与内网 IP，不依据旧“服务器 1/2”编号判断。

## 当前访问方案（已由用户确认）

销售入口 `https://salesbuddy.shenzhoukuntai.com:28899`；中台入口 `https://ops-salesbuddy.shenzhoukuntai.com:18899`。使用现有公网映射，证书改为 DNS-01，等待对方运维完成 DNS 验证与证书配置；不再要求公网 80/443。下文 80/443 超时及 HTTP-01 失败为此前排查记录，不代表带端口方案不可用。客户端合法域名须包含端口。

## 2026-09-23 首次实施

- 独立私有 GitHub 已创建，源码来源见 SOURCE_MANIFEST.json。
- 中台原始包已保存到私有 Release `agent-platform-20260923`，服务器端校验通过。
- 中台机失效 file:/cdrom 软件源已备份为 `.shenma-before` 并停用；正常软件源保留。
- 两台机器原有 unattended-upgrades 已结束。安装期间等待 dpkg 锁释放，未删除锁文件或强杀系统升级。
- 两台服务器 Docker Hub 连接失败。改由本地从官方 Docker Hub 按固定摘要下载 linux/amd64 镜像，经哈希验证后传入，不改用未知镜像源。TLS 校验保持开启；本地下载使用 IPv4 转发以避免 IPv6 CDN 连接重置。
- 销售结构需要 PostgreSQL 15+ 的 NULLS NOT DISTINCT；目标安装 PostgreSQL 16，与源项目 CI 基线一致。不能使用 Ubuntu 22.04 默认 PostgreSQL 14。
- 中台机器的 Docker CE 官方仓库 TLS 连接失败，已停用本次新增的 CE 源，改从 Ubuntu 已签名软件源安装 Docker 29.1.3、Compose 2.40.3 和 Buildx 0.30.1。Docker 服务已启用。

## 销售系统实际状态

部署源码：`7ca20056d01c45edbac8ac40d2fa2a837a3e624a`，对应通过 CI 的 PR #1 内容（当时合并提交 `aa8444e`）；后续部署脚本和文档独立在仓库 main 维护。运行目录为 `/opt/shenma-sales/current`，指向 `/opt/shenma-sales/releases/7ca20056d01c45edbac8ac40d2fa2a837a3e624a`。

| 项目 | 已验证结果 |
|---|---|
| Python / 数据库 | Python 3.12.14 / PostgreSQL 16.15，结构 V125 |
| 进程 | `shenma-api`、`shenma-worker`、Nginx 均运行；API 监听 `127.0.0.1:8080` |
| 运行角色 | `shenma_runtime`，NOSUPERUSER、NOBYPASSRLS；禁止读取密码/登录节流表及执行改权函数 |
| 初始管理员 | `CUSTOMERADMIN` 实际登录、获取自身信息、退出通过；仍要求首次改密 |
| 数据边界 | 客户、商机、拜访 0 条；仅初始化客户公司、管理团队、管理员及验证产生的认证记录 |
| 本机 HTTPS | 域名匹配验证通过，当前为 30 天临时自签证书 |
| 公网域名 | 两个域名均解析至 `223.76.131.120`；公网 443 超时，未验收通过 |
| 就绪检查 | 数据库和认证通过；模型未配置，因此 `health/ready` 返回 503 / degraded |

运行配置 `/etc/shenma-sales/runtime.env` 和初始化凭据 `/var/lib/shenma-provision/initial-admin.json` 为 root 0600；私有 keyring 仅运行服务可读。凭据不写入本说明。

`deployment/verify-live-sales.py` 已在客户机执行通过，验证真实运行账号、私有 keyring、初始管理员登录/退出及版本。该脚本仅适用于管理员尚未首次改密的初始化验收；改密后不要使用旧凭据重跑。服务证据保存在客户机 `/tmp/shenma-live-sales-check.json`，不含密码或会话 Token。

销售入口已在 Nginx 配置 `salesbuddy.shenzhoukuntai.com:443`。客户网关还需按域名将公网 443 分流到销售机 `172.22.9.234:443` 和中台机 `172.22.9.233:443`，并配置可信证书；临时证书仅用于本机检查。

已实测预留端口 NAT：公网 28899 进入 salesbuddy 的 28899，公网 18899 进入 opsbuddy 的 18899。销售 Nginx 增加 HTTPS 28899 监听，中台离线安装器增加 HTTPS 18899 映射，保留两台机器的内网 443。若网关通过公网回源，目标应为销售域名 → 28899、中台域名 → 18899，与最早的对应表相反。

销售预留端口已完成外部 HTTPS 实测：使用本次生成的公共证书作受信 CA、保留销售域名 SNI，将连接指向公网 28899，`/api/v1/health/version` 返回 200 和部署版本 `7ca20056...`。未关闭 TLS 校验。此结果说明预留端口及销售服务可用；不代表默认公网 443 或浏览器可信证书已经完成。

2026-09-23 已完成首次备份恢复演练。快照位于客户机 `/var/backups/shenma-sales/20260923T091815Z`，已校验全部归档哈希，并把数据库恢复到临时独立库：V125、1 公司、1 用户、客户/商机/拜访各 0 条。配置和 keyring 与快照一致，上传目录归档可读取；API/Worker 已恢复运行。临时恢复库及解包目录已删除，脱敏结果保留在快照的 `restore-check.json`。快照包含秘密，留在客户机，不进入代码仓库或源码交付包。

## 首次数据库权限

`deployment/runtime-grants.sql` 来源于同版本原系统的只读 ACL 元数据（schema、表、序列、函数权限），没有读取密码、连接环境或业务行。只移植权限形状，映射至神码独立角色 `shenma_runtime`；不复制原数据库角色、所有者或 BYPASSRLS 属性。

已迁移 V125，创建 NOSUPERUSER NOBYPASSRLS 的运行账号、应用显式授权并执行迁移器权限核对。`verify-runtime-access.sql` 和真实登录检查均通过；模型业务链路和小程序真机链路仍待外部配置。

## 中台发行资产

`agent-platform/runtime/build/web/web-code.tgz` 为 127MB 编译产物，位于原始 Release 安装包，不入 Git。原始 installer 的 SHA256SUMS 对原始文件有效；客户修改须使用新的构建清单，不能直接替换文件后沿用原清单。

## Let's Encrypt 与自动续期

2026-09-23 两机均安装 Certbot 1.21.0，`certbot.timer` 已 enabled/active，每日两次自动检查。宿主机 Nginx 的 HTTP 验证路径通过本机及两机内网互访；部署钩子的替换、失败恢复、无关证书隔离 3 项检查在本地和两台机器通过。手动触发 `certbot.service` 正常退出；因尚无正式证书，此次属于空配置执行，不是真实续期验收。

两机部署钩子 SHA256 均为 `074a9295a2cb03562fc50678f24ba484ebba5bfd05888f499a8d21e9c055273b`。销售 API、Worker、HTTPS 在配置后保持 active，版本接口仍返回上述 7ca20056 / V125。

正式证书尚未签发。随后两台实际运行 `certbot certonly --dry-run`，测试 CA 对两个域名均返回 `Timeout during connect`，确认无法从公网 80 下载验证文件。日志在各服务器 `/var/log/shenma-acme-staging-check.log`。公网两个域名的 80/443 仍超时，需客户网关提供验证与 HTTPS 转发；签发及实际续期演练见 [TLS.md](TLS.md)。

## 待外部输入

- 客户小程序 AppID：用户稍后提供。
- 客户可用模型配置/额度：等待接入方式确认；不自行使用商汤 Key。
- 对方运维完成证书配置：当前选择 DNS-01，证书申请、DNS 验证及自动续期由对方运维管理，复用我们已安装的任务和重载钩子。临时本机证书验收不计为正式域名验收。
- 中台完整 Web 开发源码：待补；不阻止先运行已有发行包。

## 中台离线安装方式

`deployment/install-agent-offline.py` 校验原包 SHA256SUMS、全部镜像压缩包哈希以及 BASE-IMAGES 精确覆盖，再导入 Docker。原官方摘要与本地标签对应关系写入 `offline-provenance.json`。仅在目标实例中把 Compose 镜像和 Dockerfile FROM 改为已验证的本地标签，禁用启动时拉取；原始包保留不变。

销售依赖就绪后，以 root 在销售 release 目录运行 `bash deployment/install-sales.sh`。它只接受 salesbuddy 主机，创建新库、受限运行角色、客户公司及强制首次改密管理员，再启动 API/Worker。初始账号凭据只保存在服务器 `/var/lib/shenma-provision/initial-admin.json`（root 0600）。脚本发现已有数据库时停止，不会删除或重建；部分失败需检查已完成阶段后继续。

AppID、可信证书和模型 Key 不进入仓库。当前源码包不含任何数据库业务数据；数据库结构和系统规则通过迁移创建。
