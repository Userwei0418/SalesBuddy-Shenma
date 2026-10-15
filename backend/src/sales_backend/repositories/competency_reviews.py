"""Frozen competency worker persistence, fenced by current permissions and lease."""

from copy import deepcopy
from decimal import Decimal

from sales_backend.domain.profile_scores import weighted_score
from sales_backend.job_context import JobLeaseLost, current_job_lease
from sales_backend.repositories.competency_lifecycle import (
    CALCULATION_VERSION,
    VISIT_JOINS,
    VISIT_PROJECTION,
    CompetencyInputChanged,
    CompetencySnapshotInvalid,
    assert_review_access,
    canonical_hash,
    plain,
    require_transaction,
    validate_snapshot,
)
from sales_backend.repositories.jobs import record_job_effect
from sales_backend.services.agent_platform.authorization_snapshot import recheck_connection

ROW_SQL = """SELECT id::text,workspace_id::text,subject_user_ref_id::text,review_date,
 framework_version,status,generation_no,input_fingerprint,input_snapshot,job_id::text,evaluation_state
 FROM insight.sales_competency_review WHERE id=$1::uuid AND workspace_id=$2::uuid
 AND subject_user_ref_id=$3::uuid"""
SOURCE_SQL = (
    "SELECT "  # noqa: S608 -- fixed internal projection, all values are parameters
    + VISIT_PROJECTION
    + """ FROM activity.visit v """
    + VISIT_JOINS
    + """
 WHERE v.workspace_id=$1::uuid AND v.recorder_user_ref_id=$2::uuid AND v.id=ANY($3::uuid[])
 AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')
 AND security.authorization_visit('visit.read',v.id)
 ORDER BY v.interaction_at DESC,v.created_at DESC,v.id DESC"""
)


def frozen_score_summary(values, policy):
    """Keep the existing score envelope, but never normalize partial weights."""
    summary = weighted_score({key: item.get("score") for key, item in values.items()}, policy)
    if summary["coverage_percent"] != 100:
        summary.update(value=None, text="--")
    summary["calculation"] = CALCULATION_VERSION

    def numbers(value):
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, dict):
            return {key: numbers(item) for key, item in value.items()}
        if isinstance(value, list):
            return [numbers(item) for item in value]
        return value

    return numbers(summary)


