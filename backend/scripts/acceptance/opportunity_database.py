"""Run isolated PostgreSQL regression using service-injected credentials; never print a DSN."""
import os
import subprocess
import sys
from urllib.parse import urlsplit,urlunsplit
p=urlsplit(os.environ['DATABASE_URL'])
os.environ['SALES_TEST_DATABASE_URL']=urlunsplit((p.scheme,p.netloc,'/sales_opportunity_acceptance_20260910',p.query,p.fragment))
os.environ['PYTHONPATH']='src'
raise SystemExit(subprocess.call([sys.executable,'-m','pytest','tests/integration','-q']))
