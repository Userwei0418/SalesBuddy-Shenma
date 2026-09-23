"""Identify the imported release, never the mutable current symlink or git branch."""
from pathlib import Path
import re


def release_identity(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[3]
    revision_file = root / 'REVISION'
    revision = revision_file.read_text().strip() if revision_file.is_file() else ''
    valid = bool(re.fullmatch('[a-f0-9]{40}', revision))
    versions = [int(p.name.split('__')[0][1:]) for p in (root / 'database/migrations').glob('V[0-9]*__*.sql')]
    return {'mode': 'release' if valid else 'development', 'revision': revision if valid else None,
            'release': root.name if valid else None,
            'expected_schema': f'V{max(versions):03}' if versions else None}


# Capture at import: restarting into another release must change the observed revision.
RELEASE_IDENTITY = release_identity()
