"""Maintenance proof checks cannot read an arbitrary file from a stored path."""
import hashlib
from uuid import uuid4

import pytest

from sales_backend.services.storage_reconciliation import local_bytes, staging_path, summary


def row(tmp_path):
    identifier=uuid4(); path=tmp_path/(str(identifier)+'.txt'); path.write_bytes(b'proof')
    return path,dict(import_id=identifier,workspace_id=uuid4(),state='reserved',driver='postgres',size_bytes=5,
        staging_path=str(path),content_sha256=hashlib.sha256(b'proof').hexdigest(),updated_at='now')


def test_proof_uses_actual_bytes_and_controlled_directory(tmp_path):
    path,allocation=row(tmp_path)
    assert staging_path(allocation,tmp_path)==path and local_bytes(path,allocation)==b'proof'
    path.write_bytes(b'wrong')
    with pytest.raises(ValueError,match='完整性'): local_bytes(path,allocation)
    (tmp_path/'elsewhere').mkdir()
    with pytest.raises(ValueError,match='目录'): staging_path(allocation,tmp_path/'elsewhere')
    with pytest.raises(FileNotFoundError): staging_path(allocation,tmp_path/'unavailable-volume')
    path.unlink(); path.symlink_to(tmp_path/'anything')
    with pytest.raises(ValueError,match='目录'): staging_path(allocation,tmp_path)
    with pytest.raises(OSError): local_bytes(path,allocation)


def test_inspection_never_returns_filename_paths_content_or_receipt(tmp_path):
    _,allocation=row(tmp_path); allocation.update(receipt=str(uuid4()),filename='private.txt')
    exposed=summary(allocation)
    assert set(exposed)=={'workspace_id','import_id','state','driver','size_bytes','updated_at'}
