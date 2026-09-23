"""Retry only an unexposed model invocation; never replay Agent tools or runs."""
import logging
from gevent import Timeout
from graphon.model_runtime.errors.invoke import InvokeConnectionError, InvokeRateLimitError, InvokeServerUnavailableError


class FirstChunkTimeout(TimeoutError):
    pass


def invoke_with_fallback(primary, secondary, *, timeout, context):
    stream = None
    try:
        # gevent timeout covers connection and first item. It is removed before
        # yielding; cancellation/partial output never starts another model.
        with Timeout(timeout, FirstChunkTimeout("primary_first_chunk_timeout")):
            stream = iter(primary())
            first = next(stream)
    except (FirstChunkTimeout, InvokeConnectionError, InvokeRateLimitError, InvokeServerUnavailableError) as exc:
        if stream is not None:
            getattr(stream, "close", lambda: None)()
        logging.getLogger(__name__).warning(
            "agent_model_fallback agent=%s run=%s primary=%s fallback=%s reason=%s",
            context["agent_id"], context["run_id"], context["primary"], context["fallback"], type(exc).__name__,
        )
        yield from secondary()
        return
    except BaseException:
        if stream is not None:
            getattr(stream, "close", lambda: None)()
        raise
    try:
        yield first
        yield from stream
    finally:
        if stream is not None:
            getattr(stream, "close", lambda: None)()
