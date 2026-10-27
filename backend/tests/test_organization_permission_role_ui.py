"""Exercise the actual console module, including escaping and legacy payloads."""
from pathlib import Path
import subprocess


def test_organization_permission_role_ui():
    result = subprocess.run(['node', '--test', str(Path(__file__).with_suffix('.cjs').with_name('test_organization_permission_roles.cjs'))], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
