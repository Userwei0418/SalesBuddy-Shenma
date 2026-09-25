import {createSalesWebServer} from '../server.mjs';

const port = Number(process.env.PORT ?? 5187);
const host = process.env.HOST || '127.0.0.1';
if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('PORT 必须是有效端口');
const server = createSalesWebServer({target: '', localLogin: null, previewOnly: true, environment: '交互共评 · 合成示例'});
server.on('error', error => {
  console.error(error.code === 'EADDRINUSE' ? `端口 ${port} 已占用，请设置其他 PORT 后重试。` : error.message);
  process.exitCode = 1;
});
server.listen(port, host, () => {
  const base = `http://${host}:${server.address().port}`;
  console.log(`示例页面：${base}/?mode=preview#/pages/index/index`);
  console.log(`设计规范：${base}/design-system/index.html`);
  console.log('真实后端和快捷登录已关闭；Ctrl+C 停止。');
});
