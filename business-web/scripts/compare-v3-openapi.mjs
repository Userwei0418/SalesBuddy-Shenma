// Compare two local contract snapshots; never downloads a schema or sends business requests.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {contract} from './check-v3-contract.mjs';
const HTTP = new Set(['get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'trace']);
const DOCUMENTATION = new Set(['description', 'title', 'summary', 'example', 'examples', 'externalDocs', 'operationId', 'tags']);
const SET_ARRAYS = new Set(['required', 'enum', 'anyOf', 'allOf', 'oneOf']);
const MAP_CONTAINERS = new Set(['properties', 'patternProperties', 'dependentSchemas', 'definitions', '$defs', 'content', 'responses', 'headers']);
function literal(value) {return Array.isArray(value) ? value.map(literal) : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(k => [k, literal(value[k])])) : value;}
function semantic(value, key = '') {
  if (['default', 'const'].includes(key)) return literal(value);
  if (key === 'enum') return literal(value).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
  if (Array.isArray(value)) {const result = value.map(v => semantic(v)); return SET_ARRAYS.has(key) ? result.sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))) : result;}
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).filter(k => MAP_CONTAINERS.has(key) || !DOCUMENTATION.has(k)).sort().map(k => [k, semantic(value[k], MAP_CONTAINERS.has(key) ? '' : k)]));
  return value;
}
const stringify = value => JSON.stringify(semantic(value));
function operation(o) {
  const parameters = (o.parameters || []).map(p => ({...p, required: p.required === true})).sort((a, b) => `${a.in}:${a.name}`.localeCompare(`${b.in}:${b.name}`));
  const security = (o.security || []).map(item => Object.fromEntries(Object.entries(item).map(([name, scopes]) => [name, [...scopes].sort()]))).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
  return semantic({method: o.method, path: o.path, parameters, requestBody: o.requestBody ? {...o.requestBody, required: o.requestBody.required === true} : null, responses: o.responses, security});
}
function changes(before, after, location = '') {
  if (JSON.stringify(before) === JSON.stringify(after)) return [];
  if (before && after && !Array.isArray(before) && !Array.isArray(after) && typeof before === 'object' && typeof after === 'object') {
    return [...new Set([...Object.keys(before), ...Object.keys(after)])].sort().flatMap(key => changes(before[key], after[key], location ? location + '.' + key : key));
  }
  return [{field: location, package: before === undefined ? '[absent]' : before, live: after === undefined ? '[absent]' : after}];
}
export function compareSnapshots(pack, live) {
  const liveOperations = Object.entries(live.paths || {}).flatMap(([p, item]) => Object.entries(item).filter(([method]) => HTTP.has(method)).map(([method, o]) => ({...o, path: p, method: method.toUpperCase(),
    parameters: [...(item.parameters || []), ...(o.parameters || [])], security: o.security ?? live.security ?? []})));
  const packageOps = new Map(pack.operations.map(o => [`${o.method} ${o.path}`, operation(o)])), liveOps = new Map(liveOperations.map(o => [`${o.method} ${o.path}`, operation(o)]));
  const packageSchemas = pack.schemas, liveSchemas = live.components?.schemas || {};
  const missingOperations = [...packageOps.keys()].filter(k => !liveOps.has(k)).sort(), addedOperations = [...liveOps.keys()].filter(k => !packageOps.has(k)).sort();
  const changedOperations = [...packageOps.keys()].filter(k => liveOps.has(k) && stringify(packageOps.get(k)) !== stringify(liveOps.get(k))).map(k => ({operation: k, changes: changes(packageOps.get(k), liveOps.get(k))}));
  const missingSchemas = Object.keys(packageSchemas).filter(k => !Object.hasOwn(liveSchemas, k)).sort(), addedSchemas = Object.keys(liveSchemas).filter(k => !Object.hasOwn(packageSchemas, k)).sort();
  const changedSchemas = Object.keys(packageSchemas).filter(k => Object.hasOwn(liveSchemas, k) && stringify(packageSchemas[k]) !== stringify(liveSchemas[k])).map(k => ({schema: k, changes: changes(semantic(packageSchemas[k]), semantic(liveSchemas[k]))}));
  return {packageBaseline: pack.baseline, packageOperationCount: packageOps.size, liveOperationCount: liveOps.size, packageSchemaCount: Object.keys(packageSchemas).length, liveSchemaCount: Object.keys(liveSchemas).length,
    comparedAt: new Date().toISOString(), networkRequests: 0, equivalent: [missingOperations, addedOperations, changedOperations, missingSchemas, addedSchemas, changedSchemas].every(rows => rows.length === 0),
    missingOperations, addedOperations, changedOperations, missingSchemas, addedSchemas, changedSchemas,
    method: 'Compare METHOD/path, parameters, request bodies, responses, auth declarations and component schemas; retain required/nullability/default/enum/format/bounds/additionalProperties. Ignore documentation text, examples, operation IDs and tags; normalize set ordering. Does not compare route Python implementations or production data.'};
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const source = process.argv[2]; if (!source || source.startsWith('--')) throw Error('Provide a local OpenAPI JSON path');
  const bytes = fs.readFileSync(source), report = {...compareSnapshots(contract, JSON.parse(bytes)), liveSnapshotSha256: crypto.createHash('sha256').update(bytes).digest('hex')};
  const at = process.argv.indexOf('--output');
  if (at >= 0) {const output = path.resolve(process.argv[at + 1]); fs.mkdirSync(path.dirname(output), {recursive: true}); fs.writeFileSync(output, JSON.stringify(report, null, 2) + '\n');}
  console.log(JSON.stringify(report, null, 2)); process.exitCode = report.equivalent ? 0 : 1;
}
