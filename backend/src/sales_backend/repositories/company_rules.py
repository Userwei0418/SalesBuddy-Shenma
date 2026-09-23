from sales_backend.domain.company_rules import RULE_FIELDS, RULE_LABELS, policy_snapshot


class CompanyRulesRepository:
    async def version(self, connection, rule_id):
        return await connection.fetchrow("SELECT * FROM config.rule_set WHERE id=$1", rule_id)

    async def active(self, connection, code):
        row = await connection.fetchrow("SELECT * FROM config.rule_set WHERE id=security.active_company_rule($1)", code)
        return policy_snapshot(code, row)

    async def catalog(self, connection, *, technical=False, business=False):
        result = []
        for code, label in RULE_LABELS.items():
            if code.startswith("agent_business.") != business:
                continue
            if code.startswith("agent_execution.") != technical:
                continue
            current = await self.active(connection, code)
            rows = await connection.fetch(
                """SELECT r.id::text,r.workspace_id::text,r.version_no,r.status,r.definition,r.revision_no,
                   r.base_rule_id::text,
                   r.restored_from_id::text,r.change_reason,r.created_at,r.published_at,
                   u.display_name AS updated_by,p.display_name AS published_by
                   FROM config.rule_set r LEFT JOIN platform.user_ref u ON u.id=r.updated_by_user_ref_id
                   LEFT JOIN platform.user_ref p ON p.id=r.published_by_user_ref_id
                   WHERE r.rule_code=$1 AND (r.workspace_id=common.current_workspace_id() OR r.workspace_id IS NULL)
                   ORDER BY (r.workspace_id IS NOT NULL) DESC,r.version_no DESC LIMIT 100""",
                code,
            )
            result.append(
                {
                    "code": code,
                    "label": label,
                    "current": current,
                    "fields": RULE_FIELDS[code],
                    "versions": [{**dict(row), "definition": policy_snapshot(code, row)["definition"]} for row in rows],
                }
            )
        return result

    async def save(self, connection, code, data, restored=None):
        return str(
            await connection.fetchval(
                "SELECT security.save_company_rule($1,$2,$3::jsonb,$4,$5::uuid,$6,$7::uuid,$8::uuid)",
                code,
                RULE_LABELS[code],
                data["definition"],
                data["reason"],
                data.get("id"),
                data.get("revision"),
                data.get("base_id"),
                restored,
            )
        )

    async def publish(self, connection, key, revision):
        return str(await connection.fetchval("SELECT security.publish_company_rule($1::uuid,$2)", key, revision))
