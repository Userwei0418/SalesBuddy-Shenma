"""Owner-only call accounting reconciliation; never replays an AI request."""
from sales_backend.services.storage_reconciliation import ReconciliationConflict


async def require_owner(connection):
    if not await connection.fetchval("""SELECT current_user=pg_get_userbyid(c.relowner) OR r.rolsuper
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_roles r ON r.rolname=current_user
        WHERE n.nspname='control' AND c.relname='call_attempt'"""):
        raise PermissionError('调用对账仅允许安装维护身份执行')


def summary(row):
    return {k: str(row[k]) for k in ('invocation_id','workspace_id','resource','state','updated_at')}


async def pending(connection, workspace, after=None):
    await require_owner(connection)
    rows = await connection.fetch("""SELECT invocation_id,workspace_id,resource,state,updated_at
        FROM control.call_attempt WHERE workspace_id=$1::uuid AND state IN ('reserved','dispatched','unknown')
        AND ($2::uuid IS NULL OR invocation_id>$2::uuid) ORDER BY invocation_id LIMIT 101""", str(workspace), str(after) if after else None)
    return {'items':[summary(r) for r in rows[:100]], 'next_after':str(rows[99]['invocation_id']) if len(rows)>100 else None}


async def reconcile(connection, workspace, invocation, action, *, expected_at, reason, evidence='', http_status=None):
    await require_owner(connection)
    if action not in ('release-unstarted','confirm-not-sent','confirm-response') or not 1<=len(reason.strip())<=500:
        raise ValueError('需要明确的对账动作和原因')
    if action!='release-unstarted' and not 1<=len(evidence.strip())<=500:
        raise ValueError('必须提供上游或传输记录的核验依据编号；超时或一次查询不到不是依据')
    if action=='confirm-response' and (type(http_status) is not int or not 100<=http_status<=599):
        raise ValueError('必须提供已核实的 HTTP 状态')
    async with connection.transaction():
        row = await connection.fetchrow('SELECT * FROM control.call_attempt WHERE workspace_id=$1::uuid AND invocation_id=$2::uuid', str(workspace), str(invocation))
        if not row: raise LookupError('该公司没有这条调用记录')
        await connection.execute('SELECT 1 FROM control.tenant_call_quota WHERE workspace_id=$1 AND resource=$2 FOR UPDATE', row['workspace_id'], row['resource'])
        row = await connection.fetchrow('SELECT * FROM control.call_attempt WHERE invocation_id=$1 FOR UPDATE', row['invocation_id'])
        allowed = 'reserved' if action=='release-unstarted' else 'unknown'
        if row['state']!=allowed or row['updated_at']!=expected_at:
            raise ReconciliationConflict('调用状态已变化，或仍在执行；请重新核查')
        target = 'settled' if action=='confirm-response' else 'released'
        await connection.execute('UPDATE control.call_attempt SET state=$2,updated_at=clock_timestamp() WHERE invocation_id=$1', row['invocation_id'], target)
        await connection.execute("""UPDATE control.call_event e SET state=(SELECT CASE WHEN bool_or(a.state='settled') THEN 'used'
            WHEN bool_or(a.state IN ('reserved','dispatched','unknown')) THEN 'reserved' ELSE 'released' END
            FROM control.call_attempt a WHERE a.workspace_id=e.workspace_id AND a.resource=e.resource
            AND a.operation_code=e.operation_code AND a.event_id=e.event_id)
            WHERE workspace_id=$1 AND resource=$2 AND operation_code=$3 AND event_id=$4""",
            row['workspace_id'], row['resource'], row['operation_code'], row['event_id'])
        # Keep the original error, status and unknown token fields. A received
        # HTTP response does not establish a successful business result.
        await connection.execute("""UPDATE agent.model_invocation SET http_status=COALESCE($2,http_status),
            response_snapshot=response_snapshot || jsonb_build_object('quota_reconciliation',$3::text)
            WHERE id=$1 AND workspace_id=$4""", row['invocation_id'], http_status if action=='confirm-response' else None, action, row['workspace_id'])
        await connection.execute("""INSERT INTO control.audit(workspace_id,action,reason,before_state,after_state)
            VALUES($1,'call.reconcile',$2,$3::jsonb,jsonb_build_object('state',$4::text,'evidence_ref',$5::text,'maintenance_role',current_user))""",
            row['workspace_id'], reason.strip(), summary(row), target, evidence.strip())
        return {**summary(row), 'state':target, 'outcome':'reconciled'}
