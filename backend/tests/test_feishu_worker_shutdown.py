"""Exercise the real entry point/run_once with OS signals, without a DB or Feishu."""

import asyncio
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest


def _run_probe(mode):
    from sales_backend import feishu_worker as module

    def emit(event):
        print(event, flush=True)

    async def checkpoint(name):
        emit(name)
        await asyncio.Future()

    async def cleanup_checkpoint(name):
        emit(name)
        # The parent sends another real signal before allowing cleanup to finish.
        await asyncio.to_thread(sys.stdin.readline)

    @dataclass
    class Settings:
        database_url: str = ""
        database_min_pool_size: int = 1
        database_max_pool_size: int = 3

    class Connection:
        async def fetchval(self, query):
            if mode == "verify":
                await checkpoint("verify")
            if mode in {"startup_error", "startup_error_cleanup"}:
                raise RuntimeError("startup failed")
            return "pg_roles" in query

        async def add_listener(self, channel, changed):
            emit("listener_added")

        def add_termination_listener(self, changed):
            pass

        def is_closed(self):
            return False

        async def remove_listener(self, channel, changed):
            if mode == "listener_cleanup":
                await cleanup_checkpoint("listener_cleanup")
            if mode == "unwind_error":
                raise RuntimeError("listener cleanup failed")
            if mode == "unwind_cancel":
                raise asyncio.CancelledError("independent cancellation during unwind")
            if mode == "unwind_external_cancel":
                asyncio.current_task().cancel("second independent cancellation")
                await asyncio.sleep(0)
            emit("listener_removed")

        def remove_termination_listener(self, changed):
            emit("termination_listener_removed")

    class Database:
        def __init__(self, settings):
            self.settings = settings

        async def connect(self):
            if mode == "connect":
                await checkpoint("connect")
            if mode == "connect_error":
                raise RuntimeError("connect failed")
            emit("connected")

        @asynccontextmanager
        async def connection(self):
            try:
                yield Connection()
            finally:
                emit("connection_released")

        async def close(self):
            if mode == "database_cleanup":
                await cleanup_checkpoint("database_cleanup")
            emit("database_closed")
            if mode == "database_close_error":
                raise RuntimeError("database close failed")

    class Repository:
        async def worker_config(self, connection, *args, validation=False):
            return None if validation else {"configured": True}

        async def claim(self, connection, **kwargs):
            if mode in {"active", "unwind_error"}:
                return dict(connection_id="connection", object_kind="customer", object_id="customer")
            if mode in {"unexpected_cancel", "cancel_cleanup"}:
                raise asyncio.CancelledError("unrelated operation")
            if mode == "external_cancel":
                asyncio.current_task().cancel("unrelated task cancellation")
                await asyncio.sleep(0)
            if mode == "backoff":
                asyncio.get_running_loop().call_soon(emit, "backoff")
                raise RuntimeError("queue unavailable")
            asyncio.get_running_loop().call_soon(emit, "idle")
            return None

        async def try_lock(self, connection, key):
            emit("locked")
            return True

        async def finish(self, *args, **kwargs):
            emit("event_finished")

        async def unlock(self, connection, key):
            emit("unlocked")

    class Service:
        async def handle(self, *args):
            try:
                await checkpoint("active")
            finally:
                emit("handler_stopped")

        async def close(self):
            if mode in {"worker_cleanup", "startup_error_cleanup", "cancel_cleanup"}:
                await cleanup_checkpoint("worker_cleanup")
            emit("worker_closed")
            if mode == "worker_close_error":
                raise RuntimeError("worker close failed")
            if mode == "cleanup_cancel":
                raise asyncio.CancelledError("unrelated cleanup cancellation")

    class Worker(module.FeishuWorker):
        def __init__(self, database):
            if mode == "worker_init_error":
                raise RuntimeError("worker init failed")
            self.database = database
            self.repository = Repository()
            self.service = Service()
            self.priority_claims = 0

        async def heartbeat(self, event):
            emit("heartbeat_started")
            try:
                await super().heartbeat(event)
            finally:
                emit("heartbeat_stopped")

    module.get_settings = Settings
    module.Database = Database
    module.FeishuWorker = Worker
    module.run()


