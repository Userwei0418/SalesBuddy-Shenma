"""Explicit AI request; the original reference remains backend-owned."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.contracts.material_reference_explanation import (
    MaterialReferenceExplanationInput, MaterialReferenceExplanationView,
)
from sales_backend.db import Database
from sales_backend.services.material_reference_explanation import MaterialExplanationError, explain_material_reference
from sales_backend.services.material_reference_provider import finance_explanation_provider

router = APIRouter(tags=['Materials'])


@router.post('/api/v1/opportunities/{opportunity_id}/material-forecast-explanation',
             response_model=MaterialReferenceExplanationView)
async def explain_reference(opportunity_id: UUID, body: MaterialReferenceExplanationInput,
                            identity: RequestIdentity = Depends(get_identity),
                            database: Database = Depends(get_database)):
    try:
        return await explain_material_reference(database, identity.actor, opportunity_id,
            year=body.year, quarter=body.quarter, user_text=body.user_text,
            provider=finance_explanation_provider(database, identity.actor, database.settings))
    except MaterialExplanationError as exc:
        raise HTTPException(exc.status, detail={'code': exc.code, 'detail': exc.detail}) from None
