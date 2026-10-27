"""Current, tenant-scoped material ACL and provider result boundaries.

These snapshots are trusted backend inputs. Callers must load them freshly in a
consistent transaction at enqueue, result delivery and download. Pure code does
not claim to detect a database change when given an old snapshot.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from sales_backend.contracts.knowledge import (
    KnowledgeAccessScope,
    KnowledgeAction,
    KnowledgeAllowedVersion,
    KnowledgeChunk,
    KnowledgeCitation,
    KnowledgeId,
    KnowledgeMaterial,
    KnowledgeMaterialVersion,
    KnowledgeModel,
    KnowledgeRetrievalRequest,
    KnowledgeStoredFile,
    Revision,
)


class KnowledgeActor(KnowledgeModel):
    company_id: KnowledgeId
    user_id: KnowledgeId
    team_ids: frozenset[KnowledgeId] = frozenset()
    authorization_revision: Revision
    active: bool = Field(strict=True)
    module_enabled: bool = Field(strict=True)
    permitted_actions: frozenset[KnowledgeAction] = frozenset()
    denied_actions: frozenset[KnowledgeAction] = frozenset()


class KnowledgeACLGrant(KnowledgeModel):
    subject_kind: Literal["user", "team", "company"]
    subject_id: KnowledgeId | None = None
    actions: frozenset[KnowledgeAction] = Field(min_length=1)

    @model_validator(mode="after")
    def subject_shape(self):
        if (self.subject_kind == "company") != (self.subject_id is None):
            raise ValueError("企业授权不携带主体 ID，用户/团队授权必须携带标准 ID")
        return self


class KnowledgeACL(KnowledgeModel):
    company_id: KnowledgeId
    material_id: KnowledgeId
    owner_user_id: KnowledgeId
    revision: Revision
    grants: tuple[KnowledgeACLGrant, ...] = ()
    denied_user_ids: frozenset[KnowledgeId] = frozenset()


class KnowledgeAccessEntry(KnowledgeModel):
    material: KnowledgeMaterial
    version: KnowledgeMaterialVersion
    acl: KnowledgeACL

    @model_validator(mode="after")
    def same_object(self):
        if not (self.material.company_id == self.version.company_id == self.acl.company_id
                and self.material.material_id == self.version.material_id == self.acl.material_id):
            raise ValueError("材料、版本和权限必须属于同一企业及同一材料")
        return self


class KnowledgeAccessError(PermissionError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def allows(actor: KnowledgeActor, acl: KnowledgeACL, action: KnowledgeAction) -> bool:
    # Business content_scope and CRM relationships are deliberately not inputs.
    if (not actor.active or not actor.module_enabled or actor.company_id != acl.company_id
            or action not in actor.permitted_actions or action in actor.denied_actions
            or actor.user_id in acl.denied_user_ids):
        return False
    if actor.user_id == acl.owner_user_id:
        return True
    return any(
        action in grant.actions and (
            grant.subject_kind == "company"
            or (grant.subject_kind == "user" and grant.subject_id == actor.user_id)
            or (grant.subject_kind == "team" and grant.subject_id in actor.team_ids)
        )
        for grant in acl.grants
    )


def require_access(actor: KnowledgeActor, entry: KnowledgeAccessEntry, action: KnowledgeAction) -> None:
    if entry.material.deleted or entry.version.state == "deleted" or not allows(actor, entry.acl, action):
        raise KnowledgeAccessError("KNOWLEDGE_ACCESS_DENIED", "当前身份无权访问该材料")


def require_file_access(actor: KnowledgeActor, entry: KnowledgeAccessEntry, file: KnowledgeStoredFile) -> None:
    """Original attachment access. Derived output must also revalidate its source scope."""
    require_access(actor, entry, "read")
    if (file.company_id != entry.material.company_id
            or file.file_id not in entry.version.contribution.selection.content.file_ids):
        raise KnowledgeAccessError("KNOWLEDGE_FILE_DENIED", "文件不属于当前获权材料版本")


def _read_snapshot(
    actor: KnowledgeActor, entries: Sequence[KnowledgeAccessEntry], *, observed_at: datetime,
    purpose: Literal["retrieval", "delivery"],
) -> KnowledgeAccessScope:
    """All explicitly selected entries must pass; do not silently broaden or drop any."""
    if (not actor.active or not actor.module_enabled
            or "read" not in actor.permitted_actions or "read" in actor.denied_actions):
        raise KnowledgeAccessError("KNOWLEDGE_ACCESS_DENIED", "当前身份无知识库读取权限")
    versions = []
    for entry in entries:
        require_access(actor, entry, "read")
        if purpose == "retrieval" and (
            entry.material.active_version_id != entry.version.version_id or entry.version.state != "retrievable"
        ):
            raise KnowledgeAccessError("KNOWLEDGE_VERSION_UNAVAILABLE", "检索只允许当前可检索版本")
        versions.append(KnowledgeAllowedVersion(
            material_id=entry.material.material_id, version_id=entry.version.version_id,
            acl_revision=entry.acl.revision,
        ))
    return KnowledgeAccessScope(
        purpose=purpose,
        company_id=actor.company_id, user_id=actor.user_id, authorization_revision=actor.authorization_revision,
        observed_at=observed_at, versions=tuple(versions),
    )


def freeze_read_scope(
    actor: KnowledgeActor, entries: Sequence[KnowledgeAccessEntry], *, observed_at: datetime,
) -> KnowledgeAccessScope:
    """New retrieval/generation must select current, retrievable versions."""
    return _read_snapshot(actor, entries, observed_at=observed_at, purpose="retrieval")


def revalidate_scope(
    frozen: KnowledgeAccessScope, actor: KnowledgeActor, entries: Sequence[KnowledgeAccessEntry], *,
    observed_at: datetime,
) -> KnowledgeAccessScope:
    """Reauthorize exact historical sources, returning a new delivery audit snapshot.

    A new active version or unrelated grant change is not revocation. The caller
    loads the original source versions, never substitutes today's active version.
    """
    current = _read_snapshot(actor, entries, observed_at=observed_at, purpose="delivery")
    original_refs = {(v.material_id, v.version_id) for v in frozen.versions}
    current_refs = {(v.material_id, v.version_id) for v in current.versions}
    if (frozen.company_id != current.company_id or frozen.user_id != current.user_id
            or current.observed_at < frozen.observed_at
            or original_refs != current_refs):
        raise KnowledgeAccessError("KNOWLEDGE_SCOPE_CHANGED", "材料或权限已变化，请重新发起任务")
    return current


def validate_chunks(request: KnowledgeRetrievalRequest, chunks: Sequence[KnowledgeChunk]) -> tuple[KnowledgeChunk, ...]:
    """Validate the whole response before any text enters the model or a cache."""
    expected = {d.provider_document_id: d.source for d in request.documents}
    seen = set()
    for chunk in chunks:
        identity = (chunk.provider_document_id, chunk.chunk_id)
        if expected.get(chunk.provider_document_id) != chunk.source or identity in seen:
            raise KnowledgeAccessError("KNOWLEDGE_REFERENCE_OUTSIDE_SCOPE", "检索引用不属于允许集合或存在歧义")
        seen.add(identity)
    return tuple(chunks)


def validate_citations(
    request: KnowledgeRetrievalRequest, chunks: Sequence[KnowledgeChunk], citations: Sequence[KnowledgeCitation],
) -> tuple[KnowledgeCitation, ...]:
    checked = validate_chunks(request, chunks)
    evidence = {(c.provider_document_id, c.chunk_id, c.source) for c in checked}
    for citation in citations:
        if (citation.provider_document_id, citation.chunk_id, citation.source) not in evidence:
            raise KnowledgeAccessError("KNOWLEDGE_REFERENCE_OUTSIDE_SCOPE", "回答引用未出现在本次授权检索证据中")
    return tuple(citations)
