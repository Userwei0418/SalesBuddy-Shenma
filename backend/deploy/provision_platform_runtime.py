"""Root-only dedicated platform DB role; does not deploy or start the service."""
import argparse
import json
import os
import re
import secrets
import subprocess
from pathlib import Path
from urllib.parse import quote

from sales_backend.services.platform_control import PUBLIC_FUNCTIONS


def provision(path, *, role, database, socket, port, runner=subprocess.run):
    if os.geteuid() != 0:
        raise ValueError('Root is required')
    if not all(re.fullmatch(r'[a-z][a-z0-9_]{1,62}', value) for value in (role, database)):
        raise ValueError('Invalid database identity')
    if not 1 <= port <= 65535 or not Path(socket).is_absolute():
        raise ValueError('Invalid database endpoint')
    command = ['sudo','-u','postgres','psql','-X','-h',socket,'-p',str(port),'-d',database,'-v','ON_ERROR_STOP=1','-At']
    check = runner(command,input=f"SELECT count(*) FROM pg_roles WHERE rolname='{role}';\n",text=True,capture_output=True)
    if check.returncode or check.stdout.strip() != '0':
        raise ValueError('Role exists or preflight failed')
    password = secrets.token_urlsafe(48)
    descriptor = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    dispatched = False
    try:
        with os.fdopen(descriptor,'w') as output:
            output.write(f'PLATFORM_DATABASE_URL=postgresql://{role}:{quote(password,safe="")}@127.0.0.1:{port}/{database}\n')
            output.flush()
            os.fsync(output.fileno())
        # The only copy of this new credential must be durable before SQL can
        # commit. A disconnected client cannot tell whether COMMIT succeeded.
        directory = os.open(Path(path).parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        grants = ''.join(f'GRANT EXECUTE ON FUNCTION control.{signature} TO {role};' for signature in PUBLIC_FUNCTIONS)
        sql = (f"BEGIN; CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '{password}';"
               f'GRANT USAGE ON SCHEMA control TO {role};{grants}COMMIT;')
        dispatched = True
        result = runner(command,input=sql,text=True,capture_output=True)
        if result.returncode:
            raise ValueError('Platform role provisioning failed')
    except Exception:
        # After dispatch, keep the private file even on a reported SQL failure:
        # an uncertain commit must never strand an existing role without its
        # password. Recovery is explicit; never reset/recreate that role here.
        if not dispatched:
            Path(path).unlink(missing_ok=True)
        raise
    return {'provisioned':True,'role':role,'service_started':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment-file',default='/etc/sales-platform.env')
    parser.add_argument('--role',default='sales_platform')
    parser.add_argument('--database',required=True)
    parser.add_argument('--socket',default='/tmp')
    parser.add_argument('--port',type=int,default=5432)
    args = parser.parse_args()
    try:
        print(json.dumps(provision(args.environment_file,role=args.role,database=args.database,socket=args.socket,port=args.port)))
    except Exception:
        print(json.dumps({'provisioned':False,'code':'PLATFORM_ROLE_PROVISIONING_FAILED'}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
