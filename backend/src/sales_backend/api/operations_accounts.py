import hashlib
import hmac
from uuid import UUID

from fastapi import APIRouter, Depends

from sales_backend.api.dependencies import RequestIdentity, get_database, get_settings
from sales_backend.api.idempotency import MutationKey
from sales_backend.api.management_dependencies import get_management_identity, get_system_identity
from sales_backend.config import Settings
from sales_backend.contracts.operations import (
    AccountCreate,
    AccountLoginUnlock,
    AccountUpdate,
    DepartmentSave,
    PasswordReset,
    PasswordPolicyUpdate,
)
from sales_backend.db import Database
from sales_backend.repositories.identity import ROLE_NAMES
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.services.operations import management_write
from sales_backend.services.operations_accounts import OperationsAccountService

router = APIRouter(prefix="/api/v1/console", tags=["Accounts and organization"])
repository = OperationsAccountRepository()
service = OperationsAccountService()


@router.get("/password-policy")
async def password_policy(
    identity: RequestIdentity = Depends(get_management_identity), database: Database = Depends(get_database)
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await repository.password_policy(connection)


@router.put("/password-policy")
async def update_password_policy(
    body: PasswordPolicyUpdate,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_system_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database, identity.actor, idempotency_key, "account.password_policy", body.model_dump(),
        lambda c: repository.set_password_policy(c, body.require_initial_change, body.version_no),
    )


def password_receipt(body, settings):
    payload = body.model_dump(exclude={"temporary_password"})
    # Keep the request receipt stable across retries without storing a password or a
    # dictionary-attackable unsalted password digest in the database.
    payload["credential_fingerprint"] = hmac.new(
        settings.access_token_secret.encode(), body.temporary_password.get_secret_value().encode(), hashlib.sha256
    ).hexdigest()
    return payload


@router.get("/organization")
async def organization(
    identity: RequestIdentity = Depends(get_management_identity), database: Database = Depends(get_database)
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        result = await repository.organization(connection)
        permissions = await AuthorizationRepository().effective(connection)
    return {
        **result,
        "roles": [
            {
                "code": code,
                "name": name,
                "assignable": permissions.allows("authorization.accounts_manage")
                or code not in {"operations", "administrator"},
            }
            for code, name in ROLE_NAMES.items()
        ],
    }


@router.post("/accounts", status_code=201)
async def create_account(
    body: AccountCreate,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
):
    payload = password_receipt(body, settings)
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        "account.create",
        payload,
        lambda c: service.create(c, identity.actor, payload, body.temporary_password.get_secret_value()),
    )


@router.put("/accounts/{user_id}")
async def update_account(
    user_id: UUID,
    body: AccountUpdate,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        f"account.update:{user_id}",
        body.model_dump(exclude_unset=True),
        lambda c: service.update(c, identity.actor, user_id, body.model_dump(exclude_unset=True)),
    )


@router.post("/accounts/{user_id}/reset-password")
async def reset_password(
    user_id: UUID,
    body: PasswordReset,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        f"account.password:{user_id}",
        password_receipt(body, settings),
        lambda c: service.reset_password(
            c, identity.actor, user_id, body.version_no, body.temporary_password.get_secret_value()
        ),
    )


@router.post("/accounts/{user_id}/unlock-login")
async def unlock_account_login(
    user_id: UUID,
    body: AccountLoginUnlock,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    """解除指定账号的登录限制；独立的网络登录保护保持不变。"""
    return await management_write(
        database, identity.actor, idempotency_key, f"account.unlock-login:{user_id}", body.model_dump(),
        lambda c: service.unlock_login(c, identity.actor, user_id, body.version_no, body.reason),
    )


@router.post("/departments", status_code=201)
async def create_department(
    body: DepartmentSave,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        "department.create",
        body.model_dump(),
        lambda c: repository.save_department(c, identity.actor, None, body.model_dump()),
    )


@router.put("/departments/{team_id}")
async def update_department(
    team_id: UUID,
    body: DepartmentSave,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        f"department.update:{team_id}",
        body.model_dump(),
        lambda c: repository.save_department(c, identity.actor, team_id, body.model_dump()),
    )
