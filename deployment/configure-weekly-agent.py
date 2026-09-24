"""Bind the customer weekly Agent using a root-only, separately transferred credential file."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
from uuid import UUID


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binding', type=Path, required=True)
    parser.add_argument('--workspace', action='append', required=True)
    args = parser.parse_args()
    assert os.geteuid() == 0 and socket.gethostname() == 'salesbuddy'
    assert '172.22.9.234' in subprocess.check_output(['hostname', '-I'], text=True).split()
    binding = json.loads(args.binding.read_text())
    assert re.fullmatch(r'app-[A-Za-z0-9_-]+', binding['api_key'])
    for key in ('app_id','expected_snapshot_id'): UUID(binding[key])
    workspaces = [str(UUID(w)) for w in args.workspace]
    for workspace in workspaces:
        result = subprocess.check_output(['sudo','-u','postgres','psql','-X','-At','-d','shenma_sales','-c',
            "SELECT count(*) FROM platform.workspace WHERE id='" + workspace + "'::uuid"],text=True).strip()
        assert result == '1'
    path = Path('/etc/shenma-sales/runtime.env')
    lines = path.read_text().splitlines()
    settings = dict(x.split('=',1) for x in lines if x and not x.startswith('#') and '=' in x)
    assert settings.get('AGENT_FDE_BASE_URL','').rstrip('/') == 'https://ops-salesbuddy.shenzhoukuntai.com:18899/v1'
    values = dict(WEEKLY_AGENT_API_KEY=binding['api_key'], WEEKLY_AGENT_APP_ID=binding['app_id'],
        WEEKLY_AGENT_SNAPSHOT_ID=binding['expected_snapshot_id'], WEEKLY_ENABLED_WORKSPACES=','.join(workspaces),
        WEEKLY_TIMEOUT_SECONDS='180', WEEKLY_MAX_INPUT_BYTES='180000')
    os.umask(0o077)
    backup = Path('/var/backups/shenma-sales') / ('weekly-env-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    shutil.copy2(path, backup/'runtime.env')
    output = [line for line in lines if line.partition('=')[0] not in values]
    output += [key+'='+value for key,value in values.items()]
    temporary=path.with_suffix('.weekly.tmp')
    with temporary.open('x') as target:target.write('\n'.join(output)+'\n')
    shutil.chown(temporary,user=path.stat().st_uid,group=path.stat().st_gid)
    temporary.chmod(path.stat().st_mode & 0o777)
    os.replace(temporary,path)
    print(json.dumps({'configured_workspaces':workspaces,'app_id':binding['app_id'],
                     'expected_snapshot_id':binding['expected_snapshot_id'],'backup':str(backup),
                     'restart_required':True}))


if __name__ == '__main__':
    main()
