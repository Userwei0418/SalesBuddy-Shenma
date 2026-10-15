"""CI entry: build current/previous candidates and verify fresh/upgrade databases."""
import argparse
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

from release_bundle import committed_files, prepare, write_archive
from verify_bundle_database import verify


def candidate(root, directory, revision, label):
    revision, files, modes = committed_files(root, revision)
    files, modes, _ = prepare(files, modes, revision=revision, product_name=label,
        api_url='http://127.0.0.1:18800/api/v1', appid='wx0000000000000000', profile='local')
    path = directory/(label+'.zip')
    return path, write_archive(files, modes, path)


async def check(args):
    with tempfile.TemporaryDirectory(prefix='salesbuddy-product-ci-') as temporary:
        directory = Path(temporary)
        incoming, digest = candidate(args.repo, directory, None, 'current-ci-candidate')
        previous, prior_digest = candidate(args.repo, directory, args.previous_revision, 'previous-ci-candidate')
        parameters = SimpleNamespace(archive=incoming, sha256=digest, port=args.port, user=args.user,
                                     previous=None, previous_sha256=None)
        empty = await verify(parameters)
        parameters.previous, parameters.previous_sha256 = previous, prior_digest
        upgrade = await verify(parameters)
        return {'empty': empty, 'upgrade': upgrade, 'installation_verified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--previous-revision', required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--user', required=True)
    try:
        report = asyncio.run(check(parser.parse_args()))
        # Full migration lists stay in the artifact ledger; keep CI output small.
        for entry in report.values():
            if isinstance(entry, dict):
                entry['applied_count'] = len(entry.pop('applied'))
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except Exception:
        raise SystemExit('Product candidate check failed; no installation or production deployment performed') from None