class CompetencyReviewRepository:
    async def _row(self, connection, actor, review_id, *, lock=False):
        require_transaction(connection)
        row = await connection.fetchrow(
            ROW_SQL + (" FOR UPDATE" if lock else ""), str(review_id), actor.workspace_id, actor.user_id
        )
        if not row:
            raise LookupError("competency review not found for current actor")
        row = dict(row)
        if (str(row["workspace_id"]), str(row["subject_user_ref_id"])) != (actor.workspace_id, actor.user_id):
            raise PermissionError("competency review does not belong to the current identity")
        row["input_snapshot"] = validate_snapshot(row["input_snapshot"], row["input_fingerprint"])
        return row

    async def _lease(self, connection, actor, row, *, lock=False):
        lease = current_job_lease.get()
        if lease is None or str(row["job_id"]) != str(lease.job_id):
            raise JobLeaseLost("competency review does not belong to the current lease")
        valid = await connection.fetchval(
            "SELECT 1 FROM ops.job WHERE id=$1::uuid\n"  # noqa: S608 -- only optional fixed FOR UPDATE
            """          AND workspace_id=$2::uuid AND aggregate_id=$3::uuid
          AND aggregate_type='sales_competency_review' AND job_type='sales_competency.review'
          AND lease_token=$4::uuid AND payload->>'user_id'=$5
          AND status='running' AND locked_until>clock_timestamp()
          AND security.workspace_job_enabled(workspace_id,job_type)"""
            + (" FOR UPDATE" if lock else ""),
            str(lease.job_id),
            actor.workspace_id,
            row["id"],
            str(lease.token),
            actor.user_id,
        )
        if not valid:
            raise JobLeaseLost("competency lease expired or superseded")

    async def _sources(self, connection, actor, row, *, lock=False):
        snapshot = row["input_snapshot"]
        ids = list(snapshot["source_hashes"])
        await assert_review_access(connection, actor, visit_ids=set(ids) | set(snapshot["previous_source_ids"]))
        await recheck_connection(connection, actor, snapshot["facts"]["scope"])
        if lock:
            # Lock the exact original records before the final fresh read so a
            # concurrent supplement cannot race between comparison and save.
            for table, field in [
                ("activity.visit", "visit_id"),
                ("crm.customer", "customer_id"),
                ("crm.opportunity", "opportunity_id"),
            ]:
                keys = sorted({item[field] for item in snapshot["facts"]["visits"] if item.get(field)})
                await connection.fetch(
                    f"SELECT id FROM {table} WHERE workspace_id=$1::uuid "  # noqa: S608 -- fixed table whitelist
                    "AND id=ANY($2::uuid[]) ORDER BY id FOR SHARE",
                    actor.workspace_id,
                    keys,
                )
        rows = await connection.fetch(SOURCE_SQL, actor.workspace_id, actor.user_id, ids)
        actual = {item["visit_id"]: canonical_hash(dict(item)) for item in rows}
        if actual != snapshot["source_hashes"]:
            raise CompetencyInputChanged("original competency sources changed or became inaccessible")

    async def assert_active(self, connection, actor, review_id, *, lock=False):
        row = await self._row(connection, actor, review_id, lock=lock)
        if row["status"] not in {"queued", "running", "failed"}:
            raise LookupError("competency review is no longer runnable")
        await self._lease(connection, actor, row, lock=lock)
        await self._sources(connection, actor, row, lock=lock)
        # A source row lock may have waited beyond this lease expiry.
        await self._lease(connection, actor, row, lock=lock)
        return row

    async def start(self, connection, actor, review_id):
        # Failed projections with a valid new queue lease are bounded retries of
        # the same generation. A terminal job cannot acquire that lease.
        row = await self.assert_active(connection, actor, review_id, lock=True)
        await connection.execute(
            """UPDATE insight.sales_competency_review SET status='running',
          error_code=NULL,error_detail=NULL WHERE id=$1::uuid AND workspace_id=$2::uuid
          AND subject_user_ref_id=$3::uuid AND status IN ('queued','running','failed')""",
            str(review_id),
            actor.workspace_id,
            actor.user_id,
        )
        row["status"] = "running"
        return row, deepcopy(row["input_snapshot"]["score_basis"]["framework_snapshot"])

    async def frozen_facts(self, connection, actor, review_id):
        row = await self.assert_active(connection, actor, review_id)
        snapshot = row["input_snapshot"]
        return deepcopy(snapshot["facts"]) | {
            "data_as_of": snapshot["data_as_of"],
            "evidence_coverage": deepcopy(snapshot["evidence_coverage"]),
        }

    async def facts(self, connection, actor, review_date, start, end):
        """Legacy read-only diagnostic API; workers must use frozen_facts."""
        await assert_review_access(connection, actor)
        from sales_backend.db import json_value
        from sales_backend.repositories.competency_lifecycle import CAPTURE_SQL

        row = await connection.fetchrow(CAPTURE_SQL, actor.workspace_id, actor.user_id, review_date, start, end)
        return json_value(row["visits"]), json_value(row["previous_review"])

    async def save(self, connection, actor, review_id, values, snapshot, model_ref):
        row = await self.assert_active(connection, actor, review_id, lock=True)
        if row["status"] != "running":
            raise LookupError("competency review is no longer writable")
        frozen = row["input_snapshot"]
        # Metadata is server-owned and deliberately separate from the input.
        # Old handlers supplying only trace metadata remain simple to adapt.
        allowed = {"review_id", "inference_route", "evaluation_state", "score_summary"}
        if not isinstance(snapshot, dict) or set(snapshot) - allowed:
            raise CompetencySnapshotInvalid("result metadata cannot replace frozen inputs")
        if snapshot.get("review_id", str(review_id)) != str(review_id):
            raise CompetencySnapshotInvalid("result metadata belongs to another review")
        state = values.get("evaluation_state")
        if state not in {"assessed", "partially_assessed", "insufficient_data"}:
            raise CompetencySnapshotInvalid("missing v2 competency evaluation state")
        summary = frozen_score_summary(values["dimension_scores"], frozen["score_basis"]["score_policy"])
        if snapshot.get("score_summary", summary) != summary:
            raise CompetencySnapshotInvalid("result score does not match frozen scoring policy")
        output_snapshot = deepcopy(frozen)
        output_snapshot.update(score_summary=summary)
        if "inference_route" in snapshot:
            output_snapshot["inference_route"] = plain(snapshot["inference_route"])
        saved = await connection.fetchval(
            """UPDATE insight.sales_competency_review
          SET status='succeeded',overall_score=$2,dimension_scores=$3::jsonb,summary=$4,
           strengths=$5::jsonb,improvements=$6::jsonb,evidence=$7::jsonb,input_snapshot=$8::jsonb,
           model_id=$9,reviewed_at=clock_timestamp(),error_code=NULL,error_detail=NULL,evaluation_state=$12
          WHERE id=$1::uuid AND subject_user_ref_id=$10::uuid AND workspace_id=$11::uuid
           AND status='running' AND input_fingerprint=$13 RETURNING id::text""",
            str(review_id),
            values["overall_score"],
            plain(values["dimension_scores"]),
            values["summary"],
            plain(values["strengths"]),
            plain(values["improvements"]),
            plain(values["evidence"]),
            output_snapshot,
            model_ref,
            actor.user_id,
            actor.workspace_id,
            state,
            row["input_fingerprint"],
        )
        if saved is None:
            raise LookupError("competency review is no longer writable")
        await record_job_effect(connection, actor.workspace_id)
