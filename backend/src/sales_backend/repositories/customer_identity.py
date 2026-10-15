"""Stable partner links, validated in the caller's existing customer transaction."""
import asyncpg


async def related_partner(connection, data, before=None):
    before = before or {}
    result = dict(data)
    if 'partner_id' not in result:
        # Old forms may echo the text. A changed text cannot silently keep an ID
        # pointing at a different partner; clear that ID without guessing a new one.
        if 'partner_name' in result and result['partner_name'] != before.get('primary_partner_name'):
            result['partner_id'] = None
        return result
    partner_id = result['partner_id']
    if partner_id is None:
        if before or not result.get('partner_name'):
            result['partner_name'] = ''
        return result
    row = await connection.fetchrow(
        'SELECT id,name,status FROM crm.partner WHERE id=$1::uuid AND workspace_id=common.current_workspace_id()',
        str(partner_id),
    )
    if not row or (row['status'] != 'active' and str(before.get('primary_partner_id')) != str(partner_id)):
        raise ValueError('请选择本公司有效的关联合作伙伴')
    self_id = result.get('partner_profile_id', before.get('partner_profile_id'))
    if self_id and str(partner_id) == str(self_id):
        raise ValueError('关联合作伙伴不能选择企业自身伙伴档案')
    result['partner_id'] = str(row['id'])
    result['partner_name'] = row['name']
    return result


async def set_identity(connection, customer_id, roles, profile_id):
    try:
        value = await connection.fetchval(
            'SELECT security.set_customer_business_identity($1::uuid,$2::text[],$3::uuid)',
            str(customer_id), roles, str(profile_id) if profile_id else None,
        )
        return str(value) if value else None
    except asyncpg.UniqueViolationError as exc:
        raise FileExistsError('伙伴档案已存在或已被其他企业关联，请明确选择并核对已有档案') from exc
    except asyncpg.InsufficientPrivilegeError as exc:
        raise PermissionError('当前账号无权建立此企业伙伴档案关联') from exc
    except asyncpg.NoDataFoundError as exc:
        raise LookupError('企业档案不存在或不在当前公司') from exc
    except (asyncpg.InvalidParameterValueError, asyncpg.CheckViolationError) as exc:
        raise ValueError(exc.message) from exc


async def customer_analysis_applies(connection, customer_id):
    return bool(await connection.fetchval(
        "SELECT EXISTS(SELECT 1 FROM crm.customer WHERE id=$1::uuid AND deleted_at IS NULL "
        "AND 'customer'=ANY(business_identities))", str(customer_id)))
