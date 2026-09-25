#!/usr/bin/env python3
"""Prepare a light, credential-free SalesBuddy candidate distribution. Does not build application code."""
from pathlib import Path, PurePosixPath
import argparse, datetime, hashlib, http.server, json, os, re, shutil, socket, subprocess, tempfile, threading, time, urllib.error, urllib.parse, urllib.request, zipfile, zlib

p=argparse.ArgumentParser()
p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
p.add_argument('--output',type=Path)
p.add_argument('--title',default='SalesBuddy-Web-部门设计候选版-20260920')
p.add_argument('--create',action='store_true')
p.add_argument('--extra',action='append',default=[])
a=p.parse_args(); root=a.root.resolve()
assert a.title not in ('','.','..') and '/' not in a.title and '\\' not in a.title
excluded={'.git','.runtime','node_modules','test-results','playwright-report','__pycache__','.cache','.private','__MACOSX'}
excluded_files={'scripts/package_web.py','scripts/package_crm_handoff.py','scripts/prepare_crm_full.py','scripts/prepare_crm_samples.py','scripts/verify_crm_samples.py'}
allowed_dirs={'assets','source','department-ui','scripts','tests','design-system','docs','.github','.claude'}
text_extensions={'.md','.js','.mjs','.jsx','.json','.html','.py','.command','.css','.wxml','.wxss','.txt','.csv','.yml','.yaml','.sh'}

def acceptable(rel):
    if any(part in excluded or part.startswith('.env') for part in rel.parts): return False
    if rel.as_posix() in excluded_files: return False
    if rel.name in {'settings.local.json','.DS_Store','local-login.json','CHECKSUMS.sha256'} or rel.name.startswith('._'): return False
    if rel.suffix.lower() in {'.zip','.log','.pyc','.pem','.key','.xlsx','.xls','.db','.sqlite','.sqlite3','.har'}: return False
    if rel.parts[0]=='docs' and (len(rel.parts)<2 or rel.parts[1]!='cloud'): return False
    if rel.parts[0]=='docs' and rel.suffix.lower() in {'.png','.jpg','.jpeg','.webp','.gif','.svg'}: return False
    return True

def digest(data): return hashlib.sha256(data).hexdigest()
def secure(rel,data):
    if rel.suffix in text_extensions:
        assert (b'-----BEGIN '+b'PRIVATE KEY-----') not in data, 'Private key material: '+str(rel)
        assert not re.search(rb'eyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{20,}',data), 'Possible credential material: '+str(rel)

def sanitize_docs(rel,data):
    if rel.parts[0]=='docs' and rel.suffix=='.md':
        content=data.decode('utf-8')
        content=re.sub(r'!\[([^\]]*)\]\((?:ui-review-assets|department-design-images)/[^)]*\)',r'（\1：历史截图未随本次轻量包分发，请以运行页面为准。）',content)
        return content.encode('utf-8')
    return data

previous_delivery={}
try:
    git_root=Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],cwd=root,stderr=subprocess.DEVNULL,text=True).strip()).resolve()
    has_git=git_root==root
except (subprocess.CalledProcessError,FileNotFoundError):
    has_git=False
if has_git:
    tracked=subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    branch=subprocess.check_output(['git','branch','--show-current'],cwd=root,text=True).strip()
    dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True).strip())
else:
    identity=root/'DELIVERY.json'
    assert identity.is_file(), 'Run from the source Git repository or an unpacked delivery with DELIVERY.json'
    previous_delivery=json.loads(identity.read_text())
    tracked=previous_delivery.get('source_files')
    assert isinstance(tracked,list) and tracked and all(isinstance(name,str) for name in tracked), 'Delivery has no reusable source inventory'
    assert all(not Path(name).is_absolute() and '..' not in Path(name).parts and not (root/name).is_symlink() for name in tracked), 'Unsafe delivery source inventory'
    commit=previous_delivery.get('source_commit')
    branch=previous_delivery.get('source_branch')
    checks=root/'CHECKSUMS.sha256'
    assert checks.is_file(), 'Unpacked delivery is missing checksums'
    baseline={}
    for line in checks.read_text().splitlines():
        checksum,separator,name=line.partition('  ')
        assert separator and re.fullmatch('[0-9a-f]{64}',checksum), 'Invalid checksum entry'
        baseline[name]=checksum
    dirty=any(not (root/name).is_file() or digest((root/name).read_bytes())!=baseline.get(name) for name in tracked) or bool(a.extra)

