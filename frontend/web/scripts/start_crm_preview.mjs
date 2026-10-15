import {fileURLToPath} from 'node:url';
import {access} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const localPreviewPath = fileURLToPath(new URL('../.runtime/crm-preview.json', import.meta.url));
await access(localPreviewPath); // Missing CRM data must not silently open a synthetic workspace.
const port = Number(process.env.CRM_PREVIEW_PORT || 5196);
const server = createSalesWebServer({target: '', localPreviewPath});
server.listen(port, '127.0.0.1', () => console.log(`CRM 数据 · 本地预览：http://127.0.0.1:${port}/?mode=preview`));
