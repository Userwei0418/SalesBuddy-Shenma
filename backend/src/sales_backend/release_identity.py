"""Identify the imported release, never the mutable current symlink or git branch."""
from pathlib import Path
import json
import os
import re


def release_identity(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[3]
    revision_file = root / 'REVISION'
    revision = revision_file.read_text().strip() if revision_file.is_file() else ''
    valid = bool(re.fullmatch('[a-f0-9]{40}', revision))
    versions = [int(p.name.split('__')[0][1:]) for p in (root / 'database/migrations').glob('V[0-9]*__*.sql')]
    # A generated local snapshot is never a production release. Ignore this
    # opt-in file outside development so it cannot relabel a deployed server.
    local_file = root / 'LOCAL_BUILD.json'
    if os.getenv('APP_ENV') == 'development' and local_file.is_file():
        local = json.loads(local_file.read_text())
        if local.get('environment') != 'local' or not re.fullmatch('[a-f0-9]{64}', local.get('build_id', '')):
            raise ValueError('Invalid local build manifest')
        return {'mode': 'development', 'revision': local.get('revision'), 'release': None,
                'expected_schema': f'V{max(versions):03}' if versions else None,
                'local_build_id': local['build_id']}
    return {'mode': 'release' if valid else 'development', 'revision': revision if valid else None,
            'release': root.name if valid else None,
            'expected_schema': f'V{max(versions):03}' if versions else None}


# Capture at import: restarting into another release must change the observed revision.
RELEASE_IDENTITY = release_identity()
