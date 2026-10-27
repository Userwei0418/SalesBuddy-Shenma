"""Exercise real route orchestration + real receipt service, no HTTP/DB/provider.

The fake connection changes authorization while returning from the receipt lock.
Object scope guards and business work are explicit stubs; receipts/digests are
created by the real execute_mutation implementation on the first request.
"""
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from sales_backend.api import business, visit_flow
from sales_backend.contracts.models import VisitCreate
from sales_backend.contracts.visit_flow import QualityRequest, StructureRequest, canonical_fields
from sales_backend.domain.agent import ActorContext


class Connection:
    def __init__(self):
        self.allowed = True
        self.revoke_on_lock = False
        self.receipt = None
        self.events = []
        self.writes = 0

    def is_in_transaction(self):
        return True

    async def execute(self, query, *args):
        if 'pg_advisory_xact_lock' in query:
            self.events.append('receipt_lock_return')
            if self.revoke_on_lock:
                self.allowed = False
                self.events.append('authority_revoked_while_waiting')
        elif 'INSERT INTO ops.mutation_receipt' in query:
            self.receipt = {'request_digest': args[4], 'response': args[5]}
            self.events.append('receipt_saved')
        elif 'authorization_refresh' in query:
            self.events.append('authority_refreshed')
        else:
            raise AssertionError('Unexpected SQL: '+query)

    async def fetchrow(self, query, *args):
        assert 'ops.mutation_receipt' in query
        self.events.append('receipt_read')
        return self.receipt


@pytest.mark.asyncio
@pytest.mark.parametrize('denied_by', ['scope', 'optional'])
@pytest.mark.parametrize('operation', ['structure', 'quality', 'archive'])
async def test_followup_replay_rechecks_revocation_after_lock(monkeypatch, operation, denied_by):
    c = Connection()
    actor = ActorContext(workspace_id=str(uuid4()), user_id=str(uuid4()), role='sales', data_scope='self', team_ids=[])
    identity = SimpleNamespace(actor=actor)
    customer_id, opportunity_id, key = str(uuid4()), str(uuid4()), uuid4()

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        yield c

    async def guard(*args, **kwargs):
        c.events.append('scope_check_allowed' if c.allowed else 'scope_check_denied')
        if not c.allowed and denied_by == "scope":
            raise PermissionError('synthetic collaboration removed')

    async def optional(*args, **kwargs):
        if not c.allowed and denied_by == "optional":
            raise PermissionError("synthetic optional action revoked")

    async def write(*args, **kwargs):
        await guard()
        c.writes += 1
        return {'id': 'synthetic-saved-object', 'result': 'original-authorized-response'}

    database = SimpleNamespace(transaction=transaction)
    if operation == 'archive':
        body = VisitCreate(customer_id=customer_id, fields={'opportunity_id': opportunity_id})
        monkeypatch.setattr(business, 'require_capability', guard)
        monkeypatch.setattr(business, 'require_visit_recording_scope', guard)
        monkeypatch.setattr(business, 'validate_visit_optional_actions', optional)
        monkeypatch.setattr(business, 'validate_visit_attendance', optional)
        monkeypatch.setattr(business, 'archive_visit', write)
        async def invoke():
            return await business.create_visit(body, identity, database, key)
    else:
        if operation == 'structure':
            body = StructureRequest(customer_id=customer_id, opportunity_id=opportunity_id, text='Synthetic follow-up')
        else:
            body = QualityRequest(customer_id=customer_id, opportunity_id=opportunity_id,
                                  source_run_id=str(uuid4()), fields=canonical_fields({}), summary='Synthetic')
        monkeypatch.setattr(visit_flow, 'require_visit_run_scope', guard)
        monkeypatch.setattr(visit_flow, 'prepare_visit_run', write)
        monkeypatch.setattr(visit_flow, 'validate_visit_optional_actions', optional)
        async def invoke():
            return await visit_flow.enqueue(body, operation, identity, database, key)

    first = await invoke()
    assert c.writes == 1 and c.receipt is not None
    c.events = []
    c.revoke_on_lock = True
    with pytest.raises(HTTPException) as raised:
        replay = await invoke()
        print('OBS', json.dumps({'operation': operation, 'response_replayed': replay == first,
                                'allowed_after_wait': c.allowed, 'writes': c.writes, 'events': c.events}))
    assert raised.value.status_code == 403
    assert c.writes == 1
