"""Cache policy for console assets served under stable module URLs."""

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


class RevalidatingStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        # HTML revalidation does not revalidate its ES module dependencies.
        # Keep conditional requests while preventing reuse without validation.
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response
