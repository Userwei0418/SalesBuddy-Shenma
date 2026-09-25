import {createSalesWebServer} from '../server.mjs';
const port = Number(process.env.PORT ?? 5188);
if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('PORT 必须是有效端口');
const host = process.env.HOST || '127.0.0.1';
const server = createSalesWebServer({previewOnly: true, environment: '部门组件库候选版 · 合成示例'});
server.on('error', error => {console.error(error.code === 'EADDRINUSE' ? `端口 ${port} 已占用，请设置其他 PORT 后重试。` : error.message); process.exitCode = 1;});
server.listen(port, host, () => console.log(`部门组件库候选版：http://${host}:${server.address().port}/?mode=preview#/pages/index/index\n仅使用合成示例，Ctrl+C 停止。`));
