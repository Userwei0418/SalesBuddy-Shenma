#!/usr/bin/env python3
"""Local-only stack. No production credentials, deployment, upload, or reset command."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_paths import ensure_private_directory, state_directory, state_from_config

LOCAL = state_directory(ROOT)
DB_PORT = 15432
API_PORT = 18800
BASE = f"http://127.0.0.1:{API_PORT}"
DB_NAME = "salesbuddy_local"
SCHEMA_DIRS = ("database/baseline/", "database/migrations/", "database/seeds/", "database/scripts/")


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def clean_env():
    # In particular do not inherit DATABASE_URL, PG*, model keys, proxy, or Agent settings.
    return {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR") if k in os.environ}


def write_json(path, data, private=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".new")
    with temporary.open("w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600 if private else 0o644)
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    temporary.replace(path)


def selected(name):
    p = Path(name)
    if any(x.startswith(".env") or x in {"__pycache__", "node_modules"} for x in p.parts):
        return False
    if p.name == "project.private.config.json" or p.suffix in {".key", ".pem", ".pyc", ".zip"}:
        return False
    return (name.startswith(("backend/src/", "backend/openapi/", "frontend/miniprogram/", "tools/local/", *SCHEMA_DIRS))
            or name in {"backend/pyproject.toml", "backend/uv.lock", "frontend/project.config.json", "frontend/VERSION.json",
                        "tools/installation/runtime_roles.py", "tools/installation/upgrade_permissions.py",
                        "tools/installation/runtime_permissions.json"}
            or (p.parent == Path("database") and p.suffix == ".sql"))


def source_files(root=ROOT):
    raw = run(["git", "-C", root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"], capture_output=True).stdout
    names = sorted({x.decode() for x in raw.split(b"\0") if x and selected(x.decode())})
    files = {}
    for name in names:
        path = root / name
        if path.is_symlink():
            raise RuntimeError(f"Source symlink is not accepted: {name}")
        if path.is_file():  # A tracked deletion must be reflected in the new build.
            if sys.platform == 'darwin' and getattr(path.stat(), 'st_flags', 0) & 0x40000000:
                raise RuntimeError(f'Source is an iCloud placeholder: {name}. Download it in Finder, or commit changes and use up --committed')
            files[name] = path.read_bytes()
    return files


def committed_source_files(root=ROOT):
    """Explicit, reproducible input; never silently replace unreadable worktree edits."""
    def git(*args, **kwargs):
        try:
            return run(['git', '-C', root, *args], capture_output=True, timeout=60, **kwargs).stdout
        except subprocess.TimeoutExpired:
            raise RuntimeError('Git source read timed out; make this checkout available locally before rebuilding') from None

    revision = git('rev-parse', 'HEAD').decode().strip()
    changes = git('diff', '--name-only', '-z', 'HEAD') + git('ls-files', '--others', '--exclude-standard', '-z')
    if any(selected(name.decode()) for name in changes.split(b'\0') if name):
        raise RuntimeError('Committed build requires all runtime source changes committed; no edits were discarded')
    entries = []
    for entry in git('ls-tree', '-r', '-z', revision).split(b'\0'):
        if not entry:
            continue
        info, name = entry.split(b'\t', 1)
        mode, kind, oid = info.split()
        if selected(name.decode()):
            if kind != b'blob' or mode not in (b'100644', b'100755'):
                raise RuntimeError('Committed runtime input must be a regular file: ' + name.decode())
            entries.append((name.decode(), oid))
    payload = b''.join(oid + b'\n' for _, oid in entries)
    stream = io.BytesIO(git('cat-file', '--batch', input=payload))
    files = {}
    for name, expected in entries:
        oid, kind, size = stream.readline().strip().split()
        if oid != expected or kind != b'blob':
            raise RuntimeError('Git source object differs from the selected commit')
        content = stream.read(int(size))
        if len(content) != int(size) or stream.read(1) != b'\n':
            raise RuntimeError('Incomplete Git source object')
        files[name] = content
    changed_after = git('diff', '--name-only', '-z', 'HEAD') + git('ls-files', '--others', '--exclude-standard', '-z')
    if (stream.read() or git('rev-parse', 'HEAD').decode().strip() != revision
            or any(selected(name.decode()) for name in changed_after.split(b'\0') if name)):
        raise RuntimeError('Source revision or runtime files changed during the build')
    return files


def fingerprint(files):
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode() + b"\0" + hashlib.sha256(content).digest())
    return digest.hexdigest()


def frontend_fingerprint(front):
    files = {str(p.relative_to(front)): p.read_bytes() for p in front.rglob('*') if p.is_file()
             and ('miniprogram' in p.relative_to(front).parts or p.name == 'project.config.json')}
    project = json.loads(files['project.config.json'])
    # The IDE inserts these exact default compiler settings on first import and
    # reformats JSON. Accept that normalization, but not changed compiler values,
    # AppID, source files, API URL, or any other project setting.
    defaults = {'useIsolateContext': True, 'useCompilerModule': True,
                'userConfirmedUseCompilerModuleSwitch': False, 'userConfirmedBundleSwitch': False,
                'packNpmManually': False, 'packNpmRelationList': [], 'minifyWXSS': True,
                'compileWorklet': False, 'minifyWXML': True, 'localPlugins': False,
                'disableUseStrict': False, 'useCompilerPlugins': False, 'condition': False,
                'swc': False, 'disableSWC': True}
    for key, value in defaults.items():
        if project.get('setting', {}).get(key) == value:
            project['setting'].pop(key)
    files['project.config.json'] = json.dumps(project, sort_keys=True).encode()
    return fingerprint(files)


def config(create=False):
    if not LOCAL.exists() and not create:
        raise RuntimeError('Local environment is not initialized; run dev.py up')
    if create and not LOCAL.exists() and (ROOT / '.local/settings.json').exists():
        raise RuntimeError('Existing local data requires relocation first; run dev.py relocate')
    if create:
        ensure_private_directory(LOCAL)
    if LOCAL.is_symlink():
        raise RuntimeError(".local must not be a symlink")
    if create:
        os.chmod(LOCAL, 0o700)
    path = LOCAL / "settings.json"
    if not path.exists():
        if not create:
            raise RuntimeError('Local environment settings are missing; run dev.py up')
        write_json(path, {"kind": "salesbuddy-local-v1", "root": str(ROOT), "state_root": str(LOCAL),
                         "db_port": DB_PORT, "api_port": API_PORT,
                         "owner_password": secrets.token_hex(24), "api_password": secrets.token_hex(24),
                         "worker_password": secrets.token_hex(24), "token_secret": secrets.token_hex(48),
                         "test_password": "Local-" + secrets.token_hex(8)}, private=True)
    value = json.loads(path.read_text())
    if (value.get("kind") != "salesbuddy-local-v1" or not Path(value.get("root", "")).is_absolute()
            or Path(value["root"]).resolve() != ROOT):
        raise RuntimeError("Unrecognized local environment; do not reuse or overwrite it")
    if state_from_config(value) != LOCAL:
        raise RuntimeError('Local state does not match this checkout')
    if (value["db_port"], value["api_port"]) != (DB_PORT, API_PORT):
        raise RuntimeError("Unexpected local port settings")
    if create and 'platform_totp_seed' in value and not (LOCAL / 'platform-mfa-keys.json').exists():
        raise RuntimeError('Local platform keyring missing; restore the original private keyring before restarting')
    if create and 'platform_totp_seed' not in value:
        value['platform_totp_seed'] = base64.b32encode(secrets.token_bytes(20)).decode()
        write_json(path, value, private=True)
    if create and not (LOCAL / 'platform-mfa-keys.json').exists():
        write_json(LOCAL / 'platform-mfa-keys.json', {'keys': {'local-v1': base64.b64encode(secrets.token_bytes(32)).decode()}}, private=True)
    if create and 'platform_password' not in value:
        value['platform_password'] = secrets.token_hex(24)
        write_json(path, value, private=True)
    return value


def pg_bin():
    candidates = [Path("/opt/homebrew/opt/postgresql@16/bin"), Path("/usr/local/opt/postgresql@16/bin"),
                  Path("/usr/lib/postgresql/16/bin")]
    for folder in candidates:
        if (folder / "pg_ctl").exists():
            return folder
    raise RuntimeError("Install PostgreSQL 16 (Homebrew postgresql@16 or the PostgreSQL 16 OS package)")


def python():
    value = LOCAL / "venv/bin/python"
    if not value.exists():
        raise RuntimeError("Run dev.py up to prepare the isolated, locked Python runtime")
    return value


def prepare_python():
    expected = fingerprint({name: (ROOT / name).read_bytes() for name in ('backend/uv.lock', 'backend/pyproject.toml')})
    receipt = LOCAL / 'dependencies.json'
    if (LOCAL / 'venv/bin/python').exists() and receipt.exists() and json.loads(receipt.read_text()).get('lock') == expected:
        return
    env = clean_env()
    env['UV_PROJECT_ENVIRONMENT'] = str(LOCAL / 'venv')
    run(['uv', 'sync', '--locked', '--extra', 'dev', '--python', '3.12', '--project', ROOT / 'backend'], env=env)
    write_json(receipt, {'lock': expected, 'runtime': str(LOCAL / 'venv')})


def port_free(port):
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(f"Port {port} is occupied; existing services were not stopped") from exc


def postgres_up(cfg):
    binary = pg_bin()
    data = LOCAL / "postgres"
    if data.is_symlink():
        raise RuntimeError("Local database path must not be a symlink")
    if not data.exists():
        password = LOCAL / "owner-password"
        password.write_text(cfg["owner_password"])
        password.chmod(0o600)
        try:
            run([binary / "initdb", "-D", data, "-U", "salesbuddy_local_owner", "--pwfile", password,
                 "--auth-host=scram-sha-256", "--auth-local=scram-sha-256", "--encoding=UTF8", "--locale=C"],
                env=clean_env(), stdout=subprocess.DEVNULL)
        finally:
            password.unlink(missing_ok=True)
    status = subprocess.run([str(binary / "pg_ctl"), "-D", str(data), "status"], capture_output=True)
    if status.returncode:
        port_free(DB_PORT)
        run([binary / "pg_ctl", "-D", data, "-l", LOCAL / "postgres.log", "-o",
             f"-h 127.0.0.1 -p {DB_PORT} -c unix_socket_directories=''", "-w", "start"], env=clean_env())


def configure_test_appid(cfg, appid):
    production = json.loads((ROOT / 'frontend/project.config.json').read_text())['appid']
    if not re.fullmatch(r'wx[a-f0-9]{16}', appid) or appid == production:
        raise RuntimeError('Use a real development/test AppID, never the production AppID')
    cfg['development_appid'] = appid
    write_json(LOCAL / 'settings.json', cfg, private=True)


def build(committed=False, files=None):
    if files is None:
        files = committed_source_files() if committed else source_files()
    identifier = fingerprint(files)
    target = LOCAL / "builds" / identifier
    manifest = {"environment": "local", "build_id": identifier,
                "source_mode": "commit" if committed else "worktree",
                "revision": run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True).stdout.decode().strip(),
                "components": {part: fingerprint({n: b for n, b in files.items() if n.startswith(part + "/")})
                               for part in ("frontend", "backend", "database")}}
    snapshot = dict(files)
    for admin_name in ('backend/src/sales_backend/web/admin.html', 'backend/src/sales_backend/web/platform.html', 'backend/src/sales_backend/web/company-activate.html'):
        html = snapshot[admin_name].decode().replace('<title>', '<title>[LOCAL] ', 1)
        html = html.replace('<body>', '<body><div style="position:fixed;bottom:10px;left:50%;transform:translateX(-50%);'
                            'z-index:9999;background:#fff3cd;color:#6b5100;padding:6px 16px;border:1px solid #c99f27;'
                            'border-radius:8px;font-size:13px;pointer-events:none">'
                            '本地验收 · 合成数据 · AI / 飞书未接入</div>', 1)
        snapshot[admin_name] = html.encode()
    manifest['runtime_snapshot_sha256'] = fingerprint(snapshot)
    if target.exists():
        actual = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob('*')
                  if p.is_file() and selected(str(p.relative_to(target)))}
        if fingerprint(actual) != manifest['runtime_snapshot_sha256']:
            raise RuntimeError('Existing snapshot was modified; retain it for review instead of silently accepting it')
    else:
        for name, data in snapshot.items():
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
    write_json(target / "LOCAL_BUILD.json", manifest)
    # Native mini-program build artifact, never a second editable checkout.
    front = LOCAL / "frontend"
    if front.is_symlink():
        raise RuntimeError("Generated frontend must not be a symlink")
    if front.exists() and not (front / "LOCAL_BUILD.json").exists():
        raise RuntimeError("Refusing to overwrite an unmarked frontend directory")
    front.mkdir(exist_ok=True)
    if (front / "miniprogram").exists():
        shutil.rmtree(front / "miniprogram")
    shutil.copytree(target / "frontend/miniprogram", front / "miniprogram")
    config_file = front / "miniprogram/config.js"
    original = config_file.read_text()
    replaced, count = re.subn(r'(API_BASE_URL\s*:\s*)[\"\'][^\"\']+[\"\']',
                             lambda match: match[1] + json.dumps(BASE + "/api/v1"), original)
    if count != 1:
        raise RuntimeError("Cannot identify the one API_BASE_URL; build failed closed")
    config_file.write_text(replaced)
    project = json.loads((target / "frontend/project.config.json").read_text())
    project.update(appid=config().get('development_appid') or 'touristappid', projectname="SalesBuddy-LOCAL-本地验收")
    project.setdefault("setting", {})["urlCheck"] = False
    write_json(front / "project.config.json", project)
    app_file = front / "miniprogram/app.json"
    app = json.loads(app_file.read_text())
    app.setdefault("window", {})["navigationBarTitleText"] = "SalesBuddy 本地验收"
    write_json(app_file, app)
    manifest["generated_frontend_sha256"] = frontend_fingerprint(front)
    write_json(front / "LOCAL_BUILD.json", manifest)
    write_json(LOCAL / "build.json", manifest)
    return target, manifest


def stop_apps(local=None):
    local = local or LOCAL
    for service in ("api", "worker"):
        path = local / (service + ".pid.json")
        if not path.exists():
            continue
        entry = json.loads(path.read_text())
        result = subprocess.run(["ps", "-p", str(entry["pid"]), "-o", "command="], capture_output=True, text=True)
        if result.returncode == 0:
            command = result.stdout
            state = subprocess.run(["ps", "-p", str(entry["pid"]), "-o", "stat="], capture_output=True, text=True)
            if state.stdout.strip().startswith("Z"):
                path.unlink()  # Already exited; no signal is sent to a stale PID.
                continue
            if entry["script"] not in command or str(local / "builds") not in entry["script"]:
                raise RuntimeError(f"{service} PID belongs to another process; not stopped")
            os.kill(entry["pid"], signal.SIGTERM)
            for _ in range(80):
                try:
                    os.kill(entry["pid"], 0)
                except ProcessLookupError:
                    break
                state = subprocess.run(["ps", "-p", str(entry["pid"]), "-o", "stat="], capture_output=True, text=True)
                if state.returncode or state.stdout.strip().startswith("Z"):
                    break
                time.sleep(.1)
            else:
                raise RuntimeError(f"{service} did not stop; inspect local logs")
        path.unlink()


def fetch(path):
    # Loopback checks must keep working when the user's system proxy is stopped.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(BASE + path, timeout=5) as response:
        return json.load(response)


def local_ready():
    try:
        state = fetch('/api/v1/health/ready')
    except urllib.error.HTTPError as exc:
        if exc.code != 503:
            raise
        state = json.load(exc)
    checks = state.get('checks', {})
    if not checks.get('database') or not checks.get('auth_configured'):
        raise RuntimeError('Local database or authentication is not ready')
    return state


def doctor(*, require_current_source=True):
    cfg = config()
    expected = json.loads((LOCAL / "build.json").read_text())
    # A pre-upgrade backup protects the verified running build, even after the
    # checkout advances. Acceptance always also requires current source parity.
    if require_current_source:
        if expected.get('source_mode') == 'commit':
            if run(['git', '-C', ROOT, 'rev-parse', 'HEAD'], capture_output=True).stdout.decode().strip() != expected['revision']:
                raise RuntimeError('Committed source revision changed; rebuild before acceptance')
            files = committed_source_files()
        else:
            files = source_files()
        if fingerprint(files) != expected["build_id"]:
            raise RuntimeError("Source changed since the local build. Run dev.py up before acceptance")
    live = fetch("/api/v1/health/version")
    if live.get("local_build_id") != expected["build_id"] or live.get("mode") != "development":
        raise RuntimeError("Running backend does not match the local frontend/source build")
    front = LOCAL / "frontend"
    if json.loads((front / 'project.config.json').read_text())['appid'] != (cfg.get('development_appid') or 'touristappid'):
        raise RuntimeError('Development AppID changed; run dev.py up to rebuild the frontend')
    actual = frontend_fingerprint(front)
    if actual != expected["generated_frontend_sha256"]:
        raise RuntimeError("Generated frontend changed; edit source frontend/ and rebuild")
    target = LOCAL / "builds" / expected["build_id"]
    actual_snapshot = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob('*')
                       if p.is_file() and selected(str(p.relative_to(target)))}
    if fingerprint(actual_snapshot) != expected['runtime_snapshot_sha256']:
        raise RuntimeError('Running snapshot was modified; rebuild from source')
    for service in ('api', 'worker'):
        entry = json.loads((LOCAL / (service + '.pid.json')).read_text())
        state = subprocess.run(['ps', '-p', str(entry['pid']), '-o', 'command='], capture_output=True, text=True)
        if state.returncode or entry['script'] not in state.stdout or entry['build'] != expected['build_id']:
            raise RuntimeError(service + ' is absent or running another build')
    run([python(), target / "tools/local/bootstrap.py", "verify", LOCAL / "settings.json"],
        env=clean_env(), cwd=target)
    readiness = local_ready()
    print(json.dumps({"status": "matched" if require_current_source else "running_build_verified",
                      "current_source_checked": require_current_source,
                      "build": expected["build_id"], "api": BASE,
                      "frontend": str(front), "readiness": readiness,
                      "production_changed": False}, ensure_ascii=False, indent=2))
    return cfg


def up(committed=False):
    # Unreadable or ambiguous inputs must not take a healthy local service down.
    files = committed_source_files() if committed else source_files()
    cfg = config(create=True)
    stop_apps()
    prepare_python()
    port_free(API_PORT)
    postgres_up(cfg)
    target, manifest = build(committed=committed, files=files)
    run([python(), target / "tools/local/bootstrap.py", "init", LOCAL / "settings.json"], env=clean_env(), cwd=target)
    for service in ("api", "worker"):
        script = target / "tools/local/run_service.py"
        with (LOCAL / (service + ".log")).open("a") as logfile:
            process = subprocess.Popen([str(python()), str(script), service, str(LOCAL / "settings.json")],
                                       cwd=target, env=clean_env(), stdout=logfile, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        write_json(LOCAL / (service + ".pid.json"), {"pid": process.pid, "script": str(script), "build": manifest["build_id"]})
    for _ in range(90):
        try:
            local_ready()
            break
        except (OSError, ValueError, RuntimeError):
            time.sleep(1)
    else:
        raise RuntimeError("Local API was not ready after 90s; inspect the local state api.log shown by dev.py paths")
    doctor()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("up", "check", "verify", "down", "open", "configure", "relocate", "paths", "code", "backup", "restore-check", "verify-recovery"))
    parser.add_argument('--test-appid', help='AppID obtained from WeChat development tools test-account UI')
    parser.add_argument('--committed', action='store_true', help='Build exactly HEAD; rejects uncommitted runtime source')
    parser.add_argument('--backup', help='Private backup directory for restore-check; omitted creates a fresh backup')
    args = parser.parse_args()
    command = args.command
    if args.backup and command != 'restore-check':
        parser.error('--backup is supported only with restore-check')
    if args.committed and command != 'up':
        parser.error('--committed is supported only with up')
    if command == 'verify-recovery':
        doctor()
        run([python(), ROOT / 'tools/local/check_recovery.py'], env=clean_env())
        return
    if command in {'backup', 'restore-check'}:
        doctor(require_current_source=False)
        extra = ['--backup', args.backup] if args.backup else []
        run([python(), ROOT / 'tools/local/recovery.py', command, *extra], env=clean_env())
        return
    if command == 'paths':
        print(json.dumps({'source':str(ROOT), 'state':str(LOCAL), 'frontend':str(LOCAL / 'frontend'),
                          'accounts':str(LOCAL / 'TEST_ACCOUNTS.md'),
                          'relocation_required':not LOCAL.exists() and (ROOT / '.local/settings.json').exists()},
                         ensure_ascii=False, indent=2))
        return
    if command == 'relocate':
        from relocate import relocate
        relocate(sys.modules[__name__])
        return
    if command == 'code':
        doctor()
        manifest = json.loads((LOCAL / 'build.json').read_text())
        run([python(), LOCAL / 'builds' / manifest['build_id'] / 'tools/local/platform_code.py', LOCAL / 'settings.json'], env=clean_env())
        return
    if command == 'configure':
        if not args.test_appid:
            parser.error('configure requires --test-appid')
        configure_test_appid(config(create=True), args.test_appid)
        print('Local test AppID configured; run dev.py up to rebuild')
        return
    if command == "up":
        up(committed=args.committed)
    elif command in {"check", "verify", "open"}:
        doctor()
        if command == "verify":
            run([python(), ROOT / "tools/local/smoke.py", LOCAL / "settings.json"], env=clean_env())
        elif command == "open":
            if not config().get('development_appid'):
                raise RuntimeError('Choose a test AppID in WeChat tools, then run configure --test-appid before opening')
            cli = Path("/Applications/wechatwebdevtools.app/Contents/MacOS/cli")
            result = run([cli, "open", "--project", LOCAL / "frontend"], env=clean_env(), capture_output=True, text=True)
            print(result.stdout)
            print(result.stderr, file=sys.stderr)
            if '[error]' in result.stdout + result.stderr or '✖' in result.stdout + result.stderr:
                raise RuntimeError('WeChat tools rejected the project; open is not complete')
    elif command == "down":
        config()
        stop_apps()
        run([pg_bin() / "pg_ctl", "-D", LOCAL / "postgres", "-m", "fast", "-w", "stop"], env=clean_env())
        print("Local services stopped; local data retained; no other services affected")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        # Child errors already go to local logs. Never dump environment or local credentials.
        print(str(exc) if not isinstance(exc, subprocess.CalledProcessError) else "Local command failed; inspect local logs", file=sys.stderr)
        sys.exit(1)
