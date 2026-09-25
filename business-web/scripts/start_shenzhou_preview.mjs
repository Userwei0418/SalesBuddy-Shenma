import {createSalesWebServer} from '../server.mjs';
const port=Number(process.env.PORT ?? 5191);
const server=createSalesWebServer({previewOnly:true,environment:'神舟数码 Web UI · 前端演示'});
server.on('error',error=>{console.error(error.message);process.exitCode=1;});
server.listen(port,'127.0.0.1',()=>console.log(`神舟数码 Web UI：http://127.0.0.1:${port}/?mode=preview#/pages/workbench/index`));
