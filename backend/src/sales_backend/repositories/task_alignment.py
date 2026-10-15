"""Only the database's bounded, permission-checked task alignment API writes links."""

from sales_backend.db import json_value

# The task alias is fixed by the two repository projections. The database
# function remains the authority for object membership, counts and V1 output.
# Names are current display metadata, obtained only through those visible IDs
# and the real target/objective scope under the caller's existing RLS.
TASK_ALIGNMENT_READ_SQL = """(
  WITH alignment_base AS MATERIALIZED (
    SELECT security.task_alignment_read(t.id) AS value
  )
  SELECT CASE WHEN value->>'contract_version'='2' THEN value || jsonb_build_object(
    'targets',COALESCE((
      SELECT jsonb_agg(item || jsonb_build_object('scope',item->'scope' ||
        jsonb_build_object('user_name',scope_user.display_name,'team_name',scope_team.name)) ORDER BY ordinal)
      FROM jsonb_array_elements(value->'targets') WITH ORDINALITY entries(item,ordinal)
      LEFT JOIN crm.sales_target target ON target.id=(item->>'id')::uuid
        AND target.workspace_id=common.current_workspace_id()
        AND security.target_scope_read(target.scope_type,target.user_ref_id,target.team_id,target.department_code)
      LEFT JOIN platform.user_ref scope_user
        ON scope_user.id=target.user_ref_id AND scope_user.workspace_id=target.workspace_id
      LEFT JOIN platform.team scope_team
        ON scope_team.id=target.team_id AND scope_team.workspace_id=target.workspace_id
    ),'[]'::jsonb),
    'key_results',COALESCE((
      SELECT jsonb_agg(item || jsonb_build_object('scope',item->'scope' ||
        jsonb_build_object('user_name',scope_user.display_name,'team_name',scope_team.name)) ORDER BY ordinal)
      FROM jsonb_array_elements(value->'key_results') WITH ORDINALITY entries(item,ordinal)
      LEFT JOIN crm.review_key_result kr ON kr.id=(item->>'id')::uuid
        AND kr.workspace_id=common.current_workspace_id() AND security.review_key_result_readable(kr.id)
      LEFT JOIN crm.review_objective objective
        ON objective.id=kr.objective_id AND objective.workspace_id=kr.workspace_id
      LEFT JOIN platform.user_ref scope_user
        ON scope_user.id=objective.user_ref_id AND scope_user.workspace_id=objective.workspace_id
      LEFT JOIN platform.team scope_team
        ON scope_team.id=objective.team_id AND scope_team.workspace_id=objective.workspace_id
    ),'[]'::jsonb)
  ) ELSE value END FROM alignment_base
)"""


class TaskAlignmentRepository:
    @staticmethod
    async def supports_v2(connection):
        return await connection.fetchval("SELECT EXISTS(SELECT 1 FROM ops.schema_migration WHERE version='V244')")

    @staticmethod
    async def save(connection, task_id, alignment, expected_version=None):
        return json_value(
            await connection.fetchval(
                "SELECT security.save_task_alignment($1::uuid,$2::jsonb,$3::integer)",
                task_id,
                alignment,
                expected_version,
            )
        )

    @staticmethod
    async def report(connection, task_id, contribution, expected_version):
        return json_value(
            await connection.fetchval(
                "SELECT security.record_task_contribution($1::uuid,$2::jsonb,$3::integer)",
                task_id,
                contribution,
                expected_version,
            )
        )
