"""Fixed-origin Feishu OpenAPI transport; never logs credentials or business bodies."""
import asyncio
import json
from time import monotonic
from uuid import UUID

import httpx

ORIGIN = "https://open.feishu.cn/open-apis"


class FeishuError(RuntimeError):
    def __init__(self, code: str, *, retryable=False, unknown=False, retry_after=0):
        super().__init__(code)
        self.code, self.retryable, self.unknown, self.retry_after = code, retryable, unknown, retry_after


class FeishuClient:
    def __init__(self, app_id: str, secret: str, *, client=None):
        self.app_id = app_id
        self._secret = secret
        self._client = client or httpx.AsyncClient(timeout=20, follow_redirects=False)
        self._owns_client = client is None
        self._token = ""
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def close(self):
        self._token = ""
        self._secret = ""
        if self._owns_client:
            await self._client.aclose()

    async def _send(self, method, path, *, token=None, body=None, params=None, write=False):
        try:
            response = await self._client.request(method, ORIGIN + path,
                headers={"Authorization": f"Bearer {token}"} if token else {}, json=body, params=params)
        except (httpx.TimeoutException, httpx.NetworkError):
            raise FeishuError("TRANSPORT_UNKNOWN" if write else "TRANSPORT_UNAVAILABLE",
                              retryable=not write, unknown=write) from None
        delay = max((min(3600, int(value)) if value.isdigit() else 0)
                    for value in (response.headers.get("Retry-After", "0").strip(),
                                  response.headers.get("x-ogw-ratelimit-reset", "0").strip()))
        if response.status_code == 429:
            raise FeishuError("RATE_LIMITED", retryable=True, retry_after=delay)
        if response.status_code >= 500:
            raise FeishuError("UPSTREAM_UNAVAILABLE", retryable=not write, unknown=write)
        if response.status_code >= 300:
            # Explicit provider rejections can use HTTP 400 as well as 200.
            # Preserve only a positive integer code for the existing policy;
            # an arbitrary HTTP error never becomes a retryable write.
            try:
                code = response.json().get("code")
            except (ValueError, AttributeError):
                code = None
            if type(code) is not int or code <= 0:
                raise FeishuError(f"HTTP_{response.status_code}")
        try:
            data = response.json()
            if not isinstance(data, dict) or "code" not in data:
                raise ValueError()
        except ValueError:
            raise FeishuError("INVALID_RESPONSE", unknown=write) from None
        if data["code"] != 0:
            if (path.startswith("/bitable/") and type(data["code"]) is int
                    and data["code"] == 1255001):
                # InternalError does not establish whether a write committed.
                # The queue must recheck the stable system ID before recovery;
                # do not replay the request or generate a new create token here.
                raise FeishuError("FEISHU_1255001", retryable=not write, unknown=write,
                                  retry_after=max(10, delay))
            if data["code"] in {99991400, 1254290}:
                raise FeishuError("RATE_LIMITED", retryable=True, retry_after=delay)
            if data["code"] == 1254291:
                raise FeishuError("WRITE_CONFLICT", retryable=True, retry_after=max(1, delay))
            if data["code"] == 1254607:
                # Explicit rejection while the table's prior work is unfinished.
                # Queue retry rechecks stable ID; never replay this write inline.
                raise FeishuError("DATA_NOT_READY", retryable=True, retry_after=max(10, delay))
            raise FeishuError(f"FEISHU_{data['code']}")
        return data

    async def token(self):
        async with self._lock:
            if monotonic() >= self._expires_at:
                result = await self._send("POST", "/auth/v3/tenant_access_token/internal",
                                          body={"app_id": self.app_id, "app_secret": self._secret})
                token = result.get("tenant_access_token")
                if not isinstance(token, str) or not token:
                    raise FeishuError("INVALID_TOKEN_RESPONSE")
                self._token = token
                self._expires_at = monotonic() + max(0, int(result.get("expire", 0)) - 120)
            return self._token

    async def request(self, method, path, *, body=None, params=None, write=False):
        token = await self.token()
        for attempt in range(2):
            try:
                result = await self._send(method, path, token=token, body=body, params=params, write=write)
                return result.get("data", {})
            except FeishuError as exc:
                if path.startswith("/bitable/") and exc.code in {
                        "FEISHU_1254009", "FEISHU_1254044", "FEISHU_1254045"}:
                    # Explicit rejection, including after token refresh: let
                    # the original event re-read source/schema/remote ID. Do
                    # not replay this write inside the HTTP request.
                    raise FeishuError(exc.code, retryable=True) from None
                # Only a first explicit auth rejection permits one replay.
                # Unknown writes and all other failures retain their policy.
                if exc.code != "FEISHU_99991663" or attempt:
                    raise
                async with self._lock:
                    if self._token == token:
                        self._expires_at = 0.0
                token = await self.token()

    @staticmethod
    def table_path(base, table):
        # All caller-supplied path coordinates come from validated SyncConfig.
        return f"/bitable/v1/apps/{base}/tables/{table}"

    async def fields(self, base, table):
        # Mapping binds field IDs but the write API accepts names. Always read
        # fresh: an old name can be reused by another field without any error.
        result, page, seen = {}, None, set()
        while True:
            params = {"page_size": 100}
            if page:
                params["page_token"] = page
            data = await self.request("GET", self.table_path(base, table) + "/fields", params=params)
            for item in data.get("items", []):
                result[item["field_id"]] = item
            if not data.get("has_more"):
                return result
            page = data.get("page_token")
            if not page or page in seen:
                raise FeishuError("INVALID_PAGINATION")
            seen.add(page)

    async def find_record(self, base, table, id_field_name, system_id):
        data = await self.request("POST", self.table_path(base, table) + "/records/search",
            params={"page_size": 2}, body={"field_names": [id_field_name], "filter": {
                "conjunction": "and", "conditions": [{"field_name": id_field_name,
                "operator": "is", "value": [system_id]}]}})
        items = data.get("items", [])
        if len(items) > 1 or data.get("has_more"):
            raise FeishuError("DUPLICATE_SYSTEM_ID")
        return items[0]["record_id"] if items else None

    async def create_record(self, base, table, fields, client_token: UUID):
        data = await self.request("POST", self.table_path(base, table) + "/records",
            body={"fields": fields}, params={"client_token": str(client_token)}, write=True)
        return data["record"]["record_id"]

    async def update_record(self, base, table, record_id, fields):
        await self.request("PUT", self.table_path(base, table) + f"/records/{record_id}",
                           body={"fields": fields}, write=True)

    async def send_card(self, chat_id, card, message_uuid):
        data = await self.request("POST", "/im/v1/messages", params={"receive_id_type": "chat_id"},
            body={"receive_id": chat_id, "msg_type": "interactive",
                  "content": json.dumps(card, ensure_ascii=False), "uuid": message_uuid}, write=True)
        return data["message_id"]
