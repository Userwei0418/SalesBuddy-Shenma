"""Formal WEB005 facts and informational project roles; caller owns transaction."""

from uuid import UUID

from sales_backend.contracts.opportunity_fields import SCALAR_FIELDS
from sales_backend.domain.opportunity_field_catalog import FIELD_CATALOG
from sales_backend.repositories.collaboration import effective_ids, members_by_opportunity, validated_fde
from sales_backend.repositories.authorization_checks import require_permission

PERSISTED_FIELDS = tuple(sorted(SCALAR_FIELDS | {"primary_fde_id", "intent_product_line_ids"}))


def profile_changes(before, data):
    after, changes = {}, []
    for key in PERSISTED_FIELDS:
        old = (before or {}).get(key, [] if key in {"customer_source", "intent_product_line_ids"} else None)
        value = data[key] if key in data else old
        if key == "intent_product_line_ids":
            old = sorted(str(item) for item in old)
            value = sorted(str(item) for item in value)
        if key == "primary_fde_id" and value is not None:
            value = str(value)
        if key == "primary_fde_id" and old is not None:
            old = str(old)
        after[key] = value
        if value != old:
            label = '意向产品线' if key == 'intent_product_line_ids' else FIELD_CATALOG[key]["label"]
            changes.append({"field": key, "label": label, "before": old, "after": value})
    return after, changes


async def project_people(connection, opportunity_ids):
    if not opportunity_ids:
        return {}
    rows = await connection.fetch(
        """SELECT p.opportunity_id::text,p.user_ref_id::text AS user_id,p.participant_role AS role,
        u.display_name AS name,(u.status='active' AND u.deleted_at IS NULL AND
        (p.participant_role<>'fde' OR security.fde_user_is_active(u.id))) AS is_active
        FROM crm.opportunity_project_participant p JOIN platform.user_ref u
          ON u.id=p.user_ref_id AND u.workspace_id=p.workspace_id
        WHERE p.opportunity_id=ANY($1::uuid[]) AND p.workspace_id=common.current_workspace_id()
        ORDER BY u.display_name,p.user_ref_id""", opportunity_ids,
    )
    result = {}
    for row in rows:
        item = dict(row)
        result.setdefault(item.pop("opportunity_id"), []).append(item)
    return result


async def prepare_people(connection, actor, oid, data, before):
    """Check the final aggregate before any write, retaining unchanged inactive IDs."""
    updating = before is not None
    primary = data.get("primary_fde_id", (before or {}).get("primary_fde_id"))
    primary = str(primary) if primary else None
    old_primary = str(before["primary_fde_id"]) if before and before.get("primary_fde_id") else None
    current_fde = await effective_ids(connection, oid) if updating else set()
    desired_fde = None
    if data.get("fde_member_ids") is not None:
        await require_permission(connection, "opportunity.fde_members", opportunity_id=oid if updating else None)
        people = await validated_fde(connection, data["fde_member_ids"], existing=current_fde)
        desired_fde = {person["id"] for person in people}
    if primary != old_primary:
        await require_permission(connection, "opportunity.fde_members", opportunity_id=oid if updating else None)
        if primary:
            await validated_fde(connection, [primary])
    if (primary != old_primary or desired_fde is not None) and primary and primary not in (
        desired_fde if desired_fde is not None else current_fde
    ):
        raise ValueError("主责FDE须包含在同次保存后的长期FDE名单中")
    old_people = (await project_people(connection, [oid])).get(str(oid), []) if updating else []
    current = {person["user_id"]: person["role"] for person in old_people}
    participants = None
    if "participants" in data:
        # Input was typed at the public boundary; validate IDs again here for
        # internal callers, without interpreting display names as identities.
        participants = {str(UUID(str(person["user_id"]))): person["role"] for person in data["participants"]}
        if len(participants) != len(data["participants"]):
            raise ValueError("项目参与者不能重复")
        changed = [uid for uid, role in participants.items() if current.get(uid) != role]
        available = {row["id"]: row for row in await connection.fetch(
            """SELECT u.id::text,security.fde_user_is_active(u.id) AS is_fde
             FROM platform.user_ref u WHERE u.id=ANY($1::uuid[])
             AND u.workspace_id=common.current_workspace_id() AND u.status='active' AND u.deleted_at IS NULL""",
            changed,
        )} if changed else {}
        if any(uid not in available or (participants[uid] == "fde" and not available[uid]["is_fde"]) for uid in changed):
            raise ValueError("项目参与者不存在、已停用或职责不适用")
    changes = []
    if desired_fde is not None and desired_fde != current_fde:
        changes.append({"field": "fde_members", "label": "长期FDE名单",
                        "before": sorted(current_fde), "after": sorted(desired_fde)})
    if participants is not None and participants != current:
        changes.append({"field": "participants", "label": "项目参与人员", "before": current, "after": participants})
    return {"fde_ids": desired_fde, "participants": participants, "old_participants": current,
            "changes": changes, "primary_fde_id": primary}


