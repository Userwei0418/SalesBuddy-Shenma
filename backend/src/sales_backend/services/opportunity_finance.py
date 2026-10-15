"""Explicit, idempotent source decisions; original financial facts stay immutable."""

from uuid import uuid4

from sales_backend.domain.concurrency import VersionConflict
from sales_backend.repositories.opportunity_finance import OpportunityFinanceRepository
from sales_backend.services.idempotency import execute_mutation
from sales_backend.repositories.authorization_checks import require_permission
from sales_backend.repositories.business_changes import record_change
from sales_backend.domain.concurrency import require_version


async def confirm_source_idempotently(connection, actor, opportunity_id, data, key):
    # Current object/management permissions apply even when a prior receipt can
    # be replayed. A grant revoked since the original save cannot expose it.
    repository = OpportunityFinanceRepository()
    await repository.lock_for_confirmation(connection, opportunity_id)
    await repository.read(connection, opportunity_id)
    return await execute_mutation(
        connection,
        actor,
        key,
        f"opportunity.finance.confirm:{opportunity_id}",
        data,
        lambda: confirm_source(connection, actor, opportunity_id, data),
    )


async def confirm_source(connection, actor, opportunity_id, data):
    repository = OpportunityFinanceRepository()
    await repository.lock_for_confirmation(connection, opportunity_id)
    overview = await repository.read(connection, opportunity_id)
    period = next(
        (
            p
            for p in overview["periods"]
            if (p["year"], p["quarter"], p["kind"]) == (data["year"], data["quarter"], data["kind"])
        ),
        None,
    )
    if not period or period["source_fingerprint"] != data["expected_fingerprint"]:
        raise VersionConflict("财务明细已变化，请刷新并重新核对来源")
    decision = period["decision"]
    if (decision["version_no"] if decision else 0) != data["expected_version"]:
        raise VersionConflict("来源已由其他人确认，请刷新后再操作")
    selected_id = str(data["selected_history_id"]) if data.get("selected_history_id") else None
    if data["source"] == "entries":
        if selected_id or period["entry_amount"] is None:
            raise ValueError("请选择有有效明细的逐笔来源，且不要指定历史记录")
    elif not any(str(h["id"]) == selected_id and h["amount_yuan"] is not None for h in period["history"]):
        raise ValueError("请选择本商机、本季度、本金额类型的历史汇总证据")
    reason = data["reason"].strip()
    if not reason:
        raise ValueError("请填写选择此来源的核对理由")
    args = (data["source"], selected_id, period["source_fingerprint"], reason, actor.user_id)
    if decision:
        await connection.execute(
            """UPDATE crm.opportunity_actual_basis SET source=$1,selected_history_id=$2::uuid,
          source_fingerprint=$3,reason=$4,decided_by_user_ref_id=$5::uuid,version_no=version_no+1
          WHERE id=$6::uuid AND workspace_id=$7::uuid AND version_no=$8""",
            *args,
            decision["id"],
            actor.workspace_id,
            data["expected_version"],
        )
    else:
        await connection.execute(
            """INSERT INTO crm.opportunity_actual_basis
          (source,selected_history_id,source_fingerprint,reason,decided_by_user_ref_id,id,workspace_id,opportunity_id,year,quarter,kind)
          VALUES($1,$2::uuid,$3,$4,$5::uuid,$6::uuid,$7::uuid,$8::uuid,$9,$10,$11)""",
            *args,
            str(uuid4()),
            actor.workspace_id,
            str(opportunity_id),
            data["year"],
            data["quarter"],
            data["kind"],
        )
    return await repository.read(connection, opportunity_id)


async def confirm_actuals_idempotently(connection, actor, opportunity_id, data, key):
    repository = OpportunityFinanceRepository()
    parent = await repository.lock_for_confirmation(connection, opportunity_id)
    await repository.read(connection, opportunity_id)
    if data.get('empty_action') == 'withdraw' or any(r['action'] == 'withdraw' for r in data['entries']):
        await require_permission(connection, 'actual.void', customer_id=parent['customer_id'])
        await require_permission(connection, 'actual.void', opportunity_id=opportunity_id)
    return await execute_mutation(connection, actor, key, f'opportunity.actual.confirm:{opportunity_id}', data,
                                  lambda: confirm_actuals(connection, actor, opportunity_id, data))


