# 神码 SalesBuddy 项目事实

更新日期：2026-10-27。时间均为北京时间（Asia/Shanghai）。本文仅登记神码产品与部署事实，维护步骤见[运维交接](OPERATIONS_HANDOVER.md)。

## 当前版本

| 项目 | 当前事实 |
| --- | --- |
| 公开代码交付仓库 | [Userwei0418/SalesBuddy-Shenma](https://github.com/Userwei0418/SalesBuddy-Shenma) |
| 业务后端与Web | SalesBuddy 1.2.14；已部署运行源码`4b90d47e464ccfac8285e91335f8914ae36161fd` |
| 数据库迁移 | 实际V449；运行源码expected_schema为V447，V448/V449为后续兼容增量 |
| 小程序 | Mini 1.2.17；源码`c21b0e95ec02a01135ed940f89103fa9a297b76f` |
| 微信AppID | `wx2824bdeb58528fd8` |
| 小程序发布状态 | 用户于2026-10-27确认正式发布和部署完成；版本依据已有1.2.17上传证据，未追加独立微信平台验收 |
| Agent中台程序 | 1.17.0；本次报告接入未升级中台程序 |

运行源码、小程序源码与公开仓库最终交付提交是不同标识。最终交付SHA应由交付记录登记，不用上述源提交替代。公开源码更新不自动代表服务器或微信重新发布。

现行Web开发源码入口为`frontend/web`，保留`agent-platform`；旧`business-web`不再作为现行入口。

## 服务入口

| 用途 | 地址 |
| --- | --- |
| 销售Web | <https://salesbuddy.shenzhoukuntai.com:28899/web/?mode=live> |
| 管理端 | <https://salesbuddy.shenzhoukuntai.com:28899/admin> |
| 业务API | `https://salesbuddy.shenzhoukuntai.com:28899/api/v1` |
| Agent中台 | <https://ops-salesbuddy.shenzhoukuntai.com:18899> |

两域名新SSL证书于2026-10-27安装生效，正常CA链、域名、配置及实际TLS校验通过，到期时间均为**2027-05-13 23:59:59**。续期由神码运维负责，目前未建立自动续期或提醒。

## 生效配置

- 统一`work_report`使用“神码-工作报告”版本4，正式与隔离环境均已启用绑定；保留神码自己的模型供应商/API，主模型`senseaudio-s2`，备用直连`senseaudio-s2-lite`。
- 原有Agent、供应商凭据和独立周报配置保留。报告提示词仅强化来源ID、严格JSON字段和完整统计句约束，未修改业务校验器、权限或数据库结构。
- 一个内置月度通用报告样本已完成真实AI生成、私有文档保存及授权读回。`filtered_facts`不返回可核验运行快照，`runtime_snapshot_verified=false`；发布版本由中台元数据单独核对。
- 飞书总开关仍为false，原映射与通知配置保留；Worker运行不代表同步已开启。
- 角色模板按已批准通用基准配置，保留个人额外授权/禁用。常规业务查看范围与编辑、报告、管理权限分别控制；公司隔离保持。

## 验收边界与待办

标准公网HTTPS只读登录、业务读取、写入拒绝和退出已有成功记录；不替代Android/iOS完整真机、普通写入、附件和所有正式账号验收。日报、周报及其他报告方案没有逐项生成验收，其他AI能力不能用月度报告样本代替。

扩大地图可见范围后的评分仍有契约失败待处理，最终覆盖验收暂停；数据库直导不会自动补齐评分。API、数据库连接和systemd曾出现偶发延迟，后续公网接口恢复，根因尚未证实。最后一次完整保护摘要核对通过，后续收尾的完整复核回执仍待补齐。

后台部分配置页跳转入口需适配神码中台。实际神码工作报告API绑定和调用不受该跳转入口影响，已通过的AI生成样本结论保持。

本公开交接文档不列出密码、密钥、令牌、人员名单、业务内容、租户/用户/业务记录标识、内部网络或SSH入口。运行源码和既有迁移中的固定空间标识属于兼容契约，不是账号凭据；不得擅自替换已执行迁移。备份位置、受限配置定位及详细验收回执由运维私有交接材料管理。

## 本次公开源码交付

交付分支为`codex/shenma-deployed-handover-20261027`，源码和检查范围见 [2026-10-27交付记录](GITHUB_DELIVERY_20261027.md)。GitHub推送与PR状态以实际远端回读为准；本地提交不等于已推送。

2026-10-27 22:29北京时间复核：Write权限已开通，`permissions.push=true`；[交付分支](https://github.com/Userwei0418/SalesBuddy-Shenma/tree/codex/shenma-deployed-handover-20261027)推送成功，Git远端引用与GitHub分支API均回读到`a7770ccc5ab02e60bf1b324978af617db141140c`，包含源码快照`b39205d484830aac2d7f763a933470d8ca084135`。此前写权限不足的阻塞已解决。本次状态补记仅涉及文档；分支后续文档提交的完整SHA以远端引用及提交历史为准。`main`仍为`61362e9ad65d74c707f89b122d740f9fe80cf964`，尚未合并、未创建PR。权限处理与后续核验步骤见[运维交接](OPERATIONS_HANDOVER.md)；线上部署和小程序发布状态保持独立登记，本次未重新发布。
