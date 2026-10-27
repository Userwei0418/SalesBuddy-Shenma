// 产研部署时改为已在微信小程序后台登记的 HTTPS 请求域名。
// 示例：https://sales-api.example.com/api/v1
// 不要在此文件中填写 API Key、数据库口令或其他密钥。
module.exports = {
  API_BASE_URL: "https://salesbuddy.shenzhoukuntai.com:28899/api/v1",
  LOCK_API_BASE_URL: true,
  READONLY_WORKSPACE_CODE: "shenma-isolated-demo-20260929",
  READONLY_WORKSPACE_ID: "10b037a3-0d6a-4d63-ba69-f7ffb33dc474",
  // 客户分享 H5 的 Web origin；留空沿用 API 同域。LOCAL 构建器显式写入 Web 端口。
  // 仅允许 HTTP(S) origin，不含路径、账号密码、查询参数或 fragment。
  KNOWLEDGE_CUSTOMER_WEB_ORIGIN: "",
  // 神码未配置外部资料站。
  JING_SOCIAL_URL: "",
  // 一期首页为动态推送。二期启用主动问数时，再显式开放入口。
  HOME_CHATBI_ENABLED: false,
  // desc：最新动态在上；asc：旧上新下的连续会话。公司后台可覆盖此基线，重新进入首页应用。
  HOME_MESSAGE_ORDER: "desc",
};