async def write_project_people(connection, actor, oid, prepared):
    desired = prepared["participants"]
    if desired is None:
        return
    await connection.execute(
        "DELETE FROM crm.opportunity_project_participant WHERE opportunity_id=$1::uuid "
        "AND workspace_id=common.current_workspace_id() AND NOT(user_ref_id=ANY($2::uuid[]))", oid, list(desired),
    )
    for uid, role in desired.items():
        if prepared["old_participants"].get(uid) == role:
            continue
        await connection.execute(
            """INSERT INTO crm.opportunity_project_participant
            (workspace_id,opportunity_id,user_ref_id,participant_role,created_by_user_ref_id)
            VALUES($1::uuid,$2::uuid,$3::uuid,$4,$5::uuid)
            ON CONFLICT(opportunity_id,user_ref_id) DO UPDATE SET participant_role=EXCLUDED.participant_role""",
            actor.workspace_id, oid, uid, role, actor.user_id,
        )


async def profile_projection(connection, rows):
    """Attach current profile relations without exposing historical source_fields."""
    from sales_backend.repositories.opportunity_product_lines import attach_product_lines

    await attach_product_lines(connection, rows)
    ids = [str(row["id"]) for row in rows]
    legacy_ids = [str(row["id"]) for row in rows if not row.get("shared_read_only", False)]
    participants = await project_people(connection, legacy_ids)
    permission_rows = await connection.fetch(
        "SELECT id::text,security.authorization_opportunity('opportunity.update',id) AS can_edit,"
        "security.can_manage_fde_members(id) AS can_fde FROM crm.opportunity "
        "WHERE workspace_id=common.current_workspace_id() AND id=ANY($1::uuid[])", ids,
    ) if ids else []
    permissions = {row["id"]: dict(row) for row in permission_rows}
    primary_ids = sorted({str(row["primary_fde_id"]) for row in rows
                          if row.get("primary_fde_id") and not row.get("shared_read_only", False)})
    primary = {row["id"]: dict(row) for row in await connection.fetch(
        "SELECT u.id::text,u.display_name AS name,security.fde_user_is_active(u.id) AS is_active "
        "FROM platform.user_ref u WHERE u.workspace_id=common.current_workspace_id() AND u.id=ANY($1::uuid[])",
        primary_ids,
    )} if primary_ids else {}
    for row in rows:
        if not row.get("shared_read_only", False):
            row["participants"] = participants.get(str(row["id"]), [])
            row["primary_fde"] = primary.get(str(row.get("primary_fde_id")))
            row["fde_member_ids"] = [person["id"] for person in row.get("fde_members", [])]
        else:
            row.pop("primary_fde_id", None)
        allowed = permissions.get(str(row["id"]), {})
        row["capabilities"] = {"editable_fields": editable_fields(can_edit=bool(allowed.get("can_edit")),
                               can_fde=bool(allowed.get("can_fde"))),
                               "can_manage_fde_members": bool(allowed.get("can_edit") and allowed.get("can_fde"))}
        if row.get("product_line_source") == "materials":
            row["capabilities"]["editable_fields"] = [key for key in row["capabilities"]["editable_fields"]
                                                     if key != "intent_product_line_ids"]
    return rows