selected={Path(name) for name in tracked+a.extra if name and acceptable(Path(name))}
for rel in selected:
    assert not rel.is_absolute() and '..' not in rel.parts
    assert len(rel.parts)==1 or rel.parts[0] in allowed_dirs, 'Unreviewed source directory: '+str(rel)
    assert (root/rel).is_file() and not (root/rel).is_symlink(), 'Missing or linked source: '+str(rel)
required=('connection.config.json','server.mjs','local-login.mjs','local-preview.mjs','package.json','package-lock.json','scripts/start_department_preview.mjs','scripts/build.py','scripts/build-department-ui.mjs','source/manifest.json','department-ui/Customers.jsx','scripts/package_department.py','docs/cloud/delivery-readme.md','启动小浣熊SalesBuddy.command')
assert all(Path(f) in selected for f in required), 'Missing runtime or developer input'
assert (root/'dist/department-ui/app.js').is_file() and (root/'dist/department-ui/app.css').is_file(), 'Build first with npm run build'
for file in (root/'dist').rglob('*'):
    if file.is_file():
        rel=file.relative_to(root); assert acceptable(rel) and not file.is_symlink(), 'Unexpected dist input: '+str(rel)
        selected.add(rel)
files={}; source_hashes={}
for rel in sorted(selected):
    data=(root/rel).read_bytes(); secure(rel,data)
    source_hashes[rel.as_posix()]=digest(data)
    files[rel.as_posix()]=sanitize_docs(rel,data)
for rel in selected:
    if len(rel.parts)==1 and rel.suffix in {'.js','.css','.html'} and 'dist/'+rel.name in files:
        assert files[rel.as_posix()]==files['dist/'+rel.name], 'Unbuilt source: '+str(rel)
config=json.loads(files['connection.config.json'])
assert config.get('apiTarget'), 'The configured enterprise API must be retained'
for key in ('apiTarget','adminUrl'):
    if config.get(key):
        address=urllib.parse.urlsplit(config[key])
        assert address.scheme in ('http','https') and not address.username and not address.password and not address.query and not address.fragment, 'Sensitive or invalid endpoint configuration'

