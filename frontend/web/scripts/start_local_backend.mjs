import { existsSync } from 'node:fs';
import { isAbsolute, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createSalesWebServer } from '../server.mjs';
import { buildOutput } from './build-output.mjs';

export const LOCAL_WEB_HOST = '127.0.0.1';
export const LOCAL_WEB_PORT = 5188;
export const LOCAL_API_TARGET = 'http://127.0.0.1:18800/api/v1';

// Keep this entry point independent of published connection settings and shell overrides.
export function localBackendOptions(localLogin, {output = 'dist'} = {}) {
  if (localLogin !== undefined && (
    !localLogin || localLogin.apiTarget !== LOCAL_API_TARGET || localLogin.role !== 'sales' ||
    typeof localLogin.accountCode !== 'string' || !localLogin.accountCode.trim() ||
    typeof localLogin.guidePath !== 'string' || !isAbsolute(localLogin.guidePath)
  )) {
    throw new Error('本地快捷登录配置无效：仅允许本地 18800 API、一线销售和私有账号说明的绝对路径。');
  }
  return {
    target: LOCAL_API_TARGET,
    staticDir: buildOutput(output),
    environment: '本地开发 · 离线数据快照',
    previewOnly: false,
    localLogin,
  };
}

export function startLocalBackendWeb({output = process.env.SALES_WEB_DIST_DIR || 'dist'} = {}) {
  // One LOCAL entry. A frozen build is an artifact, never a second user environment.
  // Use the original account login; do not silently load an old fixture login guide.
  const options = localBackendOptions(undefined, {output});
  if (!existsSync(join(options.staticDir, 'index.html'))) {
    throw new Error('Web 尚未构建，请先执行 npm run build，或执行 npm run dev:local。');
  }
  const server = createSalesWebServer(options);
  server.on('error', error => {
    console.error(error.code === 'EADDRINUSE'
      ? '本地 Web 端口 5188 已占用，请核对现有进程后再启动。'
      : '本地 Web 启动失败：' + error.code);
    process.exitCode = 1;
  });
  server.listen(LOCAL_WEB_PORT, LOCAL_WEB_HOST, () => {
    console.log('本地 Web：http://127.0.0.1:5188/?mode=live#/pages/login/index');
    console.log('固定本地 API：' + LOCAL_API_TARGET + '；Ctrl+C 停止 Web。');
  });
  return server;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    startLocalBackendWeb();
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
