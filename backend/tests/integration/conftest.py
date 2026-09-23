"""连真实 PostgreSQL 的集成测试脚手架。

设计要点：
- 每个测试独占一条连接，开事务、跑完无条件回滚，真实库里不留任何痕迹，
  因此可以直接指向演示库而不污染演示数据。
- 未配置 SALES_TEST_DATABASE_URL 时整个目录跳过，保证离线仍能跑纯函数单测。
- 测试身份走生产同一条解析路径（security.resolve_demo_actor），
  角色与数据范围来自 platform.role_binding，不在测试里另写一套假设。
- 仓储方法普遍以 connection 为入参，所以能把回滚事务的连接直接喂进去；
  自己开事务的入口（如 AgentRunHandler）不在本目录覆盖范围内。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio

from sales_backend.db import normalize_database_url, set_request_context
from sales_backend.domain.agent import ActorContext, RoleCode
from sales_backend.repositories.identity import IdentityRepository

DSN_ENV = "SALES_TEST_DATABASE_URL"
WORKSPACE_ENV = "SALES_TEST_WORKSPACE"

# 隔离测试引导脚本创建这些账号；不属于客户环境的通用初始化种子。
DEMO_ACCOUNTS = {
    RoleCode.SALES: "XS001",
    RoleCode.SUPERVISOR: "ZJ001",
    RoleCode.MANAGER: "ZJL001",
}


def _dsn() -> str:
    dsn = os.environ.get(DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{DSN_ENV} 未配置，跳过集成测试", allow_module_level=True)
    return normalize_database_url(dsn)


@pytest_asyncio.fixture
async def connection() -> AsyncIterator[asyncpg.Connection]:
    """一条处于「已开启事务、退出时回滚」状态的连接。"""
    conn = await asyncpg.connect(dsn=_dsn())
    for type_name in ("json", "jsonb"):
        await conn.set_type_codec(
            type_name, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text"
        )
    transaction = conn.transaction()
    await transaction.start()
    try:
        role = os.environ.get('SALES_TEST_ROLE')
        if role:
            assert re.fullmatch(r'salegent_verify_role_[a-f0-9]+', role)
            await conn.execute(f'SET LOCAL ROLE "{role}"')
            assert not await conn.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')
        yield conn
    finally:
        await transaction.rollback()
        await conn.close()


@pytest_asyncio.fixture
async def actor_factory(connection: asyncpg.Connection):
    """按角色产出 ActorContext，并把该连接的 RLS 上下文切到这个角色。"""
    workspace = os.environ.get(WORKSPACE_ENV, "demo-sales-workspace")
    repository = IdentityRepository()

    async def make(role: RoleCode) -> ActorContext:
        record = await repository.find_actor_by_account(
            connection, workspace_external_id=workspace, account_code=DEMO_ACCOUNTS[role]
        )
        if record is None:
            pytest.skip(f"目标库缺少演示账号 {DEMO_ACCOUNTS[role]}")
        assert record.context.role is role, (
            f"{DEMO_ACCOUNTS[role]} 的角色是 {record.context.role}，与预期 {role} 不符"
        )
        await set_request_context(connection, record.context)
        return record.context

    return make


@pytest_asyncio.fixture
async def sales_actor(actor_factory) -> ActorContext:
    return await actor_factory(RoleCode.SALES)


@pytest_asyncio.fixture
async def supervisor_actor(actor_factory) -> ActorContext:
    return await actor_factory(RoleCode.SUPERVISOR)


@pytest_asyncio.fixture
async def manager_actor(actor_factory) -> ActorContext:
    return await actor_factory(RoleCode.MANAGER)