files['交付说明.md']=(root/'docs/cloud/delivery-readme.md').read_bytes()
manifest={'title':a.title,'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source_commit':commit,'source_branch':branch,'source_inventory_origin':'git' if has_git else 'delivery-manifest','source_files':sorted(name for name in files if not name.startswith('dist/')),'uncommitted_changes_in_snapshot':dirty,'web_baseline':json.loads((root/'package.json').read_text())['version'],'business_baseline':'Mini Program 1.0.6','distribution':'source + built dist','node_requirement':'>=22','server_requires_node_modules':False,'backend_configuration':'tracked non-secret enterprise configuration retained; credentials excluded','credentials_included':False,'real_crm_data_included':False,'production_acceptance_claimed':False,'dist_sha256':{name:digest(data) for name,data in files.items() if name.startswith('dist/')}}
files['DELIVERY.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
files['CHECKSUMS.sha256']=''.join(digest(data)+'  '+name+'\n' for name,data in sorted(files.items())).encode()
estimate=sum(len(zlib.compress(data,9))+len(name.encode('utf-8'))*2+80 for name,data in files.items())
print(json.dumps({'stage':'estimate','files':len(files),'uncompressed_mb':round(sum(len(d) for d in files.values())/1000000,2),'estimated_zip_mb':round(estimate/1000000,2),'snapshot_has_uncommitted_changes':dirty},ensure_ascii=False),flush=True)
if not a.create: raise SystemExit(0)
assert a.output, '--output is required with --create'
a.output.mkdir(parents=True,exist_ok=True)
archive=a.output/(a.title+'.zip'); assert not archive.exists(), 'Choose a new title; do not overwrite delivery'

def assert_root_unchanged():
    assert all(digest((root/name).read_bytes())==sha for name,sha in source_hashes.items()), 'Sources changed during packaging; wait until edits and build finish'

def smoke(stage,preview,stub_url):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    env={k:v for k,v in os.environ.items() if k not in ('SALES_WEB_API_TARGET','PORT','HOST')}
    env.update({'PORT':str(port),'HOST':'127.0.0.1','SALES_WEB_API_TARGET':stub_url})
    cmd=['node','scripts/start_department_preview.mjs' if preview else 'server.mjs']
    process=subprocess.Popen(cmd,cwd=stage,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    base='http://127.0.0.1:'+str(port)
    try:
        for _ in range(100):
            if process.poll() is not None: raise AssertionError('Node startup failed: '+process.stderr.read().decode()[:500])
            try:
                with urllib.request.urlopen(base+'/health',timeout=.5) as r: health=json.load(r)
                break
            except (urllib.error.URLError,TimeoutError): time.sleep(.05)
        else: raise AssertionError('Node startup timed out')
        assert health['configured'] is (not preview)
        with urllib.request.urlopen(base+'/web-capabilities',timeout=2) as r: assert json.load(r)['previewOnly'] is preview
        for asset in ('index.html','department-ui/app.js','department-ui/app.css','bundle.js'):
            with urllib.request.urlopen(base+'/'+asset,timeout=2) as r: assert digest(r.read())==digest(files['dist/'+asset])
        with urllib.request.urlopen(base+'/connection-status',timeout=2) as r:
            state=json.load(r); assert state['configured'] is (not preview) and state['reachable'] is (not preview)
        if not preview:
            with urllib.request.urlopen(base+'/api/v1/package-probe',timeout=2) as r:
                assert json.load(r)=={'status':'package_probe_ok'}
        return {'mode':'preview' if preview else 'enterprise_with_local_stub','started_without_node_modules':True,'assets_verified':4,'local_proxy_verified':not preview,'real_backend_calls_made':False}
    finally:
        process.terminate()
        try: process.communicate(timeout=5)
        except subprocess.TimeoutExpired: process.kill(); process.communicate()

class PackageProbeHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path=='/api/v1/health/ready':
            data={'status':'ok','checks':{'database':True,'model_gateway_configured':True,'auth_configured':True}}
        elif self.path=='/api/v1/package-probe':
            data={'status':'package_probe_ok'}
        else:
            self.send_error(404); return
        body=json.dumps(data).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self,*args): pass

with tempfile.TemporaryDirectory(prefix='sales-department-delivery-') as tmp:
    workspace=Path(tmp); stage=workspace/a.title; stage.mkdir()
    for name,data in files.items():
        dst=stage/name; dst.parent.mkdir(parents=True,exist_ok=True); dst.write_bytes(data)
        if name.endswith(('.command','.sh')): dst.chmod(0o755)
    assert not (stage/'node_modules').exists() and not (stage/'.git').exists() and not (stage/'.runtime').exists()
    stub=http.server.ThreadingHTTPServer(('127.0.0.1',0),PackageProbeHandler)
    thread=threading.Thread(target=stub.serve_forever,daemon=True); thread.start()
    try:
        stub_url='http://127.0.0.1:'+str(stub.server_address[1])+'/api/v1'
        tests=[smoke(stage,True,stub_url),smoke(stage,False,stub_url)]
    finally:
        stub.shutdown(); stub.server_close(); thread.join(timeout=3)
    assert_root_unchanged()
    temporary=workspace/(a.title+'.zip')
    with zipfile.ZipFile(temporary,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name in sorted(files): z.write(stage/name,a.title+'/'+name)
    with zipfile.ZipFile(temporary) as z:
        assert z.testzip() is None
        assert len(z.infolist())==len(files)
        for info in z.infolist():
            rel=PurePosixPath(info.filename); assert not rel.is_absolute() and '..' not in rel.parts
            name='/'.join(rel.parts[1:]); assert digest(z.read(info))==digest(files[name])
    assert_root_unchanged(); shutil.copy2(temporary,archive)
    result={'artifact':archive.name,'bytes':archive.stat().st_size,'files':len(files),'sha256':digest(archive.read_bytes()),'zip_crc_ok':True,'checksums_verified':len(files)-1,'source_commit':commit,'source_dirty':dirty,'node_startup_checks':tests,'production_acceptance_claimed':False}
    archive.with_suffix('.zip.sha256').write_text(result['sha256']+'  '+archive.name+'\n')
    (a.output/(a.title+'-打包验证.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
