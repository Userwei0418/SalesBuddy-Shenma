"""Administrator-only connection configuration; validation never echoes secrets."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from sales_backend.api.dependencies import get_database, get_settings
from sales_backend.api.management_dependencies import get_system_identity
from sales_backend.domain.model_api import ConnectionPublish, ConnectionTest, Purpose
from sales_backend.services.model_api import ModelApiError, ModelApiService, receipt


class PrivateValidationRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except RequestValidationError:
                raise HTTPException(
                    422, "配置字段不符合要求，请检查完整 HTTPS 地址、协议、模型、密钥和数值范围"
                ) from None
            except ModelApiError as exc:
                raise HTTPException(exc.status, exc.message) from None

        return handler


router = APIRouter(
    prefix="/api/v1/admin/model-apis", tags=["Model API configuration"], route_class=PrivateValidationRoute
)


def service(database=Depends(get_database), settings=Depends(get_settings)):
    return ModelApiService(database, settings)


@router.get("")
async def list_connections(identity=Depends(get_system_identity), manager=Depends(service)):
    return await manager.list(identity.actor)


@router.get("/{purpose}/releases")
async def list_releases(purpose: Purpose, identity=Depends(get_system_identity), manager=Depends(service)):
    async with manager.database.transaction(identity.actor, readonly=True) as conn:
        return {"items": await manager.repo.releases(conn, identity.actor, purpose)}


@router.post("/{purpose}/tests")
async def test_connection(
    purpose: Purpose, body: ConnectionTest, identity=Depends(get_system_identity), manager=Depends(service)
):
    return await manager.test(identity.actor, purpose, body)


@router.get("/{purpose}/tests/{test_id}")
async def get_test(purpose: Purpose, test_id: str, identity=Depends(get_system_identity), manager=Depends(service)):
    from uuid import UUID

    try:
        UUID(test_id)
    except ValueError:
        raise HTTPException(422, "测试编号无效") from None
    async with manager.database.transaction(identity.actor, readonly=True) as conn:
        row = await manager.repo.test(conn, identity.actor, test_id)
        if not row or row["purpose"] != purpose or str(row["actor_user_ref_id"]) != identity.actor.user_id:
            raise HTTPException(404, "找不到测试回执")
        return receipt(row)


@router.post("/{purpose}/publish")
async def publish_connection(
    purpose: Purpose, body: ConnectionPublish, identity=Depends(get_system_identity), manager=Depends(service)
):
    return await manager.publish(identity.actor, purpose, body)
