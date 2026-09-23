"""Create isolated DB login and configuration on the customer host; never print secrets."""
import asyncio,base64,json,os,pwd,secrets,subprocess
from pathlib import Path
import asyncpg

async def main():
    if os.geteuid()!=0:raise SystemExit('Run as root on salesbuddy')
    if subprocess.check_output(['hostname'],text=True).strip()!='salesbuddy':raise SystemExit('Wrong target host')
    target=Path('/etc/shenma-sales');target.mkdir(mode=0o750,exist_ok=True)
    service=pwd.getpwnam('shenma-sales')
    os.chown(target,0,service.pw_gid)
    if (target/'runtime.env').exists():raise SystemExit('Runtime environment already exists; no overwrite')
    db_password=secrets.token_urlsafe(36)
    # The root maintenance process uses peer authentication by dropping OS identity.
    pg=pwd.getpwnam('postgres')
    os.setegid(pg.pw_gid);os.seteuid(pg.pw_uid)
    try:
        c=await asyncpg.connect(host='/var/run/postgresql',database='postgres',user='postgres')
        if await c.fetchval("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='shenma_runtime')"):
            raise RuntimeError('Runtime role already exists; review rather than overwrite')
        await c.execute("CREATE ROLE shenma_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '"+db_password+"'")
        await c.execute('GRANT CONNECT ON DATABASE shenma_sales TO shenma_runtime')
        await c.close()
    finally:os.seteuid(0);os.setegid(0)
    env={'APP_ENV':'production','AUTH_MODE':'password','DATABASE_URL':f'postgresql://shenma_runtime:{db_password}@127.0.0.1:5432/shenma_sales','ACCESS_TOKEN_SECRET':secrets.token_urlsafe(48),'ACCESS_TOKEN_ISSUER':'shenma-sales','ACCESS_TOKEN_AUDIENCE':'shenma-mini-program','CONFIG_CREDENTIAL_KEY_ID':'shenma-v1','CONFIG_CREDENTIAL_KEYRING_FILE':'/etc/shenma-sales/keyring.json','SENSEAUDIO_API_KEY':'','AGENT_PLATFORM_BINDINGS_JSON':'{}'}
    fd=os.open(target/'runtime.env',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:f.write(''.join(f'{k}={v}\n' for k,v in env.items()))
    fd=os.open(target/'keyring.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:json.dump({'keys':{'shenma-v1':base64.b64encode(secrets.token_bytes(32)).decode()}},f)
    os.chown(target/'keyring.json',service.pw_uid,service.pw_gid)
    print('Independent runtime credentials generated on customer server; values not displayed')
asyncio.run(main())
