"""Synthetic export data checks for bounded ZIP writes and owned-thread cleanup."""
import asyncio
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import zipfile

import pytest

from sales_backend.services import tenant_export as export


ACTOR = SimpleNamespace(
    workspace_id='00000000-0000-0000-0000-000000000901',
    user_id='00000000-0000-0000-0000-000000000902',
)
EXPORT_ID = '00000000-0000-0000-0000-000000000903'


class Source:
    snapshot_at = '2026-01-01T00:00:00+00:00'

    def __init__(self, tables=None, originals=()):
        self.tables = tables or {}
        self.originals = originals
        self.yielded = 0
        self.exited = False

    @asynccontextmanager
    async def snapshot(self):
        try:
            yield self
        finally:
            self.exited = True

    async def rows(self, table):
        for row in self.tables.get(table.name, ()):
            self.yielded += 1
            yield row

    async def imports(self):
        for row, _ in self.originals:
            yield row

    async def read_original(self, row):
        return next(content for original, content in self.originals if original['id'] == row['id'])


@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    created = []
    real_mkdtemp = export.tempfile.mkdtemp

    def mkdtemp(*, prefix):
        directory = Path(real_mkdtemp(prefix=prefix, dir=tmp_path))
        created.append(directory)
        return str(directory)

    monkeypatch.setattr(export.tempfile, 'mkdtemp', mkdtemp)
    return created


async def build(source, sections=('customers',), originals=False, limits=None):
    return await export.build_package(
        None, ACTOR, sections, originals, source=source,
        limits=limits or export.Limits(), export_id=EXPORT_ID,
    )


def row(number, size=0):
    return {
        'id':str(number), 'workspace_id':ACTOR.workspace_id,
        'name':'合成客户🙂' + 'x' * size,
        'amount':Decimal('123456789.01'), 'day':date(2026, 1, 1),
    }


def capture_member_writes(monkeypatch):
    writes = []
    original = export.file_io

    async def observe(operation, *args):
        owner = getattr(operation, '__self__', None)
        if getattr(operation, '__name__', '') == 'write' and hasattr(owner, '_zinfo'):
            writes.append((owner._zinfo.filename, bytes(args[0])))
        return await original(operation, *args)

    monkeypatch.setattr(export, 'file_io', observe)
    return writes


def assert_removed(artifacts, source):
    assert artifacts and all(not directory.exists() for directory in artifacts)
    assert source.exited


@pytest.mark.asyncio
async def test_content_order_tail_hashes_counts_and_empty_members(artifacts, monkeypatch):
    values = [row(i, 21000) for i in range(8)]
    source = Source({'crm.customer':values})
    writes = capture_member_writes(monkeypatch)
    package = await build(source)
    expected = b''.join(export.json_bytes(value) for value in values)
    try:
        with zipfile.ZipFile(package.path) as archive:
            assert archive.testzip() is None
            assert archive.read('crm/customer.ndjson') == expected
            assert [item['id'] for item in map(json.loads, expected.splitlines())] == list(map(str, range(8)))
            for item in package.manifest['files']:
                content = archive.read(item['path'])
                assert item['sha256'] == hashlib.sha256(content).hexdigest()
                assert item['bytes'] == len(content)
                assert item['rows'] == len(content.splitlines())
                if item['path'] != 'crm/customer.ndjson':
                    assert content == b''
            assert archive.read('manifest.json') == export.json_bytes(package.manifest)
        assert package.manifest['record_count'] == len(values)
        assert package.manifest['content_bytes'] == len(expected)
        assert package.sha256 == hashlib.sha256(package.path.read_bytes()).hexdigest()
        chunks = [data for name, data in writes if name == 'crm/customer.ndjson']
        assert b''.join(chunks) == expected and 1 < len(chunks) < len(values)
        assert all(len(data) <= 65536 for data in chunks)
        assert all(name == 'crm/customer.ndjson' for name, _ in writes)
    finally:
        package.cleanup()
    assert_removed(artifacts, source)


