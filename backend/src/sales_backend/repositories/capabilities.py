class CapabilityRepository:
    async def fde_state(self, connection):
        return await connection.fetchrow(
            "SELECT security.fde_visit_entry_enabled() AS visit_entry_enabled, "
            "security.fde_permission_version() AS permission_version"
        )

    async def analysis_identity(self, connection, actor):
        from sales_backend.domain.capabilities import FDE_ROLES

        identity = actor.model_dump(mode="json")
        if actor.role.value in FDE_ROLES:
            state = await self.fde_state(connection)
            identity["permission_version"] = state["permission_version"]
        return identity

    async def has_customer_access(self, connection, customer_id):
        return bool(await connection.fetchval("SELECT security.has_customer_access($1::uuid)", customer_id))
