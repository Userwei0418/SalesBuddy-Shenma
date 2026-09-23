// 产研部署时改为已在微信小程序后台登记的 HTTPS 请求域名。
// 示例：https://sales-api.example.com/api/v1
// 不要在此文件中填写 API Key、数据库口令或其他密钥。
module.exports = {
  API_BASE_URL: "https://salesbuddy.shenzhoukuntai.com:28899/api/v1",
  // 一期首页为动态推送。二期启用主动问数时，再显式开放入口。
  HOME_CHATBI_ENABLED: false,
  // desc：最新动态在上；asc：旧上新下的连续会话。公司后台可覆盖此基线，重新进入首页应用。
  HOME_MESSAGE_ORDER: "desc",
};