@pytest.mark.asyncio
async def test_all_empty_tables_and_archive_overhead_limit(artifacts, monkeypatch):
    source = Source()
    writes = capture_member_writes(monkeypatch)
    package = await build(source)
    size = package.path.stat().st_size
    try:
        assert package.manifest['record_count'] == 0
        assert package.manifest['content_bytes'] == 0
        assert all(item['rows'] == item['bytes'] == 0 for item in package.manifest['files'])
        with zipfile.ZipFile(package.path) as archive:
            assert archive.testzip() is None
            assert all(archive.read(item['path']) == b'' for item in package.manifest['files'])
        assert writes == []
    finally:
        package.cleanup()
    rejected = Source()
    with pytest.raises(export.ExportRejected) as error:
        await build(rejected, limits=export.Limits(max_bytes=size - 1))
    assert error.value.status == 413
    assert_removed(artifacts, source)
    assert rejected.exited


@pytest.mark.asyncio
async def test_tiny_rows_flush_before_the_cursor_consumes_more_than_fifty(artifacts, monkeypatch):
    source = Source({'crm.customer':[row(i) for i in range(101)]})
    positions = []
    real_write = zipfile._ZipWriteFile.write

    def observe(member, content):
        if member._zinfo.filename == 'crm/customer.ndjson':
            positions.append(source.yielded)
        return real_write(member, content)

    monkeypatch.setattr(zipfile._ZipWriteFile, 'write', observe)
    package = await build(source)
    package.cleanup()
    assert positions == [50, 100, 101]
    assert_removed(artifacts, source)


@pytest.mark.asyncio
async def test_oversized_row_is_written_alone_between_buffered_rows(artifacts, monkeypatch):
    values = [row(1, 100), row(2, 200000), row(3, 100)]
    source = Source({'crm.customer':values})
    writes = capture_member_writes(monkeypatch)
    package = await build(source)
    try:
        expected = [export.json_bytes(value) for value in values]
        chunks = [data for name, data in writes if name == 'crm/customer.ndjson']
        assert chunks == expected
        assert len(chunks[0]) < 65536 < len(chunks[1]) and len(chunks[2]) < 65536
        with zipfile.ZipFile(package.path) as archive:
            assert archive.read('crm/customer.ndjson') == b''.join(expected)
    finally:
        package.cleanup()
    assert_removed(artifacts, source)


