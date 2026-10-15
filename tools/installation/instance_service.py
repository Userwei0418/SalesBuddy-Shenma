"""Start one instance service with a scrubbed environment and its own credentials."""
import argparse
import os
from pathlib import Path
import runpy
import sys
from urllib.parse import quote, urlencode, urlsplit

if __name__ == '__main__': sys.dont_write_bytecode = True

from instance_files import source_check, read_private, clean_env, no_pending_upgrade
from instance_process import register


def service_environment(root, service):
    no_pending_upgrade(root)
    data, _ = source_check(root)
    private = read_private(root/'private'/f'{service}.json')
    endpoint = data['database']
    def dsn(kind):
        return ('postgresql://'+data['roles'][kind]+':'+quote(private['database_passwords'][kind], safe='')
                +'@/'+endpoint['name']+'?'+urlencode({'host':endpoint['host'],'port':endpoint['port'],
                                                     'sslmode':endpoint['sslmode']}))
    env = clean_env()
    public = urlsplit(data['api_base_url'])  # Validated against the installed artifact above.
    env.update(APP_ENV='production', AUTH_MODE='password', PYTHONUNBUFFERED='1',
               PUBLIC_WEB_ORIGIN=f'{public.scheme}://{public.netloc}',
               PYTHONPYCACHEPREFIX=str(root/'cache/pycache'),
               DATABASE_URL=dsn(service), ACCESS_TOKEN_SECRET=private['token_secret'],
               ACCESS_TOKEN_ISSUER='salesbuddy-'+data['instance_id'],
               ACCESS_TOKEN_AUDIENCE='salesbuddy-client-'+data['instance_id'],
               VISIT_IMPORT_ROOT=str(root/'materials'), WORKER_ID='instance-'+data['instance_id'],
               CONFIG_CREDENTIAL_KEYRING_FILE=str(root/'private/provider-keys.json'),
               CONFIG_CREDENTIAL_KEY_ID='instance-v1')
    if service == 'api':
        env.update(PLATFORM_DATABASE_URL=dsn('platform'), PLATFORM_MFA_KEY_ID='instance-v1',
                   PLATFORM_MFA_KEYRING_FILE=str(root/'private/platform-mfa-keys.json'))
    if service == 'feishu': env['FEISHU_SYNC_DATABASE_URL'] = dsn('feishu')
    return data, env


def run(root, service, identity):
    descriptor = register(root, service, identity)
    try:
        run_service(root, service, identity)
    finally:
        os.close(descriptor)


def run_service(root, service, identity):
    data, env = service_environment(root, service)
    if data['instance_id'] != identity: raise ValueError('Wrong instance identity')
    os.environ.clear(); os.environ.update(env)
    sys.pycache_prefix = env['PYTHONPYCACHEPREFIX']
    sys.path.insert(0, str(root/'release/backend/src'))
    if service == 'api':
        import uvicorn
        uvicorn.run('sales_backend.main:app', host='127.0.0.1', port=data['api_port'],
                    proxy_headers=data['trust_loopback_proxy'], forwarded_allow_ips='127.0.0.1')
    else:
        runpy.run_module('sales_backend.feishu_worker' if service == 'feishu' else 'sales_backend.worker', run_name='__main__')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--service', choices=['api','worker','feishu'], required=True)
    parser.add_argument('--instance-id', required=True)
    args = parser.parse_args()
    run(args.root, args.service, args.instance_id)
