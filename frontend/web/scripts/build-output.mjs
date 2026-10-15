import {dirname, resolve, sep} from 'node:path';
import {fileURLToPath} from 'node:url';

export const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export function buildOutput(value = process.env.SALES_WEB_DIST_DIR || 'dist') {
  const output = resolve(webRoot, value);
  if (output !== resolve(webRoot, 'dist') && !output.startsWith(resolve(webRoot, '.runtime') + sep)) {
    throw Error('Candidate output must be a directory inside frontend/web/.runtime');
  }
  return output;
}
