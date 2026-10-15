"""Local service selection reuses the product contract without weakening it."""
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'tools/local'), str(ROOT/'tools/installation')]
import bootstrap
import runtime_roles
import upgrade_permissions

spec = importlib.util.spec_from_file_location('local_policy_dev', ROOT/'tools/local/dev.py')
dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev)


@pytest.mark.asyncio
@pytest.mark.parametrize('roles', [{}, {'api':'one'}, {'api':'one','worker':'two'},
    {'api':'one','worker':'two','platform':'three','feishu':'four'},
    {'api':'one','worker':'one','platform':'three'}])
async def test_local_requires_its_three_distinct_services_before_any_database_access(roles):
    with pytest.raises(RuntimeError, match='distinct API, worker and platform'):
        await bootstrap.reconcile_local_runtime(None, {}, roles=roles)


@pytest.mark.asyncio
@pytest.mark.parametrize('function', [runtime_roles.verify, upgrade_permissions.reconcile])
async def test_formal_installation_entrypoints_still_require_all_four_identities(function):
    with pytest.raises(runtime_roles.RolePolicyError, match='Four'):
        await function(None, bootstrap.LOCAL_ROLES, runtime_roles.load_policy())


def test_local_snapshot_contains_shared_policy_imports_and_imports_without_source_checkout(tmp_path):
    names = ['tools/local/bootstrap.py','tools/local/local_paths.py','database/scripts/migrate.py',
             'tools/installation/runtime_roles.py','tools/installation/upgrade_permissions.py',
             'tools/installation/runtime_permissions.json','backend/src/sales_backend/auth/passwords.py']
    for name in names:
        assert dev.selected(name), name
        destination = tmp_path/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT/name).read_bytes())
    result = subprocess.run([sys.executable,'-I','-c',
        "import sys;from pathlib import Path;sys.path.insert(0,str(Path('tools/local').resolve()));"
        "import bootstrap,runtime_roles,upgrade_permissions;"
        "assert bootstrap.load_policy is runtime_roles.load_policy;"
        "assert bootstrap.reconcile_service_roles is upgrade_permissions.reconcile_service_roles;"
        "assert Path(runtime_roles.__file__).is_relative_to(Path.cwd());"
        "assert set(bootstrap.load_policy()['services'])==runtime_roles.SERVICES"],
        cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    baseline = {name:(tmp_path/name).read_bytes() for name in names}
    changed = {**baseline,'tools/installation/runtime_permissions.json':b'changed policy'}
    assert dev.fingerprint(baseline) != dev.fingerprint(changed)
