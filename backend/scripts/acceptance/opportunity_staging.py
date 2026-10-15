"""Isolated temporary service, credentials injected by systemd, no secrets emitted."""
import os
import sys
from urllib.parse import urlsplit,urlunsplit
p=urlsplit(os.environ['DATABASE_URL'])
os.environ['DATABASE_URL']=urlunsplit((p.scheme,p.netloc,'/sales_opportunity_acceptance_20260910',p.query,p.fragment))
if sys.argv[-1]=='worker':
    from sales_backend.worker import run
    run()
else:
    import uvicorn
    uvicorn.run('sales_backend.main:app',host='127.0.0.1',port=8089)
