"""Run explicitly selected fixed probes under a formal administrator; never publish."""

import argparse
import asyncio
import json
from uuid import uuid4

from sales_backend.config import get_settings
from sales_backend.db import Database
from sales_backend.domain.company_rules import TECHNICAL_CAPABILITIES
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.connectivity import ConnectivityService
from sales_backend.services.model_api import ModelApiService


async def main(args):
    settings = get_settings()
    database = Database(settings)
    await database.connect()
    try:
        async with database.connection() as conn:
            record = await IdentityRepository().find_maintenance_administrator(
                conn, workspace_external_id=args.workspace, account_code=args.administrator
            )
        if not record or record.context.role.value != "administrator":
            raise RuntimeError("需要当前公司有效系统管理员")
        actor = record.context
        service = ConnectivityService(database, settings)
        models = ModelApiService(database, settings)
        before = await models.list(actor)
        results = []
        for kind, targets in (("direct", args.direct), ("agent", args.agent)):
            for target in targets:
                key = uuid4()
                result = await service.run(actor, kind, target, key)
                for _ in range(45):
                    if result["status"] != "running":
                        break
                    await asyncio.sleep(2)
                    result = await service.get(actor, key)
                assert await service.get(actor, key) == result
                assert await service.run(actor, kind, target, key) == result
                assert not result["business_writes"] and not result["fallback_used"]
                results.append(result)
        after = await models.list(actor)
        assert before == after
        print(json.dumps({"workspace": args.workspace, "bindings_unchanged": True,
                          "read_and_idempotent_replay_verified": True, "tests": results},
                         ensure_ascii=False, indent=2))
    finally:
        await database.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--administrator", required=True)
    parser.add_argument("--direct", action="append", choices=["text", "asr"], default=[])
    parser.add_argument("--agent", action="append", choices=list(TECHNICAL_CAPABILITIES), default=[])
    args = parser.parse_args()
    if not 1 <= len(args.direct) + len(args.agent) <= 5:
        parser.error("每轮显式指定一至五个测试对象")
    try:
        asyncio.run(main(args))
    except Exception as exc:
        raise SystemExit("核验未完成：" + type(exc).__name__) from None
