"""Fail a release preflight if copied editable installs still import an older release."""
import argparse
import importlib.util
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--release', required=True)
args = parser.parse_args()
release = Path(args.release).resolve()
origins = {}
for module in ('sales_backend.main', 'sales_backend.worker', 'sales_backend.repositories.opportunity_mutations'):
    spec = importlib.util.find_spec(module)
    if spec is None or not spec.origin:
        raise SystemExit(f'Missing module: {module}')
    origin = Path(spec.origin).resolve()
    if not origin.is_relative_to(release):
        raise SystemExit(f'Runtime mismatch: {module} imports {origin}, expected under {release}')
    origins[module] = str(origin)
print(json.dumps({'release': str(release), 'modules': origins}, ensure_ascii=False))
