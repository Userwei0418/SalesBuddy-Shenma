"""Authorized source archive endpoint; existing uploads provide the file bytes."""

from uuid import UUID

from fastapi import APIRouter, Depends

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.api.knowledge import errors, no_cache
from sales_backend.contracts.knowledge_source_files import SourceArchiveInput, SourceFilesView
from sales_backend.db import Database
from sales_backend.services.knowledge_source_files import KnowledgeSourceFilesService

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge-source-files"], dependencies=[Depends(no_cache)])


def service(database: Database = Depends(get_database)):
    return KnowledgeSourceFilesService(database)


@router.get("/materials/{material_id}/source-files", response_model=SourceFilesView)
async def source_files(
    material_id: UUID,
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeSourceFilesService = Depends(service),
):
    with errors():
        return await svc.get(identity.actor, material_id)


@router.post("/materials/{material_id}/source-files", response_model=SourceFilesView)
async def archive_source_files(
    material_id: UUID,
    body: SourceArchiveInput,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_identity),
    svc: KnowledgeSourceFilesService = Depends(service),
):
    with errors():
        return await svc.archive(identity.actor, idempotency_key, material_id, body)
