"""Controlled company approval calls; database checks native identity and effective grants."""
import asyncpg
from sales_backend.services.operations import OperationsError

SQL = {
    'directory': 'SELECT security.support_directory($1)',
    'grants': 'SELECT security.support_grants()',
    'approve': 'SELECT security.approve_support($1,$2,$3,$4,$5)',
    'revoke': 'SELECT security.revoke_support($1,$2)',
    'audit': 'SELECT security.support_audit_page($1)',
}


async def company_support_call(database, actor, operation, *args):
    try:
        async with database.transaction(actor) as conn:
            return await conn.fetchval(SQL[operation], *args)
    except asyncpg.InsufficientPrivilegeError:
        raise OperationsError('只有具有临时支持管理权限的本公司管理员可以操作', 403) from None
    except asyncpg.NoDataFoundError:
        raise OperationsError('授权不存在', 404) from None
    except asyncpg.SerializationError:
        raise OperationsError('授权请求已变化，请刷新', 409) from None
    except asyncpg.InvalidParameterValueError as exc:
        raise OperationsError(exc.message, 422) from None
