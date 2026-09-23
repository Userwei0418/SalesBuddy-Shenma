"""Company reads, versioned updates and audit persistence under the caller's RLS."""

from sales_backend.db import json_value


class CompanyRepository:
    async def directory(self, connection):
        return json_value(await connection.fetchval("SELECT security.company_directory()")) or []

    async def record_selection(self, connection, actor, selected):
        await connection.execute(
            """INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,module_code,object_type,object_label,after_snapshot,result_code,request_id)
          VALUES($1::uuid,$2::uuid,$3,'company.select','organization','workspace',$4,$5::jsonb,'success',NULLIF(current_setting('app.request_id',true),'')::uuid)""",
            actor.workspace_id, actor.user_id, actor.role.value, selected["name"],
            {"target_company_id": selected["id"]},
        )

    async def lock(self, connection, company_id):
        return await connection.fetchrow(
            "SELECT name,version_no FROM platform.workspace WHERE id=$1::uuid FOR UPDATE", company_id
        )

    async def rename(self, connection, company_id, name, version_no):
        return await connection.fetchrow(
            """UPDATE platform.workspace SET name=$2,version_no=version_no+1
          WHERE id=$1::uuid AND version_no=$3 AND status='active' AND deleted_at IS NULL
          RETURNING id::text,name,version_no""",
            company_id, name, version_no,
        )

    async def record_rename(self, connection, actor, before, after):
        await connection.execute(
            """INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,module_code,object_type,object_id,object_label,before_snapshot,after_snapshot,result_code,request_id)
          VALUES($1::uuid,$2::uuid,$3,'company.rename','organization','workspace',$1::uuid,$4,$5::jsonb,$6::jsonb,'success',NULLIF(current_setting('app.request_id',true),'')::uuid)""",
            actor.workspace_id, actor.user_id, actor.role.value, after["name"], dict(before), dict(after),
        )
