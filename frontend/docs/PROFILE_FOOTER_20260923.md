# 我的页退出入口布局

“退出登录 / 切换账号”与版本文字组成页尾。页面内容不足一屏时，页尾靠近原生底部导航上方；超过一屏时，页尾跟随内容正常滚动，不悬浮遮挡卡片。营销成熟度、营销效率、销售画像及 FDE 内容共用布局，登录事件与接口契约不变。

## 基准和范围

- 开发基准：main `7a760e523cf4da829f6e803b49fdd224076d6a2a`，修改前工作区干净。
- 唯一仓库：`Raccoon SalesBuddy`（本文件所在仓库）；GitHub `Userwei0418/Raccoon-SalesBuddy`，GitLab `zhouwei5/raccoon-salesbuddy`。
- 代码：`miniprogram/pages/profile/index.wxml` 与 `index.wxss`。沿用页面已有最小视口高度和底部间距，内容区与页尾使用弹性布局。

## 验证

- `node --test frontend/tests/*.test.js`：771 项通过。
- `python3 frontend/scripts/verify_frontend.py`：代码检查通过；1.0.7 发行复核发现此文档后来写入本机绝对路径使打包检查失败，现已改为仓库相对说明，并重新通过检查。
- 微信开发者工具实际编译及截图检查通过。390px 宽模拟器短页高度等于 671px 可视区，页尾顶部 554px；成熟度长页自然增长至 685.52px，页尾在内容之后；销售画像长内容不被退出入口遮挡。
- IDE：本仓库 `frontend`，AppID `wx08463b79dbeb2ba7`。
- 后端版本接口与发布记录均为 `advice-prefetch-20260923-1cad34c` / `1cad34c6baaeb307a8530f40a8b01c08b39fcb6f`。
- 本次仅前端布局，无后端部署、无业务数据写入；未上传小程序、未替换 1.0.6 体验版。手机验收另计。