@pytest.mark.asyncio
async def test_original_payload_and_relationship_manifest_are_preserved(artifacts):
    import_id = '00000000-0000-0000-0000-000000000904'
    value = {'id':'visit-1', 'workspace_id':ACTOR.workspace_id, 'source_import_id':import_id}
    content = b'SYNTHETIC ORIGINAL\x00\xff'
    original = {
        'id':import_id, 'workspace_id':ACTOR.workspace_id, 'filename':'synthetic.bin',
        'file_size':len(content), 'content_sha256':hashlib.sha256(content).hexdigest(),
        'visit_ids':['visit-1'], 'storage_driver':'local', 'storage_key':None,
    }
    source = Source({'activity.visit':[value]}, [(original, content)])
    package = await build(source, sections=('visits',), originals=True)
    try:
        entry = package.manifest['originals'][0]
        with zipfile.ZipFile(package.path) as archive:
            assert archive.read(entry['path']) == content
        assert entry['sha256'] == original['content_sha256']
        assert entry['visit_ids'] == ['visit-1'] and entry['included'] is True
        assert package.manifest['record_count'] == 2
    finally:
        package.cleanup()
    assert_removed(artifacts, source)


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [1, 50])
async def test_write_failure_removes_tail_or_full_batch_artifact(artifacts, monkeypatch, count):
    source = Source({'crm.customer':[row(i) for i in range(count)]})
    real_write = zipfile._ZipWriteFile.write

    def fail(member, content):
        if member._zinfo.filename == 'crm/customer.ndjson':
            real_write(member, content[:len(content) // 2])
            raise OSError('synthetic write failure')
        return real_write(member, content)

    monkeypatch.setattr(zipfile._ZipWriteFile, 'write', fail)
    with pytest.raises(OSError, match='synthetic write failure'):
        await build(source)
    assert_removed(artifacts, source)


@pytest.mark.asyncio
@pytest.mark.parametrize('limits', [export.Limits(max_rows=1), export.Limits(max_bytes=10)])
async def test_budget_rejection_discards_unflushed_rows(artifacts, monkeypatch, limits):
    source = Source({'crm.customer':[row(1), row(2)]})
    writes = capture_member_writes(monkeypatch)
    with pytest.raises(export.ExportRejected) as error:
        await build(source, limits=limits)
    assert error.value.status == 413 and writes == []
    assert_removed(artifacts, source)


@pytest.mark.asyncio
@pytest.mark.parametrize('valid_count', [1, 51])
async def test_tenant_mismatch_discards_entire_partial_export(artifacts, monkeypatch, valid_count):
    foreign = {**row(999), 'workspace_id':'different-company'}
    source = Source({'crm.customer':[row(i) for i in range(valid_count)] + [foreign]})
    writes = capture_member_writes(monkeypatch)
    with pytest.raises(export.ExportRejected, match='归属校验失败'):
        await build(source)
    if valid_count == 1:
        assert writes == []
    else:
        assert writes  # The earlier valid batch must also be discarded.
        assert b'different-company' not in b''.join(content for _, content in writes)
    assert_removed(artifacts, source)


@pytest.mark.asyncio
async def test_cancel_while_cursor_waits_discards_pending_buffer(artifacts, monkeypatch):
    waiting = asyncio.Event()

    class WaitingSource(Source):
        async def rows(self, table):
            if table.name == 'crm.customer':
                yield row(1)
                waiting.set()
                await asyncio.Event().wait()

    source = WaitingSource()
    writes = capture_member_writes(monkeypatch)
    task = asyncio.create_task(build(source))
    try:
        await asyncio.wait_for(waiting.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert writes == []
        assert_removed(artifacts, source)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('interrupt', ['cancel', 'timeout'])
@pytest.mark.parametrize('write_fails', [False, True])
async def test_interrupt_joins_writer_before_member_close_and_cleanup(
    artifacts, monkeypatch, interrupt, write_fails,
):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    source = Source({'crm.customer':[row(1)]})
    real_write = zipfile._ZipWriteFile.write
    order = []

    def blocked_write(member, content):
        if member._zinfo.filename != 'crm/customer.ndjson':
            return real_write(member, content)
        started.set()
        assert release.wait(3), 'test failed to release synthetic writer'
        try:
            assert not member.closed and artifacts[0].exists()
            if write_fails:
                raise OSError('synthetic late write failure')
            return real_write(member, content)
        finally:
            order.append('writer-finished')
            finished.set()

    real_rmtree = export.shutil.rmtree

    def cleanup(path, *args, **kwargs):
        assert finished.is_set()
        order.append('cleanup')
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(zipfile._ZipWriteFile, 'write', blocked_write)
    monkeypatch.setattr(export.shutil, 'rmtree', cleanup)
    limits = export.Limits(timeout_seconds=0.1 if interrupt == 'timeout' else 3)
    task = asyncio.create_task(build(source, limits=limits))
    try:
        async with asyncio.timeout(2):
            while not started.is_set():
                await asyncio.sleep(0.001)
        if interrupt == 'cancel':
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()  # a second shutdown cancellation must not abandon the thread
            await asyncio.sleep(0)
        else:
            await asyncio.sleep(0.15)
        assert not task.done() and artifacts[0].exists() and not finished.is_set()
        release.set()
        if interrupt == 'cancel':
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        else:
            with pytest.raises(export.ExportRejected) as error:
                await asyncio.wait_for(task, 2)
            assert error.value.status == 504
        assert order == ['writer-finished', 'cleanup']
        assert_removed(artifacts, source)
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
