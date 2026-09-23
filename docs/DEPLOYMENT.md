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

**2026-09-24 更新：** 当前运行源码为 `d287330a1f04317c634147e5792362ebcd4234de`，API/Worker 运行正常，结构 V125。`current` 指向同 SHA 的 releases 目录，旧 7ca20056 保留用于回退。管理员已通过正常改密接口完成首次改密，受限交接文件已更新，验证脚本已支持并通过改密后核验。中台现已发布 12 个销售 Agent，4 项核心能力限部署管理员进行 48 小时试点；详见 [运行验收](RUNTIME_ACCEPTANCE_20260924.md)、[核心能力验收](CORE_AGENT_ACCEPTANCE.md) 和 [辅导建议验收](COACHING_ACCEPTANCE.md)。

以下首次部署、初始数据行数、备份和证书排查记录为历史快照，不代表当前运行版本或当前应用数量。正式可信证书仍待配置。

部署源码：`7ca20056d01c45edbac8ac40d2fa2a837a3e624a`，对应通过 CI 的 PR #1 内容（当时合并提交 `aa8444e`）；后续部署脚本和文档独立在仓库 main 维护。运行目录为 `/opt/shenma-sales/current`，指向 `/opt/shenma-sales/releases/7ca20056d01c45edbac8ac40d2fa2a837a3e624a`。

| 项目 | 已验证结果 |
|---|---|
| Python / 数据库 | Python 3.12.14 / PostgreSQL 16.15，结构 V125 |
| 进程 | `shenma-api`、`shenma-worker`、Nginx 均运行；API 监听 `127.0.0.1:8080` |
| 运行角色 | `shenma_runtime`，NOSUPERUSER、NOBYPASSRLS；禁止读取密码/登录节流表及执行改权函数 |
| 初始管理员 | `CUSTOMERADMIN` 实际登录、获取自身信息、退出通过；仍要求首次改密 |
| 数据边界 | 客户、商机、拜访 0 条；仅初始化客户公司、管理团队、管理员及验证产生的认证记录 |
| 本机 HTTPS | 域名匹配验证通过，当前为 30 天临时自签证书 |
| 公网销售入口 | `:28899/admin` 和版本接口已返回 200；系统默认信任校验仍报告自签证书，待更换可信证书 |
| 就绪检查 | 用户提供调试模型配置后，数据库、模型配置和认证均通过，`health/ready` 返回 200 / ok |

运行配置 `/etc/shenma-sales/runtime.env` 和初始化凭据 `/var/lib/shenma-provision/initial-admin.json` 为 root 0600；私有 keyring 仅运行服务可读。凭据不写入本说明。

`deployment/verify-live-sales.py` 已在客户机执行通过，验证真实运行账号、私有 keyring、初始管理员登录/退出及版本。该脚本仅适用于管理员尚未首次改密的初始化验收；改密后不要使用旧凭据重跑。服务证据保存在客户机 `/tmp/shenma-live-sales-check.json`，不含密码或会话 Token。

销售 Nginx 已监听 28899，保留内部 443。当前使用已确认的带端口入口，不要求客户额外配置公网 443 分流。临时自签证书可用于指定信任后的联调，不能作为微信小程序正式 HTTPS 验收。

已实测预留端口 NAT：公网 28899 进入 salesbuddy 的 28899，公网 18899 进入 opsbuddy 的 18899。销售 Nginx 监听 HTTPS 28899，中台安装配置使用主机 18899 → 容器 443。公网映射应为销售域名 → 28899、中台域名 → 18899，与最早职责互换前的对应表相反。

销售预留端口已完成外部 HTTPS 实测：使用本次生成的公共证书作受信 CA、保留销售域名 SNI，将连接指向公网 28899，`/api/v1/health/version` 返回 200 和部署版本 `7ca20056...`。未关闭 TLS 校验。此结果说明预留端口及销售服务可用；不代表默认公网 443 或浏览器可信证书已经完成。

2026-09-23 已完成首次备份恢复演练。快照位于客户机 `/var/backups/shenma-sales/20260923T091815Z`，已校验全部归档哈希，并把数据库恢复到临时独立库：V125、1 公司、1 用户、客户/商机/拜访各 0 条。配置和 keyring 与快照一致，上传目录归档可读取；API/Worker 已恢复运行。临时恢复库及解包目录已删除，脱敏结果保留在快照的 `restore-check.json`。快照包含秘密，留在客户机，不进入代码仓库或源码交付包。

## 首次数据库权限

`deployment/runtime-grants.sql` 来源于同版本原系统的只读 ACL 元数据（schema、表、序列、函数权限），没有读取密码、连接环境或业务行。只移植权限形状，映射至神码独立角色 `shenma_runtime`；不复制原数据库角色、所有者或 BYPASSRLS 属性。

已迁移 V125，创建 NOSUPERUSER NOBYPASSRLS 的运行账号、应用显式授权并执行迁移器权限核对。`verify-runtime-access.sql` 和真实登录检查均通过；模型业务链路和小程序真机链路仍待外部配置。

## 中台安装与账户验收（2026-09-24）