def editable_fields(*, can_edit, can_fde):
    permitted = {key for key, field in FIELD_CATALOG.items() if field["edit_mode"] == "inline"} if can_edit else set()
    if can_edit:
        permitted |= {"participants", "stage", "status", "amount", "sales_channel", "partner",
                      "expected_close_date", "forecast_recognized", "forecast_collection", "collection_confidence", "intent_product_line_ids"}
    if can_edit and can_fde:
        permitted |= {"primary_fde_id", "fde_member_ids"}
    return sorted(permitted)


async def read_fields(connection, actor, opportunity_id):
    from sales_backend.repositories.opportunity_business_date import business_created_on, creation_date_projection
    from sales_backend.repositories.opportunity_browse import STAGE_SQL

    await require_permission(connection, "opportunity.read")
    row = await connection.fetchrow(
        """SELECT o.*,owner.display_name AS owner_name,t.name AS team_name,partner.name AS partner_name,
        c.name AS customer_name,COALESCE(c.name,security.customer_reference(o.customer_id)->>'name') AS safe_customer_name,
        c.owner_user_ref_id::text AS customer_owner_id,co.display_name AS customer_owner_name,
        creator.display_name AS creator_name,""" + STAGE_SQL + " AS display_stage,"
        + creation_date_projection() + """ AS creation_fact,
        security.authorization_opportunity('opportunity.update',o.id) AS can_edit,
        security.can_manage_fde_members(o.id) AS can_fde,
        latest.interaction_at AS latest_followup_at,latest.follow_up_record AS latest_progress,
        EXISTS(SELECT 1 FROM activity.visit recent WHERE recent.workspace_id=o.workspace_id
          AND recent.deleted_at IS NULL AND recent.status IN ('confirmed','archived')
          AND (recent.opportunity_id=o.id OR EXISTS(SELECT 1 FROM activity.visit_opportunity vo
            WHERE vo.visit_id=recent.id AND vo.opportunity_id=o.id))
          AND recent.interaction_at >= ((timezone('Asia/Shanghai',clock_timestamp())::date-6)::timestamp
            AT TIME ZONE 'Asia/Shanghai') AND recent.interaction_at <
          ((timezone('Asia/Shanghai',clock_timestamp())::date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')) AS has_followup_7d,
        (SELECT count(*) FROM crm.opportunity_demo_scenes d WHERE d.opportunity_id=o.id AND d.deleted_at IS NULL) AS demo_count
        FROM crm.opportunity o
        LEFT JOIN crm.customer c ON c.id=o.customer_id AND c.deleted_at IS NULL
        LEFT JOIN platform.user_ref owner ON owner.id=o.owner_user_ref_id AND owner.workspace_id=o.workspace_id
        LEFT JOIN platform.team t ON t.id=o.owner_team_id AND t.workspace_id=o.workspace_id
        LEFT JOIN crm.partner partner ON partner.id=o.partner_id AND partner.workspace_id=o.workspace_id
        LEFT JOIN platform.user_ref co ON co.id=c.owner_user_ref_id AND co.workspace_id=o.workspace_id
        LEFT JOIN platform.user_ref creator ON creator.id=o.created_by_user_ref_id AND creator.workspace_id=o.workspace_id
        LEFT JOIN LATERAL(SELECT v.interaction_at,v.follow_up_record FROM activity.visit v
          WHERE v.workspace_id=o.workspace_id AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')
          AND (v.opportunity_id=o.id OR EXISTS(SELECT 1 FROM activity.visit_opportunity vo
            WHERE vo.visit_id=v.id AND vo.opportunity_id=o.id))
          ORDER BY v.interaction_at DESC NULLS LAST,v.id DESC LIMIT 1) latest ON true
        WHERE o.id=$1::uuid AND o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL
          AND security.authorization_opportunity('opportunity.read',o.id)""", opportunity_id,
    )
    if not row:
        raise LookupError("商机不存在或不在当前权限范围")
    from sales_backend.db import json_value

    item = dict(row)
    item["fde_members"] = (await members_by_opportunity(connection, [str(opportunity_id)])).get(str(opportunity_id), [])
    await profile_projection(connection, [item])

    def person(uid, name):
        return {"id": str(uid), "name": name or "成员"} if uid else None

    values = {key: item.get(key) for key in PERSISTED_FIELDS}
    values.update(
        id=str(item["id"]), name=item["name"], customer_name=item["safe_customer_name"],
        stage=item["display_stage"], status=item["status"], amount=str(item["amount"]) if item["amount"] is not None else None,
        owner=person(item.get("owner_user_ref_id"), item.get("owner_name")),
        team=person(item.get("owner_team_id"), item.get("team_name")),
        partner=person(item.get("partner_id"), item.get("partner_name")),
        customer_owner=person(item.get("customer_owner_id"), item.get("customer_owner_name")),
        creator=person(item.get("created_by_user_ref_id"), item.get("creator_name")),
        primary_fde_id=item["primary_fde"], fde_member_ids=item["fde_members"], participants=item["participants"],
        business_created_on=business_created_on(json_value(item["creation_fact"])), region=None,
        **{key: item.get(key) for key in ("product_line", "product_lines", "product_line_source", "legacy_product_line",
            "intent_product_line_ids", "intent_product_lines", "sales_channel", "follow_up_plan", "expected_close_date",
            "expected_close_year", "expected_close_quarter", "latest_followup_at", "latest_progress", "has_followup_7d", "demo_count")},
    )
    return {"id": str(item["id"]), "customer_id": str(item["customer_id"]), "version_no": item["version_no"],
            "values": values, "capabilities": item["capabilities"]}