async def confirm_actuals(connection, actor, opportunity_id, data):
    repository = OpportunityFinanceRepository()
    parent = await repository.lock_for_confirmation(connection, opportunity_id)
    require_version(parent['version_no'], data['expected_opportunity_version'])
    before = await repository.read(connection, opportunity_id, year=data['year'], quarter=data['quarter'])
    period = next(p for p in before['periods'] if (p['year'],p['quarter'],p['kind']) == (data['year'],data['quarter'],data['kind']))
    if period['source_fingerprint'] != data['expected_fingerprint']:
        raise VersionConflict('核对参考资料已变化，请刷新后重新确认')
    require_version(period['version_no'], data['expected_version'])
    decision = period['decision']
    if (period['entries'] or period['history'] or decision) and (decision or {}).get('source') != 'operations' and not data['replace_source']:
        raise VersionConflict('请明确确认替换现有实际来源，旧证据仍会保留')
    expected = [str(x) for x in data.get('expected_material_line_ids') or []] if data['kind']=='recognized' else None
    if decision and decision['source']=='operations' and data['kind']=='recognized':
        old_expected = decision.get('expected_material_line_ids') or []
        if set(expected) != set(old_expected) and not data['replace_expected_materials']:
            raise VersionConflict('本期应登记物料集合已存在，修改须明确确认替换集合')
    if data.get('empty_action') == 'withdraw' and not period['empty_confirmation']:
        raise VersionConflict('本期没有可撤回的空集合确认，请刷新')
    existing = {str(r['material_line_id']) if r['material_line_id'] else None: r for r in period['confirmations']}
    for item in data['entries']:
        line_id = str(item['material_line_id']) if item.get('material_line_id') else None
        old = existing.get(line_id)
        require_version(old['version_no'] if old else 0, item['expected_version'])
        if item['action']=='withdraw' and (not old or old['status']!='confirmed'):
            raise VersionConflict('该项尚未确认或已经撤回，请刷新')
        if old:
            await connection.execute("""UPDATE crm.opportunity_actual_confirmation SET
              amount=COALESCE($1,amount),status=$2,source_ref=CASE WHEN $2='confirmed' THEN $3 ELSE source_ref END,
              note=CASE WHEN $2='confirmed' THEN $4 ELSE note END,reason=$5,confirmed_by_user_ref_id=$6::uuid,
              version_no=version_no+1 WHERE id=$7::uuid""", item.get('amount'),
              'withdrawn' if item['action']=='withdraw' else 'confirmed',item.get('source_ref',''),item.get('note',''),
              data['reason'],actor.user_id,old['id'])
        else:
            await connection.execute("""INSERT INTO crm.opportunity_actual_confirmation
              (workspace_id,opportunity_id,material_line_id,year,quarter,kind,amount,tax_basis,source_ref,note,reason,
               material_snapshot,confirmed_by_user_ref_id)
              VALUES($1::uuid,$2::uuid,$3::uuid,$4,$5,$6,$7,$8,$9,$10,$11,'{}',$12::uuid)""",
              actor.workspace_id,str(opportunity_id),line_id,data['year'],data['quarter'],data['kind'],item['amount'],
              'exclusive' if data['kind']=='recognized' else 'not_applicable',item['source_ref'],item.get('note',''),data['reason'],actor.user_id)
    empty = data.get('empty_action') == 'confirm'
    if decision:
        await connection.execute("""UPDATE crm.opportunity_actual_basis SET source='operations',selected_history_id=NULL,
          source_fingerprint=$1,reason=$2,decided_by_user_ref_id=$3::uuid,expected_material_line_ids=$4::uuid[],
          empty_confirmation=$5,version_no=version_no+1 WHERE id=$6::uuid""",
          period['source_fingerprint'],data['reason'],actor.user_id,expected,empty,decision['id'])
    else:
        await connection.execute("""INSERT INTO crm.opportunity_actual_basis
          (workspace_id,opportunity_id,year,quarter,kind,source,source_fingerprint,reason,decided_by_user_ref_id,
           expected_material_line_ids,empty_confirmation)
          VALUES($1::uuid,$2::uuid,$3,$4,$5,'operations',$6,$7,$8::uuid,$9::uuid[],$10)""",
          actor.workspace_id,str(opportunity_id),data['year'],data['quarter'],data['kind'],period['source_fingerprint'],
          data['reason'],actor.user_id,expected,empty)
    # Fail inside this service boundary (and before a replay receipt is stored)
    # if the final set would drop still-confirmed material lines.
    await connection.execute('SET CONSTRAINTS crm.actual_final_state,crm.basis_final_state IMMEDIATE')
    result = await repository.read(connection, opportunity_id, year=data['year'], quarter=data['quarter'])
    after = next(p for p in result['periods'] if (p['year'],p['quarter'],p['kind']) == (data['year'],data['quarter'],data['kind']))
    await record_change(connection, actor, {'id':parent['customer_id'],'name':before['name']},
                        opportunity_id=str(opportunity_id),version=after['version_no'],notify=False,
                        changes=[{'field':'operational_actual','label':'运营期间实际','year':data['year'],'quarter':data['quarter'],
                                  'kind':data['kind'],'reason':data['reason'],'before':period,'after':after}])
    return result
