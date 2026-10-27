"""Release-wide knowledge pause, independent of tenant configuration and ACLs.

Keep the data/configuration intact. Re-enabling this module requires an explicit
code release; configuring a provider or changing a role cannot bypass the pause.
"""

KNOWLEDGE_AVAILABLE = False
KNOWLEDGE_UNAVAILABLE_CODE = "KNOWLEDGE_TEMPORARILY_UNAVAILABLE"
KNOWLEDGE_UNAVAILABLE_MESSAGE = "知识库暂不能使用"


class KnowledgeTemporarilyUnavailable(RuntimeError):
    code = KNOWLEDGE_UNAVAILABLE_CODE

    def __init__(self):
        super().__init__(KNOWLEDGE_UNAVAILABLE_MESSAGE)


def require_knowledge_available():
    if not KNOWLEDGE_AVAILABLE:
        raise KnowledgeTemporarilyUnavailable()


def is_knowledge_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/")
               for prefix in ("/api/v1/knowledge", "/api/v1/public/knowledge"))


async def knowledge_availability_boundary(request, call_next):
    # Before body parsing, authentication/database access or upload buffering.
    if not KNOWLEDGE_AVAILABLE and is_knowledge_path(request.url.path):
        from sales_backend.error_contract import error_response
        return error_response(request, status_code=503, code=KNOWLEDGE_UNAVAILABLE_CODE,
                              known_rejection=True, retryable=False)
    return await call_next(request)