class WorkerProcess:
    def __init__(self, mode):
        env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "TMPDIR") if key in os.environ}
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        env["FEISHU_SYNC_DATABASE_URL"] = "unused-process-test"
        self.process = subprocess.Popen(
            [sys.executable, __file__, "--worker-probe", mode],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env,
        )
        self.events = []
        self.pending = queue.Queue()

        def collect():
            for line in self.process.stdout:
                self.pending.put(line.strip())
            self.pending.put(None)

        self.reader = threading.Thread(target=collect, daemon=True)
        self.reader.start()

    def wait_for(self, expected):
        deadline = time.monotonic() + 8
        while expected not in self.events:
            event = self.pending.get(timeout=max(0.01, deadline - time.monotonic()))
            assert event is not None, f"worker exited before {expected}: {self.events}"
            self.events.append(event)

    def signal(self, signum):
        self.process.send_signal(signum)

    def finish(self):
        code = self.process.wait(timeout=8)
        self.reader.join(timeout=1)
        while not self.pending.empty():
            event = self.pending.get_nowait()
            if event is not None:
                self.events.append(event)
        return code, self.process.stderr.read()

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=8)
        self.reader.join(timeout=1)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()


@pytest.fixture
def worker_process():
    processes = []

    def start(mode):
        process = WorkerProcess(mode)
        processes.append(process)
        return process

    yield start
    for process in processes:
        process.close()


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
@pytest.mark.parametrize("phase", ["connect", "verify", "idle", "active", "backoff"])
def test_signal_stops_real_process_and_releases_resources(worker_process, phase, signum):
    child = worker_process(phase)
    child.wait_for(phase)
    child.signal(signum)
    code, error = child.finish()
    assert code == 0, error
    assert "Traceback" not in error and "CancelledError" not in error
    assert child.events.count("database_closed") == 1
    if phase != "connect":
        assert child.events.count("worker_closed") == 1
        assert child.events.index("worker_closed") < child.events.index("database_closed")
    if phase == "active":
        assert child.events.index("handler_stopped") < child.events.index("unlocked")
        assert child.events.index("heartbeat_stopped") < child.events.index("unlocked")
        assert child.events.index("unlocked") < child.events.index("worker_closed")
        assert "event_finished" not in child.events  # Never acknowledge interrupted remote work.


@pytest.mark.parametrize("phase", ["listener_cleanup", "worker_cleanup", "database_cleanup"])
def test_repeated_signals_do_not_interrupt_cleanup(worker_process, phase):
    child = worker_process(phase)
    child.wait_for("idle")
    child.signal(signal.SIGTERM)
    child.wait_for(phase)
    child.signal(signal.SIGINT)
    time.sleep(0.05)
    child.signal(signal.SIGTERM)
    time.sleep(0.05)
    child.process.stdin.write("finish cleanup\n")
    child.process.stdin.flush()
    code, error = child.finish()
    assert code == 0, error
    assert child.events.count("worker_closed") == child.events.count("database_closed") == 1


@pytest.mark.parametrize("mode", [
    "connect_error", "startup_error", "worker_init_error", "unexpected_cancel", "external_cancel",
])
def test_unsignalled_errors_and_cancellation_remain_failures(worker_process, mode):
    child = worker_process(mode)
    code, error = child.finish()
    assert code != 0 and "Traceback" in error
    assert child.events.count("database_closed") == 1
    if mode not in {"connect_error", "worker_init_error"}:
        assert child.events.count("worker_closed") == 1
    if "cancel" in mode:
        assert "CancelledError: unrelated" in error


@pytest.mark.parametrize("mode", [
    "worker_close_error", "database_close_error", "cleanup_cancel", "unwind_error",
    "unwind_cancel", "unwind_external_cancel",
])
def test_signal_does_not_hide_cleanup_failures(worker_process, mode):
    child = worker_process(mode)
    child.wait_for("active" if mode == "unwind_error" else "idle")
    child.signal(signal.SIGTERM)
    code, error = child.finish()
    assert code != 0 and "Traceback" in error
    assert child.events.count("worker_closed") == child.events.count("database_closed") == 1


@pytest.mark.parametrize("mode", ["startup_error_cleanup", "cancel_cleanup"])
def test_signal_during_failure_cleanup_does_not_convert_failure_to_success(worker_process, mode):
    child = worker_process(mode)
    child.wait_for("worker_cleanup")
    child.signal(signal.SIGTERM)
    time.sleep(0.05)
    child.process.stdin.write("finish cleanup\n")
    child.process.stdin.flush()
    code, error = child.finish()
    assert code != 0 and "Traceback" in error
    assert "startup failed" in error if mode == "startup_error_cleanup" else "CancelledError: unrelated" in error
    assert child.events.count("worker_closed") == child.events.count("database_closed") == 1


if __name__ == "__main__":
    assert sys.argv[1] == "--worker-probe"
    _run_probe(sys.argv[2])
