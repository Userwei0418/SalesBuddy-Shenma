# 仓库可见性核验记录

2026-09-24 收尾核验时，GitHub API 返回 `Userwei0418/SalesBuddy-Shenma` 的 `visibility=PUBLIC`、`private=false`。这与本项目已确认的私有交付要求不符，也说明此前文档中的“私有”描述不能作为实时证据。本次未取得可见性变更历史，无法据此确认公开状态的起始时间或原因。

已执行私有化并再次核验：

- GitHub API 返回 `visibility=PRIVATE`、`private=true`。
- 未携带登录凭据访问仓库页及最新交付 Release 页均返回 HTTP 404。
- Git remote 仍仅为神码独立仓库，未添加商汤或公司 GitLab 目标。
- 对当前可达 Git 历史的 3,272 个 blob 检查长 `sk-` Key、长 `app-` Key 和私钥头特征，未命中。该特征检查不是所有类型秘密的完整审计，不能证明此前无人获取源码。

防止再次误判：完整源码和前端打包器均实时调用 GitHub API，要求仓库全名匹配且 `private=true`；无法核验或仓库公开时停止。CI 另检查 GitHub 事件中的仓库名称和私有标记。真实私有状态检查通过，模拟公开状态被打包前检查拒绝。

仓库和 Release 当前需要授权访问。私有化不能撤回公开期间可能已经下载的副本；本次没有证据可确定是否发生过外部下载。
