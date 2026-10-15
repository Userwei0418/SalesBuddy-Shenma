# 我的模块页面验收修正

2026-09-15，基线 `0a7e0fc`。仅修改前端样式、展示名称和回归测试；后端继续使用 `8c9e243` / V092，未重新部署。

## 修正

- 季度目标入口改为紧凑的“设置目标”，明确尺寸和文字大小，覆盖原生按钮默认样式。
- 商机比例标题改为“高质量商机占比”，原 A、B 级分子／总商机分母继续显示在下方说明。
- 团队／个人控件固定紧凑宽度，选择成员／团队位于同一行右侧，收紧主体名称与上下卡片的间距。
- 主管穿透权限没有删除。原生按钮默认宽度撑大左侧控件，将右侧入口挤出屏幕；原账号目录与页面状态确认有合法成员和入口节点。修复后无需改变权限接口。
- 成员列表与搜索框同宽，整行可选；弹层高度按人数自适应，并限制最大高度。空搜索保留提示空间。
- 销售、主管、经理、FDE 负责人共用上述选择控件；目标组件同时供销售和 FDE 使用。

## 验证

- 四个前端定向文件 **50 项通过**，新增主管和 FDE 负责人从选择器事件到实际请求参数的回归：UUID 成员映射、三标签主体一致、本人恢复、非法目录 ID 忽略、权限撤回阻止他人选择。
- 前端结构和 JavaScript 语法检查通过（26 页面、47 JSON、343 文件），差异检查通过。
- 微信开发者工具真实主管账号：团队／个人切换；右侧成员入口可见且能点击；选下属后经营事实、营销效率、六维画像均跟随；下属目标不出现编辑入口。
- 已截图检查目标按钮、紧凑选择栏、全宽成员列表和高质量商机占比。原业务截图仅保留本机 `/tmp/profile-controls-after.png`、`/tmp/profile-member-sheet-final.png`、`/tmp/profile-efficiency-final.png`，不提交仓库。
- FDE 负责人事件与目录边界已测试；未将本轮主管页面截图等同于全部角色视觉验收。未提交业务目标或审批，未上传微信体验版。

验证命令：

```sh
node --test --test-reporter=dot frontend/tests/profile-data.test.js frontend/tests/profile-scores.test.js frontend/tests/fde-profile-visual.test.js frontend/tests/profile-logout.test.js
python3 frontend/scripts/verify_frontend.py
git diff --check
```
