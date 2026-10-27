import {existsSync} from 'node:fs';
import {join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createSalesWebServer} from '../server.mjs';
import {buildOutput} from './build-output.mjs';

export function candidateOptions({apiPort = 18810, output = '.runtime/auth001-dist'} = {}) {
  if (!Number.isInteger(apiPort) || apiPort < 1024 || apiPort > 65535 || apiPort === 18800) throw Error('候选后端须使用独立本地端口');
  const staticDir = buildOutput(output);
  if (staticDir === buildOutput('dist')) throw Error('候选页面须使用独立构建目录');
  return {target: `http://127.0.0.1:${apiPort}/api/v1`, staticDir, environment: '本地权限改造候选', previewOnly: false};
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const options = candidateOptions({apiPort: Number(process.env.SALES_WEB_CANDIDATE_API_PORT || 18810), output: process.env.SALES_WEB_DIST_DIR || '.runtime/auth001-dist'});
  if (!existsSync(join(options.staticDir, 'index.html'))) throw Error('请先构建候选页面');
  const server = createSalesWebServer(options);
  server.listen(5190, '127.0.0.1', () => console.log('候选 Web：http://127.0.0.1:5190/?mode=live；后端：' + options.target));
}
