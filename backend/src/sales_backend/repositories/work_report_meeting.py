"""Meeting persistence uses existing private report lifecycle, without a model run."""

from uuid import uuid4

from sales_backend.contracts.work_report import ResolvedScope
from sales_backend.contracts.work_report_meeting import MeetingSelection, MeetingSnapshotView
from sales_backend.domain.work_report import WorkReportError, canonical_hash
from sales_backend.domain.work_report_meeting_document import render
from sales_backend.repositories.work_report import WorkReportRepository, decoded, dump
from sales_backend.repositories.work_report_meeting_facts import assert_dependencies


def legacy_scope(scope):
    return ResolvedScope(
        kind="team" if scope.kind == "managed_team" else "self",
        team_id=scope.team_id,
        member_ids=scope.member_ids,
        label=scope.label,
    )


def view(row):
    payload = row["payload"] or {}
    selection = {k: v for k, v in row["selection"].items() if k != "sources"}
    facts = payload.get("meeting_facts")
    scope = facts["scope"] if facts else {**row["scope"], "kind": selection["scope"]["kind"]}
    scope = {k: v for k, v in scope.items() if k != "member_id"}
    return MeetingSnapshotView(
        id=row["id"],
        status=row["status"],
        selection=selection,
        period=row["period"],
        scope=scope,
        created_at=row["created_at"],
        data_as_of=row["data_as_of"],
        snapshot_hash=row["snapshot_hash"],
        meeting_facts=facts,
        error=row["error"],
    )


class MeetingRepository(WorkReportRepository):
    async def clock(self, connection):
        return await connection.fetchval("SELECT transaction_timestamp()")

    async def snapshot(self, connection, actor, snapshot_id):
        await self._enter(connection, actor)
        row = decoded(
            await connection.fetchrow(
                """SELECT * FROM agent.work_report_snapshot
          WHERE id=$1::uuid AND workspace_id=$2::uuid AND owner_user_ref_id=$3::uuid
           AND selection->>'facts_contract'='work_report.meeting_facts.v1'""",
                str(snapshot_id),
                actor.workspace_id,
                actor.user_id,
            )
        )
        if row["selection"].get("facts_contract") != "work_report.meeting_facts.v1":
            raise WorkReportError("WORK_REPORT_NOT_FOUND", "会议快照不存在", status=404)
        result = view(row)
        if result.status == "ready":
            if (
                canonical_hash(row["payload"]) != result.snapshot_hash
                or dump(legacy_scope(result.scope)) != row["scope"]
            ):
                raise WorkReportError("WORK_REPORT_SNAPSHOT_CHANGED", "会议快照摘要或范围不一致")
            await assert_dependencies(connection, row["source_manifest"])
        return row, result

    async def prepare(self, connection, actor, selection: MeetingSelection, facts, dependencies):
        await self._enter(connection, actor, "work_report.write")
        await assert_dependencies(connection, dependencies, permission="work_report.write")
        scope = legacy_scope(facts.scope)
        stored_selection = {**dump(selection), "sources": ["visit", "opportunity", "task"]}
        row = decoded(
            await connection.fetchrow(
                """INSERT INTO agent.work_report_snapshot
          (workspace_id,owner_user_ref_id,selection,period,scope,job_id)
          VALUES($1::uuid,$2::uuid,$3::jsonb,$4::jsonb,$5::jsonb,$6::uuid) RETURNING *""",
                actor.workspace_id,
                actor.user_id,
                stored_selection,
                dump(facts.period),
                dump(scope),
                None,
            )
        )
        await self._event(connection, actor, "snapshot", row["id"], "created", status="preparing")
        payload = dict(
            period=dump(facts.period),
            scope=dump(scope),
            coverage=[],
            metrics=[],
            sources=[],
            meeting_facts=dump(facts),
            meeting_dependencies=dependencies,
        )
        row = decoded(
            await connection.fetchrow(
                """UPDATE agent.work_report_snapshot SET status='ready',payload=$4::jsonb,
          snapshot_hash=$5,data_as_of=$6 WHERE id=$1::uuid AND workspace_id=$2::uuid
           AND owner_user_ref_id=$3::uuid AND status='preparing' RETURNING *""",
                str(row["id"]),
                actor.workspace_id,
                actor.user_id,
                payload,
                canonical_hash(payload),
                facts.data_as_of,
            )
        )
        await self._event(
            connection, actor, "snapshot", row["id"], "ready", status="ready", snapshot_hash=row["snapshot_hash"]
        )
        return view(row)

    async def save(self, connection, actor, body):
        await self._enter(connection, actor, "work_report.write")
        row, snapshot = await self.snapshot(connection, actor, body.snapshot_id)
        if snapshot.status != "ready" or snapshot.snapshot_hash != body.snapshot_hash:
            raise WorkReportError("WORK_REPORT_SNAPSHOT_CHANGED", "会议快照未就绪或已经变化")
        await self._actions(connection, actor, row["source_manifest"], row["scope"], "work_report.write")
        content, scheme = render(snapshot.meeting_facts, body.title, body.builtin_scheme_id)
        document_id, revision_id = uuid4(), uuid4()
        await connection.execute(
            """INSERT INTO agent.work_report_document
          (id,workspace_id,owner_user_ref_id,snapshot_id,snapshot_hash,context,source_manifest,
           selected_sources,latest_revision_id)
          VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5,'{}','[]','[]',$6::uuid)""",
            str(document_id),
            actor.workspace_id,
            actor.user_id,
            str(snapshot.id),
            snapshot.snapshot_hash,
            str(revision_id),
        )
        await self._insert_revision(connection, actor, document_id, revision_id, 1, content, scheme, "basic_summary")
        await self._event(
            connection,
            actor,
            "document",
            document_id,
            "created",
            revision_id=str(revision_id),
            content_hash=canonical_hash(content),
        )
        return await self.get_document(connection, actor, document_id)
