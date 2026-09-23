"""Drain Agent response persistence independently of the HTTP subscriber."""
from collections import deque
from threading import Condition, Thread


def durable_response(source, *, context, on_error, max_buffer=256):
    """Start immediately, even if the subscriber never reads the first event.

    Only the producer owns source. Disconnect/slow consumers cannot close it.
    The buffer is bounded; overflow detaches HTTP but still drains persistence.
    """
    buffer = deque()
    condition = Condition()
    state = dict(done=False, detached=False, error=None)

    def produce():
        with context():
            try:
                for item in source:
                    with condition:
                        if not state['detached']:
                            if len(buffer) >= max_buffer:
                                buffer.clear()
                                state['detached'] = True
                                state['error'] = RuntimeError('Response consumer too slow; check conversation history')
                            else:
                                buffer.append(item)
                            condition.notify_all()
            except Exception as exc:
                with condition:
                    state['error'] = exc
                on_error(exc)
            finally:
                with condition:
                    state['done'] = True
                    condition.notify_all()

    Thread(target=produce, name='agent-response-persistence', daemon=True).start()

    def receive():
        try:
            while True:
                with condition:
                    condition.wait_for(lambda: buffer or state['done'] or state['error'])
                    if buffer:
                        item = buffer.popleft()
                    elif state['error']:
                        raise state['error']
                    else:
                        return
                yield item
        finally:
            with condition:
                state['detached'] = True
                buffer.clear()

    return receive()
