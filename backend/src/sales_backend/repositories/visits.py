from __future__ import annotations

import uuid

from sales_backend.contracts.visit_schema import FIELDS, FIRST_VISIT_KEYS, FORM_VERSION_ID, schema_snapshot
from sales_backend.db import json_value
from sales_backend.domain.visit_contract import VISIT_FIELDS, quality_grade, validate_content
from sales_backend.domain.visit_dates import business_date, visit_date_fields
from sales_backend.repositories.collaboration import validated_fde, write_visit_participants
from sales_backend.repositories.directory import DirectoryRepository
from sales_backend.repositories.visit_normalize import _interaction_datetime


async def validate_collaborators(connection, actor, ids):
    if not ids:
        return []
    rows = await DirectoryRepository().colleagues(connection, actor, ids=ids)
    if len(rows) != len(ids):
        raise ValueError("协同人须为公司内有效的非 FDE 人员，请重新选择；FDE 请在商机的协助 FDE 中选择")
    return [{"id": row["id"], "display_name": row["name"]} for row in rows]


class VisitRepository:
    async def archived_counts(self, connection, detail):
        keys = await connection.fetch(
            "SELECT d.field_key FROM config.form_version_field f JOIN config.field_definition d "
            "ON d.id=f.field_definition_id WHERE f.form_version_id=$1::uuid",
            detail["form_version_id"],
        )
        snapshot = detail.get("archived_fields") or detail
        return {"completed_count": sum(bool(snapshot.get(r["field_key"])) for r in keys), "total_count": len(keys)}

    async def form_schema(self, connection):
        snapshot = json_value(
            await connection.fetchval(
                "SELECT schema_snapshot FROM config.form_version WHERE id=$1::uuid", FORM_VERSION_ID
            )
        )
        if snapshot != schema_snapshot():
            raise RuntimeError("拜访表单版本未部署或契约不一致")
        return snapshot["fields"]

    async def create(self, connection, actor, *, customer_id, fields):
        clean = validate_content(fields)
        fde_people = await validated_fde(connection, fields.get("_fde_participant_ids", []))
        clean["fde_participant_ids"] = [p["id"] for p in fde_people]
        for key in ("interaction_at", "created_date", "contact_name"):
            if not clean[key]:
                raise ValueError(f"请补充{VISIT_FIELDS[key]}")
        for key in ("interaction_at", "created_date"):
            value = business_date(clean[key])
            if value is None:
                raise ValueError(f"{VISIT_FIELDS[key]}日期格式不正确")
            clean[key] = value.isoformat()
        # Verify the immutable version before linking values to its definitions.
        await self.form_schema(connection)
        customer = json_value(
            await connection.fetchval(
                "SELECT security.customer_reference($1::uuid)",
                customer_id,
            )
        )
        if not customer:
            raise LookupError("客户不存在或不在当前权限范围内")
        opportunity_id = clean["opportunity_id"]
        if opportunity_id:
            opportunity = await connection.fetchrow(
                "SELECT id::text,name FROM crm.opportunity WHERE id=$1::uuid AND customer_id=$2::uuid "
                "AND deleted_at IS NULL",
                opportunity_id,
                customer_id,
            )
            if not opportunity:
                raise ValueError("商机不属于当前客户，请重新关联")
            clean["opportunity_name"] = opportunity["name"]
        else:
            clean["opportunity_name"] = ""
        collaborators = await validate_collaborators(connection, actor, clean["collaborator_ids"])
        if clean["source_import_id"]:
            owned = await connection.fetchval(
                """SELECT id FROM activity.visit_import WHERE id=$1::uuid AND created_by_user_ref_id=$2::uuid AND
                status='succeeded'""",
                clean["source_import_id"],
                actor.user_id,
            )
            if not owned:
                raise ValueError("上传材料不存在或尚未处理完成")
        recorder = await connection.fetchval(
            "SELECT display_name FROM platform.user_ref WHERE id=$1::uuid", actor.user_id
        )
        clean.update(
            customer_name=customer["name"],
            customer_type=fields.get("customer_type")
            if fields.get("customer_type") in {"客户", "伙伴"}
            else customer["customer_type_code"] or "",
            recorder_user_id=recorder,
        )
        visit_id = str(uuid.uuid4())
        score = fields["_follow_up_quality_score"]
        quality = {
            "follow_up_score": score,
            "grade": quality_grade(score, (fields.get("_company_policy") or {}).get("definition")),
            "company_policy": fields.get("_company_policy"),
            "next_action_passed": True,
            "reviewed_by": "visit_entry_agent",
        }
        await connection.execute(
            """INSERT INTO activity.visit (
            id,workspace_id,customer_id,opportunity_id,recorder_user_ref_id,recorder_team_id,form_version_id,
            status,interaction_at,interaction_mode_code,visit_location,follow_up_record,next_action,
            partner_name_snapshot,contact_title_snapshot,contact_name_snapshot,visit_goal,follow_up_score,
            quality_review,source_import_id,confirmed_by_user_ref_id,confirmed_at,archived_at,
            customer_type_code_snapshot,created_by_user_ref_id,is_first_visit,first_visit_profile,recorded_on,archived_fields)
            VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$6::uuid,$7::uuid,'archived',$8,$9,$10,$11,$12,
                    $13,$14,$15,$16,$17,$18::jsonb,$19::uuid,$5::uuid,clock_timestamp(),clock_timestamp(),$20,$5::uuid,$21,$22::jsonb,$23,$24::jsonb)""",
            visit_id,
            actor.workspace_id,
            customer_id,
            opportunity_id,
            actor.user_id,
            actor.team_ids[0] if actor.team_ids else customer["owner_team_id"],
            FORM_VERSION_ID,
            _interaction_datetime(clean["interaction_at"]) if clean["interaction_at"] else None,
            clean["interaction_mode"],
            clean["visit_location"],
            clean["follow_up_record"],
            clean["next_action"],
            clean["partner_name"],
            clean["contact_title"],
            clean["contact_name"],
            clean["visit_goal"],
            score,
            quality,
            clean["source_import_id"],
            customer["customer_type_code"],
            clean["is_first_visit"],
            {key: clean[key] for key in FIRST_VISIT_KEYS} if clean["is_first_visit"] else {},
            business_date(clean["created_date"]),
            clean,
        )
        await write_visit_participants(connection, actor, visit_id, clean["collaborator_ids"], fde_people)
        definitions = await connection.fetch(
            "SELECT d.id::text,d.field_key FROM config.form_version_field f "
            "JOIN config.field_definition d ON d.id=f.field_definition_id WHERE f.form_version_id=$1::uuid",
            FORM_VERSION_ID,
        )
        for definition in definitions:
            value = clean.get(definition["field_key"])
            if value:
                await connection.execute(
                    """INSERT INTO activity.visit_field_value
                    (visit_id,workspace_id,field_definition_id,value_json,value_text,source_type,is_confirmed)
                    VALUES($1::uuid,$2::uuid,$3::uuid,$4::jsonb,$5,'user',true)""",
                    visit_id,
                    actor.workspace_id,
                    definition["id"],
                    value,
                    str(value),
                )
        populated = sum(bool(clean.get(f.field_key)) for f in FIELDS)
        return {
            "id": visit_id,
            "status": "archived",
            "customer_id": customer_id,
            "customer_name": customer["name"],
            "opportunity_id": opportunity_id,
            "completed_count": populated,
            "total_count": len(FIELDS),
            "fields": clean,
            "quality_review": quality,
            "collaborators": collaborators,
            "fde_participants": fde_people,
        }

    async def detail(self, connection, visit_id):
        row = await connection.fetchrow(
            """SELECT v.id::text,v.customer_id::text,
            COALESCE(v.archived_fields->>'customer_name',
                     security.customer_reference(v.customer_id)->>'name') AS customer_name,
            v.visit_goal,v.follow_up_record,v.next_action,v.interaction_at,v.interaction_mode_code AS interaction_mode,
            v.status,v.expectation_code,v.follow_up_score,v.duration_minutes,v.interaction_mode_code,
            v.partner_name_snapshot,v.contact_name_snapshot,v.partner_id::text,
            v.original_recorder_name,manager.display_name AS manager_name,
            COALESCE((SELECT jsonb_agg(jsonb_build_object('id',linked.id::text,'name',linked.name)
              ORDER BY linked.name,linked.id) FROM activity.visit_opportunity vo
              JOIN crm.opportunity linked ON linked.id=vo.opportunity_id AND linked.deleted_at IS NULL
              WHERE vo.visit_id=v.id),'[]'::jsonb) AS linked_opportunities,
            COALESCE((SELECT jsonb_agg(jsonb_build_object('id',cu.id,'name',cu.display_name))
              FROM activity.visit_participant vp JOIN platform.user_ref cu ON cu.id=vp.user_ref_id
              WHERE vp.visit_id=v.id AND vp.participant_role='collaborator'), '[]'::jsonb) AS collaborators,
            COALESCE((SELECT jsonb_agg(jsonb_build_object('id',cu.id,'name',cu.display_name,
              'team',team.name,'team_id',vp.team_id_at_event,'role',vp.role_code_at_event))
              FROM activity.visit_participant vp JOIN platform.user_ref cu ON cu.id=vp.user_ref_id
              LEFT JOIN platform.team team ON team.id=vp.team_id_at_event
              WHERE vp.visit_id=v.id AND vp.participant_role='fde'), '[]'::jsonb) AS fde_participants,
            ARRAY(SELECT vp.user_ref_id::text FROM activity.visit_participant vp
              WHERE vp.visit_id=v.id AND vp.participant_role='fde') AS fde_participant_ids,
            v.visit_location,COALESCE(partner.name,v.partner_name_snapshot) AS partner_name,
            v.contact_name_snapshot AS contact_name,
            v.contact_title_snapshot AS contact_title,
            ARRAY(SELECT vp.user_ref_id::text FROM activity.visit_participant vp
              WHERE vp.visit_id=v.id AND vp.participant_role='collaborator') AS collaborator_ids,
            v.created_at,v.recorded_on,v.archived_fields,v.form_version_id::text,v.is_first_visit,v.first_visit_profile,
            v.version_no,v.quality_review,v.opportunity_id::text,o.name AS opportunity_name,
            u.display_name AS recorder_name,v.recorder_user_ref_id::text AS recorder_id,
            creator.display_name AS creator_name,v.created_by_user_ref_id::text AS creator_id,
            v.customer_type_code_snapshot AS customer_type
            FROM activity.visit v
            LEFT JOIN platform.user_ref u ON u.id=v.recorder_user_ref_id
            LEFT JOIN platform.user_ref manager ON manager.id=v.manager_user_ref_id
            LEFT JOIN crm.partner partner ON partner.id=v.partner_id
            LEFT JOIN platform.user_ref creator ON creator.id=v.created_by_user_ref_id
            LEFT JOIN crm.opportunity o ON o.id=v.opportunity_id
            WHERE v.id=$1::uuid AND v.deleted_at IS NULL""",
            visit_id,
        )
        if not row:
            raise LookupError("拜访记录不存在或不可见")
        item = dict(row)
        item.update(json_value(item.get("first_visit_profile")) or {})
        return visit_date_fields(item)

    async def supplement(self, connection, actor, visit_id, data):
        allowed = {
            "partner_name",
            "contact_name",
            "contact_title",
            "interaction_at",
            "interaction_mode",
            "visit_location",
            "collaborator_ids",
            "version_no",
            "created_date",
            "is_first_visit",
            *FIRST_VISIT_KEYS,
        }
        if set(data) - allowed:
            raise ValueError("补充入口只允许修改辅助信息，正文变化须重新审核")
        row = await connection.fetchrow(
            """SELECT recorder_user_ref_id::text,version_no,is_first_visit,first_visit_profile
            FROM activity.visit WHERE id=$1::uuid AND deleted_at IS
            NULL FOR UPDATE""",
            visit_id,
        )
        if not row:
            raise LookupError("记录不存在或不可见")
        if row["recorder_user_ref_id"] != actor.user_id:
            raise ValueError("仅跟进人可以补充本次记录")
        if data.get("version_no") != row["version_no"]:
            raise ValueError("记录已更新，请刷新后再补充")
        columns = {
            "partner_name": "partner_name_snapshot",
            "contact_name": "contact_name_snapshot",
            "contact_title": "contact_title_snapshot",
            "interaction_at": "interaction_at",
            "interaction_mode": "interaction_mode_code",
            "visit_location": "visit_location",
        }
        args = [visit_id]
        sets = []
        if any(key in data for key in FIRST_VISIT_KEYS) or "is_first_visit" in data:
            merged = {**(json_value(row["first_visit_profile"]) or {}), **data}
            if data.get("is_first_visit", row["is_first_visit"]) != row["is_first_visit"]:
                raise ValueError("归档后的拜访类型不可更改")
            checked = validate_content(
                {
                    **merged,
                    "is_first_visit": row["is_first_visit"],
                    "follow_up_record": "supplement",
                    "next_action": "supplement",
                }
            )
            args.append({key: checked[key] for key in FIRST_VISIT_KEYS} if row["is_first_visit"] else {})
            sets.append(f"first_visit_profile=${len(args)}::jsonb")
        if "created_date" in data:
            recorded_on = business_date(data["created_date"])
            if recorded_on is None:
                raise ValueError("请选择销售填写日期")
            args.append(recorded_on)
            sets.append(f"recorded_on=${len(args)}")
        for key, column in columns.items():
            if key not in data:
                continue
            value = data[key]
            if key == "interaction_at":
                if value is None or not str(value).strip():
                    raise ValueError("请选择拜访日期，归档记录的拜访日期不能清空")
                value = _interaction_datetime(value)
            else:
                value = str(value or "").strip()
                if len(value) > 300:
                    raise ValueError("辅助信息过长")
            args.append(value)
            sets.append(f"{column}=${len(args)}")
        if "collaborator_ids" in data:
            value = data["collaborator_ids"]
            if not isinstance(value, list) or len(value) > 30:
                raise ValueError("协同人格式不正确")
            ids = list(dict.fromkeys(str(uuid.UUID(str(x))) for x in value))
            await validate_collaborators(connection, actor, ids)
            # Editing ordinary collaborators must not erase actual FDE event snapshots.
            await connection.execute(
                "DELETE FROM activity.visit_participant WHERE visit_id=$1::uuid AND participant_role='collaborator' "
                "AND NOT(user_ref_id=ANY($2::uuid[]))",
                visit_id,
                ids,
            )
            for uid in ids:
                await connection.execute(
                    "INSERT INTO activity.visit_participant"
                    "(visit_id,user_ref_id,workspace_id,participant_role,created_by_user_ref_id) "
                    "VALUES($1::uuid,$2::uuid,$3::uuid,'collaborator',$4::uuid) ON CONFLICT DO NOTHING",
                    visit_id,
                    uid,
                    actor.workspace_id,
                    actor.user_id,
                )
        if sets or "collaborator_ids" in data:
            await connection.execute(
                "UPDATE activity.visit SET "  # noqa: S608 -- columns come only from the fixed map above.
                + ",".join([*sets, "version_no=version_no+1", "updated_at=clock_timestamp()"])
                + " WHERE id=$1::uuid",
                *args,
            )
        result = await self.detail(connection, visit_id)
        if sets or "collaborator_ids" in data:
            from sales_backend.repositories.customer_risk import enqueue_customer_risk_review

            await enqueue_customer_risk_review(
                connection, actor, customer_id=result["customer_id"],
                trigger_type="visit.supplemented", trigger_id=visit_id,
            )
        return result
