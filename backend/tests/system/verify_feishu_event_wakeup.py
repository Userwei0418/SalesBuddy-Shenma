"""Commit/rollback wakeups and targeted regression in a newly created disposable DB."""

import asyncio
import os
from pathlib import Path
import sys
from uuid import uuid4

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_integration_postgres import main


async def verify(config, name, role):
    assert name.startswith("salegent_verify_integration_")
    writer = await asyncpg.connect(database=name, **config)
    listener = await asyncpg.connect(database=name, **config)
    signal = asyncio.Event()
    payloads = []

    def changed(_connection, _pid, _channel, payload):
        payloads.append(payload)
        signal.set()

    try:
        await listener.add_listener("sales_feishu_work", changed)
        cid = uuid4()
        ws, user = await writer.fetchrow(
            "SELECT w.id,u.id FROM platform.workspace w JOIN platform.user_ref u ON u.workspace_id=w.id WHERE w.external_workspace_id='demo-sales-workspace' AND u.account_code='ADMIN001'"
        )
        async with writer.transaction():
            await writer.execute(
                "INSERT INTO config.feishu_connection(id,workspace_id,revision,settings,updated_by) VALUES($1,$2,1,jsonb_build_object('workspace_id',$2::uuid::text,'connection_id',$1::uuid::text),$3)",
                cid,
                ws,
                user,
            )
        await asyncio.wait_for(signal.wait(), 2)
        assert payloads == [""]
        signal.clear()
        payloads.clear()
        tx = writer.transaction()
        await tx.start()
        await writer.execute(
            "INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id) VALUES($1,$2,'customer',$3)",
            cid,
            ws,
            uuid4(),
        )
        await asyncio.sleep(0.05)
        assert not signal.is_set()
        await tx.rollback()
        await asyncio.sleep(0.05)
        assert not signal.is_set()
        async with writer.transaction():
            for _ in range(3):
                await writer.execute(
                    "INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id) VALUES($1,$2,'customer',$3)",
                    cid,
                    ws,
                    uuid4(),
                )
        await asyncio.wait_for(signal.wait(), 2)
        assert payloads == [""]  # PostgreSQL coalesces same-channel same-payload events in one transaction.
        await listener.remove_listener("sales_feishu_work", changed)
        # A missed notification does not lose durable work.
        assert await writer.fetchval("SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1", cid) == 3
        print("PASS committed wakeup, rollback silence, coalescing, empty payload, durable outbox", flush=True)
        # Isolate regression fixtures from this committed synthetic seed.
        await writer.execute("UPDATE ops.feishu_event SET status='succeeded' WHERE connection_id=$1", cid)
        env = {
            **os.environ,
            "SALES_TEST_DATABASE_URL": f"postgresql://{config['user']}@/{name}?host={config['host']}",
            "SALES_TEST_ROLE": role,
            "SALES_TEST_WORKSPACE": "demo-sales-workspace",
        }
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pytest",
            "tests/integration/test_feishu_storage.py",
            "tests/integration/test_feishu_lifecycle.py",
            "-q",
            env=env,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        assert await proc.wait() == 0
    finally:
        await listener.close()
        await writer.close()


if __name__ == "__main__":
    asyncio.run(main(serve=verify))
