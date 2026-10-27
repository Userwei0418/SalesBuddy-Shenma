"""Append-only sales review requests. The caller owns the connection and transaction.

One SQL snapshot captures all model facts and rules. Neither this module nor the
worker reconstructs a generation from live facts after it has been queued.
"""

import hashlib
import json
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sales_backend.db import json_value
from sales_backend.domain.company_rules import policy_snapshot
from sales_backend.domain.competency_review import framework_definitions, review_window
from sales_backend.repositories.authorization_checks import require_permission
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.agent_platform.authorization_snapshot import read_scope

CONTRACT_VERSION = "sales.competency.v2"
CALCULATION_VERSION = "sales_competency_frozen_complete_v2"
TRIGGERS = {"manual", "visit.archived", "visit.supplemented"}


class CompetencySnapshotInvalid(ValueError):
    code = "COMPETENCY_SNAPSHOT_INVALID"
    retryable = False


class CompetencyInputChanged(PermissionError):
    code = "COMPETENCY_INPUT_CHANGED"
    retryable = False


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError('non-finite competency snapshot number')
        # Preserve numeric precision when a caller supplies a database Decimal.
        # Monetary source columns are projected as text below before JSONB can
        # pass through the shared float decoder.
        return int(value) if value == value.to_integral_value() else format(value, 'f').rstrip('0')
    raise TypeError("unsupported competency snapshot value")


def plain(value):
    return json.loads(json.dumps(value, default=_json_default, ensure_ascii=False, allow_nan=False))


