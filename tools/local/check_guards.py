"""Exercise version rejection without modifying canonical source or any production state."""
import json
from pathlib import Path
import subprocess
import sys
from local_paths import state_directory

ROOT = Path(__file__).resolve().parents[2]
LOCAL = state_directory(ROOT)


def rejected(path, change, message):
    before = path.read_bytes()
    try:
        path.write_bytes(change(before))
        result = subprocess.run([sys.executable, str(ROOT / 'tools/local/dev.py'), 'check'],
                                capture_output=True, text=True, timeout=60)
        assert result.returncode != 0 and message in result.stderr, result.stderr
    finally:
        path.write_bytes(before)


def main():
    manifest = json.loads((LOCAL / 'build.json').read_text())
    rejected(LOCAL / 'frontend/miniprogram/config.js', lambda b: b + b'\n// mismatch\n', 'Generated frontend changed')
    rejected(LOCAL / 'builds' / manifest['build_id'] / 'backend/src/sales_backend/web/admin.html',
             lambda b: b + b'\n<!-- mismatch -->\n', 'Running snapshot was modified')
    rejected(LOCAL / 'build.json', lambda b: json.dumps({**json.loads(b), 'build_id': '0' * 64}).encode(),
             'Source changed since the local build')
    subprocess.run([sys.executable, str(ROOT / 'tools/local/dev.py'), 'check'], check=True)
    print('LOCAL_GUARDS_OK: stale build, edited frontend, modified snapshot rejected; restored build passes')


if __name__ == '__main__':
    main()
