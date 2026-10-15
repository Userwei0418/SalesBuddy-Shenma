import importlib.util
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

path=Path(__file__).parents[1]/'scripts/build_managed_release.py'
spec=importlib.util.spec_from_file_location('build_managed_release',path)
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


def test_clean_commit_package_contains_root_migrations_and_rejects_dirty_tree(tmp_path):
    root=tmp_path/'repo';root.mkdir()
    def git(*args):return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True)
    git('init');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
    files=['backend/src/sales_backend/main.py','backend/pyproject.toml','backend/uv.lock',
           'backend/agent_platform/example.txt','backend/deploy/example.py','backend/scripts/example.py',
           'backend/openapi/example.yaml','database/scripts/migrate.py',
           'database/baseline/V001__current_schema.sql','database/V035__historical.sql',
           'database/migrations/V202__current.sql','tools/installation/example.py']
    for name in files:
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# synthetic fixture\n')
    git('add','--',*files);git('commit','-m','fixture')
    result=builder.build(root,tmp_path/'out')
    with tarfile.open(result['archive']) as archive:
        assert 'database/V035__historical.sql' in archive.getnames()
    manifest=json.loads(Path(result['manifest']).read_text())
    assert set(files)<=manifest['file_hashes'].keys()
    with pytest.raises(ValueError,match='overwrite'):builder.build(root,tmp_path/'out')
    (root/'database/V035__historical.sql').write_text('changed')
    with pytest.raises(ValueError,match='clean committed'):builder.build(root,tmp_path/'dirty')
