"""Exact public company entry lookup, with no account discovery or tenant listing."""

import hashlib

from sales_backend.db import json_value
from sales_backend.repositories.passwords import PasswordRepository
from sales_backend.services.password_auth import LoginThrottled


class CompanyEntryService:
    def __init__(self, database):
        self.database = database

    async def lookup(self, company_code: str, network: str) -> dict | None:
        code = company_code.strip().lower()
        # Separate from password counters: visiting a company entry cannot lock an account.
        keys = ((f"company-entry-network:{network}", 50),)
        repository = PasswordRepository()
        async with self.database.connection() as connection:
            blocked = []
            for value, limit in keys:
                key = hashlib.sha256(value.encode()).hexdigest()
                if not await repository.consume_attempt(connection, key, limit):
                    state = await repository.limit_status(connection, key, limit)
                    blocked.append(state["retry_after_seconds"])
            if blocked:
                raise LoginThrottled("network", max(blocked))
            # Invalid and unknown codes deliberately have the same public outcome.
            if not 1 <= len(code) <= 128 or any(ord(char) < 32 for char in code):
                return None
            return json_value(await connection.fetchval("SELECT security.company_entry($1)", code)) or None