def canonical_hash(value):
    def normalize(item):
        if isinstance(item, dict):
            return {key: normalize(part) for key, part in item.items()}
        if isinstance(item, list):
            return [normalize(part) for part in item]
        # JSONB loses numeric scale, while asyncpg preserves numeric as Decimal.
        if isinstance(item, float) and item.is_integer():
            return int(item)
        return item

    return hashlib.sha256(
        json.dumps(
            normalize(plain(value)), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def require_transaction(connection):
    if not connection.is_in_transaction():
        raise RuntimeError("competency persistence requires the caller transaction")


async def assert_review_access(connection, actor, *, visit_ids=()):
    """Identity is the requesting self, never a synthetic profile subject actor."""
    require_transaction(connection)
    await connection.execute("SELECT security.authorization_refresh()")
    if actor.role.value not in {"sales", "supervisor"}:
        raise PermissionError("仅销售本人可生成成长复盘")
    current = await IdentityRepository().find_actor_by_id(
        connection, workspace_id=actor.workspace_id, user_id=actor.user_id, role=actor.role.value
    )
    def normalize(value):
        return value.model_copy(update={"team_ids": tuple(sorted(value.team_ids))})
    if not current or normalize(current.context) != normalize(actor):
        raise PermissionError("当前身份或团队权限已变化")
    await require_permission(connection, "profile.sales_review")
    if not await connection.fetchval(
        "SELECT security.workspace_job_enabled($1::uuid,'sales_competency.review')", actor.workspace_id
    ):
        raise PermissionError("当前公司未启用 AI 复盘能力")
    for visit_id in visit_ids:
        await require_permission(connection, "visit.read", visit_id=visit_id)


async def can_review(connection, actor):
    try:
        await assert_review_access(connection, actor)
    except PermissionError:
        return False
    return True


# The same projection is used when checking original source rows at worker
# boundaries. No unrestricted row_to_json(v), customer attributes or credentials.
VISIT_PROJECTION = """v.id::text AS visit_id,v.version_no,
 to_char(v.interaction_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US')||'+00:00' AS interaction_at,
 to_char(v.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US')||'+00:00' AS created_at,
 c.id::text AS customer_id,c.name AS customer_name,
 o.id::text AS opportunity_id,o.name AS opportunity_name,o.amount::text AS opportunity_amount,
 v.partner_name_snapshot,v.lead_source_snapshot,v.contact_category_snapshot,v.contact_title_snapshot,
 v.contact_name_snapshot,v.interaction_mode_code,v.visit_location,v.duration_minutes,v.expectation_code,
 v.follow_up_record,v.next_action"""
VISIT_JOINS = """LEFT JOIN crm.customer c ON c.id=v.customer_id AND c.workspace_id=v.workspace_id
 AND security.authorization_customer('customer.read',c.id)
 LEFT JOIN crm.opportunity o ON o.id=v.opportunity_id AND o.workspace_id=v.workspace_id
 AND security.authorization_opportunity('opportunity.read',o.id)"""
CAPTURE_SQL = (
    "WITH eligible AS MATERIALIZED (\n SELECT "  # noqa: S608 -- only fixed internal SQL projections
    + VISIT_PROJECTION
    + """ FROM activity.visit v """
    + VISIT_JOINS
    + """
 WHERE v.workspace_id=$1::uuid AND v.recorder_user_ref_id=$2::uuid AND v.deleted_at IS NULL
 AND v.status IN ('confirmed','archived') AND v.interaction_at >=$4::timestamptz
 AND v.interaction_at<$5::timestamptz AND security.authorization_visit('visit.read',v.id)
), selected AS (SELECT * FROM eligible ORDER BY interaction_at DESC,created_at DESC,visit_id DESC LIMIT 80)
SELECT
 (SELECT to_jsonb(f) FROM (SELECT version_no,display_name,dimensions,scoring_rules
   FROM config.sales_competency_framework WHERE status='active'
   AND (workspace_id=$1::uuid OR workspace_id IS NULL)
   AND effective_from<=statement_timestamp() AND effective_to>statement_timestamp()
   ORDER BY (workspace_id IS NOT NULL) DESC,version_no DESC,id DESC LIMIT 1) f) AS framework,
 (SELECT to_jsonb(r) FROM config.rule_set r WHERE id=security.active_company_rule('score.competency')) AS score_policy,
 (SELECT to_jsonb(r) FROM config.rule_set r
   WHERE id=security.active_company_rule('agent_business.competency_review')) AS business_rules,
 (SELECT jsonb_build_object('version',revision_no,'text',COALESCE(prompt_overrides->>'competency_review',''))
   FROM config.agent_runtime_config WHERE workspace_id=$1::uuid AND enabled) AS prompt_override,
 COALESCE((SELECT jsonb_agg(to_jsonb(s) ORDER BY interaction_at DESC,created_at DESC,visit_id DESC)
   FROM selected s),'[]'::jsonb) AS visits,
 (SELECT count(*) FROM eligible) AS total_eligible_visits,
 (SELECT to_jsonb(p) FROM (SELECT id::text,review_date,generation_no,framework_version,
   overall_score,dimension_scores,summary,evaluation_state,
   input_snapshot->'source_hashes' AS _source_hashes,input_snapshot->'previous_source_ids' AS _previous_source_ids
   FROM insight.sales_competency_review WHERE workspace_id=$1::uuid AND subject_user_ref_id=$2::uuid
   AND status='succeeded' AND review_date<$3::date
   ORDER BY review_date DESC,generation_no DESC,id DESC LIMIT 1) p) AS previous_review,
 security.authorization_snapshot(NULL::uuid) AS authorization,
 statement_timestamp() AS data_as_of"""
)


def freeze_input(raw, actor, review_date):
    raw = {
        key: json_value(value)
        if isinstance(value, str)
        and key
        in {
            "framework",
            "score_policy",
            "business_rules",
            "prompt_override",
            "visits",
            "previous_review",
            "authorization",
        }
        else value
        for key, value in dict(raw).items()
    }
    framework = plain(raw["framework"])
    if not framework:
        raise CompetencySnapshotInvalid("active competency framework missing")
    try:
        framework_definitions(framework)
    except ValueError as exc:
        raise CompetencySnapshotInvalid("invalid competency framework") from exc
    rules = framework.get("scoring_rules") or {}
    minimum = rules.get("minimum_visit_count", 1)
    if type(minimum) is not int or not 1 <= minimum <= 80 or rules.get("evidence_required", True) is not True:
        raise CompetencySnapshotInvalid("invalid competency evidence rules")
    score_policy = policy_snapshot("score.competency", raw.get("score_policy"))
    business_rules = policy_snapshot("agent_business.competency_review", raw.get("business_rules"))
    prompt = plain(raw.get("prompt_override") or {"version": 0, "text": ""})
    start, end = review_window(review_date)
    visits = plain(raw["visits"])
    total = raw["total_eligible_visits"]
    if type(total) is not int or total < len(visits) or len(visits) != min(total, 80):
        raise CompetencySnapshotInvalid("inconsistent competency coverage")
    scope = read_scope(raw["authorization"], actor, personal=True)
    prior = plain(raw.get("previous_review"))
    previous_source_ids = []
    if prior:
        previous_source_ids = sorted(
            set(prior.pop("_source_hashes", None) or {}) | set(prior.pop("_previous_source_ids", None) or [])
        )
    facts = {
        "subject_user_ref_id": actor.user_id,
        "review_date": review_date.isoformat(),
        "window_days": 30,
        "business_timezone": "Asia/Shanghai",
        "visits": visits,
        "previous_review": prior,
        "scope": scope,
        "agent_business_policy": business_rules,
    }
    coverage = {
        "status": "known",
        "window_start": start.isoformat(),
        "window_end_exclusive": end.isoformat(),
        "business_timezone": "Asia/Shanghai",
        "total_eligible_visits": total,
        "used_visits": len(visits),
        "limit": 80,
        "truncated": total > 80,
        "selection": "interaction_at_desc_created_at_desc_id_desc",
    }
    basis = {
        "framework_version": framework["version_no"],
        "framework_hash": canonical_hash(framework),
        "framework_snapshot": framework,
        "score_policy": score_policy,
        "calculation_version": CALCULATION_VERSION,
        "snapshot_status": "frozen",
    }
    basis["basis_id"] = canonical_hash({"basis": basis, "business_rules": business_rules, "prompt_override": prompt})
    snapshot = {
        "contract_version": CONTRACT_VERSION,
        "facts": facts,
        "score_basis": basis,
        "business_rules": business_rules,
        "prompt_override": prompt,
        "evidence_coverage": coverage,
        "previous_source_ids": previous_source_ids,
        "source_hashes": {row["visit_id"]: canonical_hash(row) for row in visits},
    }
    snapshot["input_fingerprint"] = canonical_hash(snapshot)
    snapshot["data_as_of"] = plain(raw["data_as_of"])
    return snapshot


def validate_snapshot(snapshot, fingerprint):
    snapshot = deepcopy(json_value(snapshot))
    if not isinstance(snapshot, dict) or snapshot.get("contract_version") != CONTRACT_VERSION:
        raise CompetencySnapshotInvalid("legacy or missing frozen competency input")
    immutable_keys = {
        "contract_version",
        "facts",
        "score_basis",
        "business_rules",
        "prompt_override",
        "evidence_coverage",
        "source_hashes",
        "previous_source_ids",
    }
    try:
        actual = canonical_hash({key: snapshot[key] for key in immutable_keys})
        source_hashes = {row["visit_id"]: canonical_hash(row) for row in snapshot["facts"]["visits"]}
    except (KeyError, TypeError, ValueError) as exc:
        raise CompetencySnapshotInvalid("invalid frozen competency input") from exc
    if (
        actual != fingerprint
        or snapshot.get("input_fingerprint") != fingerprint
        or source_hashes != snapshot["source_hashes"]
    ):
        raise CompetencySnapshotInvalid("frozen competency input hash mismatch")
    return snapshot


def receipt(row, *, reused):
    return {
        key: (str(row[key]) if key in {"review_id", "job_id", "retry_of_review_id"} and row.get(key) else row.get(key))
        for key in (
            "review_id",
            "status",
            "generation_no",
            "trigger_type",
            "requested_at",
            "input_fingerprint",
            "retry_of_review_id",
            "job_id",
        )
    } | {"reused": reused}


async def request_review(
    connection, actor, *, trigger_type="manual", trigger_id=None, trigger_version=None, review_date=None
):
    require_transaction(connection)
    if trigger_type not in TRIGGERS:
        raise ValueError("invalid competency trigger")
    if trigger_type == "manual":
        if trigger_id is not None or trigger_version is not None:
            raise ValueError("manual review cannot supply a source trigger")
    elif trigger_id is None or type(trigger_version) is not int or trigger_version < 1:
        raise ValueError("visit review requires its persisted trigger and version")
    await assert_review_access(connection, actor)
    review_date = review_date or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    await connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", f"competency:{actor.workspace_id}:{actor.user_id}"
    )
    if trigger_id is not None:
        trigger_id = str(UUID(str(trigger_id)))
        valid = await connection.fetchval(
            """SELECT 1 FROM activity.visit WHERE id=$1::uuid
         AND workspace_id=$2::uuid AND recorder_user_ref_id=$3::uuid AND version_no=$4
         AND deleted_at IS NULL AND status IN ('confirmed','archived')
         AND security.authorization_visit('visit.read',id)""",
            trigger_id,
            actor.workspace_id,
            actor.user_id,
            trigger_version,
        )
        if not valid:
            raise CompetencyInputChanged("competency trigger no longer current")
    start, end = review_window(review_date)
    args = (actor.workspace_id, actor.user_id, review_date, start, end)
    snapshot = freeze_input(await connection.fetchrow(CAPTURE_SQL, *args), actor, review_date)
    # A second atomic read fences changed facts/rules while waiting on the
    # subject lock; timestamps deliberately do not create another generation.
    await assert_review_access(
        connection, actor, visit_ids=set(snapshot["source_hashes"]) | set(snapshot["previous_source_ids"])
    )
    checked = freeze_input(await connection.fetchrow(CAPTURE_SQL, *args), actor, review_date)
    if checked["input_fingerprint"] != snapshot["input_fingerprint"]:
        raise CompetencyInputChanged("competency input changed before enqueue")
    previous = await connection.fetchrow(
        """SELECT r.id::text AS review_id,r.status,r.generation_no,
      r.trigger_type,r.created_at AS requested_at,r.input_fingerprint,r.retry_of_review_id::text,r.job_id::text,
      j.status AS job_status FROM insight.sales_competency_review r
      LEFT JOIN ops.job j ON j.id=r.job_id AND j.workspace_id=r.workspace_id
       AND j.aggregate_id=r.id AND j.aggregate_type='sales_competency_review' AND j.job_type='sales_competency.review'
      WHERE r.workspace_id=$1::uuid AND r.subject_user_ref_id=$2::uuid AND r.review_date=$3
       AND r.input_fingerprint=$4 ORDER BY r.generation_no DESC LIMIT 1 FOR UPDATE OF r""",
        actor.workspace_id,
        actor.user_id,
        review_date,
        snapshot["input_fingerprint"],
    )
    if previous and previous["job_status"] is None:
        raise CompetencySnapshotInvalid("frozen competency request has no matching job")
    if previous and (
        previous["status"] == "succeeded"
        or previous["job_status"] in {"queued", "running", "failed"}
        or trigger_type != "manual"
    ):
        return receipt(previous, reused=True)
    retry_of = previous["review_id"] if previous else None
    review_id, job_id = str(uuid4()), str(uuid4())
    row = await connection.fetchrow(
        """INSERT INTO insight.sales_competency_review
      (id,workspace_id,subject_user_ref_id,review_date,framework_version,status,generation_no,
       trigger_type,trigger_id,trigger_version,input_fingerprint,retry_of_review_id,input_snapshot,job_id)
      SELECT $1::uuid,$2::uuid,$3::uuid,$4,$5,'queued',COALESCE(max(generation_no),0)+1,
       $6,$7::uuid,$8,$9,$10::uuid,$11::jsonb,$12::uuid FROM insight.sales_competency_review
      WHERE workspace_id=$2::uuid AND subject_user_ref_id=$3::uuid AND review_date=$4
      RETURNING id::text AS review_id,status,generation_no,trigger_type,created_at AS requested_at,
       input_fingerprint,retry_of_review_id::text,job_id::text""",
        review_id,
        actor.workspace_id,
        actor.user_id,
        review_date,
        snapshot["score_basis"]["framework_version"],
        trigger_type,
        trigger_id,
        trigger_version,
        snapshot["input_fingerprint"],
        retry_of,
        snapshot,
        job_id,
    )
    await connection.execute(
        """INSERT INTO ops.job(id,workspace_id,job_type,aggregate_type,aggregate_id,payload,priority)
      VALUES($1::uuid,$2::uuid,'sales_competency.review','sales_competency_review',$3::uuid,$4::jsonb,45)""",
        job_id,
        actor.workspace_id,
        review_id,
        actor.model_dump(mode="json"),
    )
    return receipt(row, reused=False)
