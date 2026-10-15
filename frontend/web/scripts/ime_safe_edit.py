"""Checked, timestamped edits for this delivery. Never overwrites an unknown version."""
from pathlib import Path
from datetime import datetime
import hashlib
ROOT = Path(__file__).resolve().parents[1]
def write(relative, before, after):
    path = ROOT / relative
    if isinstance(before, str): before = before.encode()
    if isinstance(after, str): after = after.encode()
    current = path.read_bytes() if path.exists() else None
    if current != before: raise RuntimeError('Concurrent change: ' + relative)
    if current == after: return
    if current is not None:
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        backup = ROOT / '.history' / stamp / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        with backup.open('xb') as f: f.write(current)
        assert backup.read_bytes() == current
    path.parent.mkdir(parents=True, exist_ok=True)
    if (path.read_bytes() if path.exists() else None) != current: raise RuntimeError('Concurrent change before write: ' + relative)
    with path.open('wb' if current is not None else 'xb') as f: f.write(after)
    assert path.read_bytes() == after
    print('VERIFIED', relative, hashlib.sha256(after).hexdigest()[:12])
