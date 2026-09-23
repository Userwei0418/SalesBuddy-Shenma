"""Read-only lookup of real, active accounts and their existing memberships."""
import json,sys
from app import app
from sqlalchemy import select
from core.db.session_factory import session_factory
from models import Account,Tenant,TenantAccountJoin
mode,email,workspace=sys.argv[1:]
with app.app_context(),session_factory.create_session() as s:
    account=s.scalar(select(Account).where(Account.email==email))
    if not account or str(account.status)!='active':raise SystemExit('请先在新平台创建或邀请这个账号，并完成激活。')
    rows=s.execute(select(Tenant,TenantAccountJoin).join(TenantAccountJoin,TenantAccountJoin.tenant_id==Tenant.id).where(TenantAccountJoin.account_id==account.id)).all()
    if mode=='cli':
        rows=[(t,m) for t,m in rows if t.name==workspace or t.id==workspace]
        if len(rows)!=1 or str(rows[0][1].role) not in ('owner','admin','editor'):raise SystemExit('空间不唯一或该账号尚无编辑权限。先在页面加入目标工作空间。')
        tenant_id=rows[0][0].id
    else:
        if not any(str(m.role)=='owner' for t,m in rows):raise SystemExit('只允许现有工作空间所有者开通创建空间能力。')
        tenant_id=''
    print('RACCOON_RESULT='+json.dumps(dict(account_id=account.id,workspace_id=tenant_id)))
