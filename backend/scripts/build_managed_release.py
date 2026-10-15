"""Package a clean fixed commit with its complete migration tree and SHA256 map."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'backend/deploy'))
from release_bundle_control import SOURCE_PATHS, tree_digest
from release_upgrade import safe_path


def build(root, output):
    def git(*args): return subprocess.check_output(['git','-C',str(root),*args])
    revision=git('rev-parse','HEAD').decode().strip()
    if git('status','--porcelain','--untracked-files=all').strip():
        raise ValueError('A managed release requires a clean committed tree')
    archive=git('archive','--format=tar',revision,*SOURCE_PATHS,'tools/installation')
    files={}
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for entry in source:
            name=safe_path(entry.name.rstrip('/'))
            if entry.isfile(): files[name]=hashlib.sha256(source.extractfile(entry).read()).hexdigest()
            elif not entry.isdir(): raise ValueError('Only regular committed source is allowed')
    # Include root V035-V039 as well as the newer migrations directory.
    migrations={str(path.relative_to(root)) for path in (root/'database').glob('V*__*.sql')}
    migrations|={str(path.relative_to(root)) for path in (root/'database/migrations').glob('V*__*.sql')}
    required={'backend/src/sales_backend/main.py','backend/pyproject.toml',
              'database/scripts/migrate.py','database/baseline/V001__current_schema.sql'}
    if not required|migrations <= files.keys(): raise ValueError('Incomplete source or migration archive')
    if git('rev-parse','HEAD').decode().strip()!=revision or git('status','--porcelain','--untracked-files=all').strip():
        raise ValueError('Source changed during packaging')
    output.mkdir(parents=True,exist_ok=True)
    tar=output/(revision+'.tar');manifest=output/(revision+'.manifest.json')
    if tar.exists() or manifest.exists(): raise ValueError('Refusing to overwrite an existing artifact')
    value={'commit':revision,'sha256':hashlib.sha256(archive).hexdigest(),'file_hashes':files,
           'source_sha256':tree_digest(root,SOURCE_PATHS)}
    tar.write_bytes(archive);manifest.write_text(json.dumps(value,indent=2)+'\n')
    return {'commit':revision,'archive':str(tar),'manifest':str(manifest),
            'source_files':len(files),'source_sha256':value['source_sha256']}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(build(ROOT,args.output.resolve()),indent=2))
