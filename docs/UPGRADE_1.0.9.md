# 神码已有环境升级到 1.0.9

适用：销售主机 `salesbuddy` / `172.22.9.234`，已有 V125 数据库。首次安装脚本不用于本次升级。

1. 在干净的神码提交运行 `scripts/package_source.py --output-dir <仓库外目录>`，记录源码 SHA、包 SHA256；按 `deployment/runtime-requirements.txt` 准备 Linux Python 3.12 离线 wheel 包，附 SHA256SUMS。
2. 将两个包及本版 `deployment/upgrade-sales.py` 传到销售主机，核对当前 REVISION 与计划一致。
3. 以 root 执行：

```bash
python3 upgrade-sales.py \
  --archive <源码包> --archive-sha256 <源码包SHA256> \
  --wheels <依赖包> --wheels-sha256 <依赖包SHA256> \
  --revision <本次神码40位提交> --expected-current <现场40位提交>
```

脚本校验目标主机、包、AppID、历史 SQL 及 V125 状态，先在新 release 安装独立依赖，再停止 API/Worker。备份数据库、上传文件、运行配置和交接文件；实际恢复到临时数据库并比对所有业务 schema 表行数，然后执行迁移。升级后逐行摘要核对账号、密码、任职、客户及归属不变，重复迁移检查后切换 current，并检查版本、就绪状态与两个服务。

若迁移或服务检查失败，在停服状态下保留失败库，恢复已验证的升级前数据库和代码。快照留在 `/var/backups/shenma-sales/`；含客户数据与秘密，不下载或上传仓库。升级后不得仅切换旧代码作为数据库回滚。

4. 以客户既有测试账号验证各角色登录、主页、后台权限、公司隔离和新接口；不重建账号、不覆盖密码。
5. 客户中台只更新对应 Agent 提示词，保留模型及运行标识；记录发布快照并按后端契约验证。代码包中存在模板不代表运行中已启用。
6. 以神码 AppID 编译预览小程序，核对 API 地址及新后端。微信正式审核发布单独处理。

升级回执分别记录源码提交、运行提交、实际数据库迁移、Agent 发布和微信预览状态，见 SYNC_1.0.9_20260924.md。