- 实例：`/opt/raccoon-agent`，独立 Compose 项目 `raccoon_a040fe4cce`。原始安装包及 12 个基础镜像全部校验通过，安装任务退出码 0。
- 17 项服务检查通过：16 个服务运行，`init_permissions` 以 0 正常退出；有健康检查的服务均为 healthy。仅 Nginx 对主机开放 HTTPS 18899，数据库、Redis、向量库和沙箱未发布宿主机端口。
- 外部 `:18899/console/api/setup`、`/signin`、品牌 JS 和 favicon 均返回 200；使用指定临时证书保持 TLS 校验，尚非系统默认信任。
- 管理员 `admin@example.com` 已通过 HTTPS API 初始化、登录、读取自身信息和退出；重复执行检查后仍为 1 个工作空间、0 个应用。密码只保存在客户机 `/var/lib/shenma-provision/agent-admin.json`（root 0600），不进入交付包。邮箱用作登录标识，未配置邮件发送。
- `deployment/bootstrap-agent-admin.py` 仅允许在 opsbuddy 以 root 执行；先检查实例地址及初始化状态，已有实例但缺少匹配凭据记录时拒绝重新初始化。此脚本仅用于初次交付；管理员改密后不要使用旧凭据重跑。
- 双向内网 HTTPS 已验证：销售 `.234` → 中台 `.233:18899` 的 setup 接口，中台 → 销售 `.234:28899` 的版本接口。连接检查使用指定证书，不关闭 TLS 校验，也未把临时证书加入系统全局信任。
- 用户提供的 SenseAudio 调试 Key 已通过模型列表及真实文字请求；销售后端 SenseAudioClient 以运行账号调用通过。中台供应商配置和销售 Agent 绑定继续推进；网络连通、容器运行和管理员登录不等于 AI 业务闭环完成。

## 首次中台备份恢复

2026-09-24 已使用 `deployment/backup-agent.py` 完成维护窗口备份：停止应用写入，对 `dify`、`dify_plugin` 分别做逻辑备份并恢复到临时数据库，逐表行数比对通过（143 / 13 张表），随后删除本次新建的临时库。停止数据库后归档实例配置、证书、卷目录和管理员交接凭据，2,490 个归档文件可完整读取；恢复原服务并通过 HTTPS 健康检查。

快照仅保存在客户机 `/var/backups/shenma-agent/20260923T174819Z`，包含秘密和实例数据，禁止上传源码仓库或普通交付包。物理卷归档已检查可读性；逻辑数据库恢复已实际演练，未执行整机替换演练。

## 中台发行资产

`agent-platform/runtime/build/web/web-code.tgz` 为 127MB 编译产物，位于原始 Release 安装包，不入 Git。原始 installer 的 SHA256SUMS 对原始文件有效；客户修改须使用新的构建清单，不能直接替换文件后沿用原清单。

## Let's Encrypt 与自动续期

2026-09-23 两机均安装 Certbot 1.21.0，`certbot.timer` 已 enabled/active，每日两次自动检查。宿主机 Nginx 的 HTTP 验证路径通过本机及两机内网互访；部署钩子的替换、失败恢复、无关证书隔离 3 项检查在本地和两台机器通过。手动触发 `certbot.service` 正常退出；因尚无正式证书，此次属于空配置执行，不是真实续期验收。

两机部署钩子 SHA256 均为 `074a9295a2cb03562fc50678f24ba484ebba5bfd05888f499a8d21e9c055273b`。销售 API、Worker、HTTPS 在配置后保持 active，版本接口仍返回上述 7ca20056 / V125。

正式证书尚未签发。随后两台实际运行 `certbot certonly --dry-run`，测试 CA 对两个域名均返回 `Timeout during connect`，确认无法从公网 80 下载验证文件。日志在各服务器 `/var/log/shenma-acme-staging-check.log`。这是此前 HTTP-01 方案的失败记录。当前改用 DNS-01，不再要求客户开放公网 80/443；由客户运维完成可信证书配置，签发及实际续期演练见 [TLS.md](TLS.md)。

## 待外部输入

- 客户小程序 AppID：用户稍后提供。
- 调试模型已由用户提供：SenseAudio `https://api.senseaudio.cn`，文字模型 `senseaudio-s2-lite`。Key 仅在客户机受限配置中保存，未复制商汤运行环境凭据；正式客户 Key 可后续替换。
- 对方运维完成证书配置：当前选择 DNS-01，证书申请、DNS 验证及自动续期由对方运维管理，复用我们已安装的任务和重载钩子。临时本机证书验收不计为正式域名验收。
- 中台完整 Web 开发源码：待补；不阻止先运行已有发行包。

## 中台离线安装方式

`deployment/install-agent-offline.py` 校验原包 SHA256SUMS、全部镜像压缩包哈希以及 BASE-IMAGES 精确覆盖，再导入 Docker。原官方摘要与本地标签对应关系写入 `offline-provenance.json`。仅在目标实例中把 Compose 镜像和 Dockerfile FROM 改为已验证的本地标签，禁用启动时拉取；原始包保留不变。

销售依赖就绪后，以 root 在销售 release 目录运行 `bash deployment/install-sales.sh`。它只接受 salesbuddy 主机，创建新库、受限运行角色、客户公司及强制首次改密管理员，再启动 API/Worker。初始账号凭据只保存在服务器 `/var/lib/shenma-provision/initial-admin.json`（root 0600）。脚本发现已有数据库时停止，不会删除或重建；部分失败需检查已完成阶段后继续。

AppID、可信证书和模型 Key 不进入仓库。当前源码包不含任何数据库业务数据；数据库结构和系统规则通过迁移创建。