async def member_directory(connection, *, query=None, kind="all", limit=50, offset=0):
    await require_permission(connection, "opportunity.read")
    rows = await connection.fetch(
        """SELECT u.id::text,u.display_name AS name,u.account_code,true AS is_active,
        COALESCE((SELECT jsonb_agg(DISTINCT b.role_code) FROM platform.role_binding b
          WHERE b.workspace_id=u.workspace_id AND b.user_ref_id=u.id
          AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to),'[]'::jsonb) AS roles,
        COALESCE((SELECT jsonb_agg(DISTINCT jsonb_build_object('id',t.id,'name',t.name))
          FROM platform.team_membership m JOIN platform.team t ON t.id=m.team_id AND t.workspace_id=m.workspace_id
          WHERE m.workspace_id=u.workspace_id AND m.user_ref_id=u.id AND t.status='active' AND t.deleted_at IS NULL
          AND clock_timestamp()>=m.valid_from AND clock_timestamp()<m.valid_to
          AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to),'[]'::jsonb) AS teams
        FROM platform.user_ref u WHERE u.workspace_id=common.current_workspace_id()
        AND u.status='active' AND u.deleted_at IS NULL
        AND ($1::text IS NULL OR strpos(lower(concat_ws(' ',u.display_name,u.account_code)),lower($1))>0)
        AND ($2::text='all' OR security.fde_user_is_active(u.id))
        ORDER BY u.display_name COLLATE "C",u.id""", query, kind,
    )
    from sales_backend.db import json_value

    total = len(rows)
    return {"items": [{**dict(row), "roles": json_value(row["roles"]), "teams": json_value(row["teams"])}
                      for row in rows[offset:offset + limit]],
            "total": total, "has_more": offset + limit < total,
            "next_offset": offset + limit if offset + limit < total else None}
