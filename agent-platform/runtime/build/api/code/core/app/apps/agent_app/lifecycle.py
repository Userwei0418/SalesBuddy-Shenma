"""Agent-only operational metadata. Never stores credentials or request bodies."""
from datetime import datetime, timezone
import json
from sqlalchemy import select
from core.db.session_factory import session_factory
from models.model import Message
from models.enums import MessageStatus

TERMINAL = {'succeeded', 'cancelled', 'failed', 'timed_out'}

def stamp():
    return datetime.now(timezone.utc).isoformat()

def merge_execution(metadata, fields):
    result = dict(metadata) if isinstance(metadata, dict) else {}
    previous = dict(result.get('agent_execution') or {})
    # Late progress callbacks must not resurrect a terminal execution.
    if previous.get('status') in TERMINAL and fields.get('status') not in (None, previous['status']):
        fields = {k:v for k,v in fields.items() if k != 'status'}
    previous.update(fields)
    result['agent_execution'] = previous
    return result

def record(message_id, app_id, **fields):
    with session_factory.create_session() as session:
        row = session.scalar(select(Message).where(Message.id == message_id, Message.app_id == app_id).with_for_update())
        if row is None:
            return
        try: metadata = json.loads(row.message_metadata or '{}')
        except (ValueError, TypeError): metadata = {}
        row.message_metadata = json.dumps(merge_execution(metadata, fields))
        state = json.loads(row.message_metadata)['agent_execution'].get('status')
        if state in ('cancelled','failed','timed_out'):
            row.status = MessageStatus.ERROR
            row.error = 'Agent execution ' + state
        session.commit()

def started_stream(stream, *, task_id, message_id, conversation_id, snapshot_id):
    # The caller can cancel even while the model has not produced any text.
    yield {'event':'message_started','task_id':task_id,'message_id':message_id,
           'conversation_id':conversation_id,'snapshot_id':snapshot_id,
           'execution_status':'running','created_at':int(datetime.now(timezone.utc).timestamp())}
    yield from stream
