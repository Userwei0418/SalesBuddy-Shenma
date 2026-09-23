# 神码部署记录与操作入口

销售主机：salesbuddy / 172.22.9.234 / SSH 32222。中台主机：opsbuddy / 172.22.9.233 / SSH 12222。共用公网 223.76.131.120。操作前核对 hostname 与内网 IP，不依据旧“服务器 1/2”编号判断。

## 2026-09-23 首次实施

- 独立私有 GitHub 已创建，源码来源见 SOURCE_MANIFEST.json。
- 中台原始包已保存到私有 Release `agent-platform-20260923`，服务器端校验通过。
- 中台机失效 file:/cdrom 软件源已备份为 `.shenma-before` 并停用；正常软件源保留。
- 两台机器本来就在执行 unattended-upgrades。安装遇到 dpkg 锁时等待，不删除锁文件、不强杀系统升级。
- 两台服务器 Docker Hub 连接失败。改由本地从官方 Docker Hub 按固定摘要下载 linux/amd64 镜像，经哈希验证后传入，不改用未知镜像源。TLS 校验保持开启；本地下载使用 IPv4 转发以避免 IPv6 CDN 连接重置。
- 销售结构需要 PostgreSQL 15+ 的 NULLS NOT DISTINCT；目标安装 PostgreSQL 16，与源项目 CI 基线一致。不能使用 Ubuntu 22.04 默认 PostgreSQL 14。

## 首次数据库权限

`deployment/runtime-grants.sql` 来源于同版本原系统的只读 ACL 元数据（schema、表、序列、函数权限），没有读取密码、连接环境或业务行。只移植权限形状，映射至神码独立角色 `shenma_runtime`；不复制原数据库角色、所有者或 BYPASSRLS 属性。

先迁移 V125，再创建 NOSUPERUSER NOBYPASSRLS 的运行账号、应用显式授权并执行迁移器权限核对。`verify-runtime-access.sql` 检查运行账号不是 owner、不具备超级权限、不能读取密码表及改变权限。最终还需真实受限身份登录及业务链路验证。

## 中台发行资产

`agent-platform/runtime/build/web/web-code.tgz` 为 127MB 编译产物，位于原始 Release 安装包，不入 Git。原始 installer 的 SHA256SUMS 对原始文件有效；客户修改须使用新的构建清单，不能直接替换文件后沿用原清单。

## 待外部输入

- 客户小程序 AppID：用户稍后提供。
- 客户可用模型配置/额度：等待接入方式确认；不自行使用商汤 Key。
- 公网 443 分流和可信证书：等待客户 IT 配合方式。临时本机证书验收不计为正式域名验收。
- 中台完整 Web 开发源码：待补；不阻止先运行已有发行包。

## 中台离线安装方式

`deployment/install-agent-offline.py` 校验原包 SHA256SUMS、全部镜像压缩包哈希以及 BASE-IMAGES 精确覆盖，再导入 Docker。原官方摘要与本地标签对应关系写入 `offline-provenance.json`。仅在目标实例中把 Compose 镜像和 Dockerfile FROM 改为已验证的本地标签，禁用启动时拉取；原始包保留不变。

销售依赖就绪后，以 root 在销售 release 目录运行 `bash deployment/install-sales.sh`。它只接受 salesbuddy 主机，创建新库、受限运行角色、客户公司及强制首次改密管理员，再启动 API/Worker。初始账号凭据只保存在服务器 `/var/lib/shenma-provision/initial-admin.json`（root 0600）。脚本发现已有数据库时停止，不会删除或重建；部分失败需检查已完成阶段后继续。

AppID、可信证书和模型 Key 不进入仓库。当前源码包不含任何数据库业务数据；数据库结构和系统规则通过迁移创建。
