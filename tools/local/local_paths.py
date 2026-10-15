"""Generated local state belongs outside synchronized source directories."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys


def source_identity(root: Path) -> Path:
    """Retain a local runtime identity only with a private, explicit move receipt.

    The old entry must point to this exact checkout. Copying a repository or its
    receipt elsewhere cannot attach another checkout's database by accident.
    """
    root = root.resolve()
    receipt = root / '.git/local-source-relocation.json'
    if not receipt.exists() and not receipt.is_symlink():
        return root
    stat = receipt.lstat()
    if receipt.is_symlink() or stat.st_uid != os.getuid() or stat.st_mode & 0o077:
        raise RuntimeError('Source relocation receipt must be a private file owned by this OS user')
    value = json.loads(receipt.read_text())
    old = Path(value.get('source', ''))
    if (value.get('kind') != 'salesbuddy-local-source-relocation-v1'
            or value.get('target') != str(root) or not old.is_absolute()
            or str(old) != os.path.normpath(str(old)) or old == root
            or not old.is_symlink() or old.resolve() != root):
        raise RuntimeError('Source relocation receipt does not match the original entry and this checkout')
    return old


def state_directory(root: Path, *, home: Path | None = None, system: str | None = None) -> Path:
    home = home or Path.home()
    system = system or sys.platform
    base = home / 'Library/Application Support/Raccoon SalesBuddy' if system == 'darwin' else home / '.local/share/raccoon-salesbuddy'
    identifier = hashlib.sha256(str(source_identity(root)).encode()).hexdigest()[:16]
    return base / identifier


def state_from_config(cfg: dict) -> Path:
    if cfg.get('kind') != 'salesbuddy-local-v1' or not Path(cfg.get('root', '')).is_absolute():
        raise RuntimeError('Invalid local environment identity')
    expected = state_directory(Path(cfg['root']))
    if cfg.get('state_root') != str(expected):
        raise RuntimeError('Local state location is missing or belongs to another checkout; run dev.py relocate')
    if expected.is_symlink() or expected.resolve() != expected:
        raise RuntimeError('Local state must not redirect to another directory')
    return expected


def ensure_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise RuntimeError('Local state must not be a symlink')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid():
        raise RuntimeError('Local state belongs to another OS user')
    path.chmod(0o700)
