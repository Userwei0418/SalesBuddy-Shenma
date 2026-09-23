"""Exercise certificate replacement and rollback without touching a live server."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('hook', Path(__file__).with_name('deploy-certificate.py'))
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class DeploymentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.domain = 'salesbuddy.shenzhoukuntai.com'
        self.lineage = self.root / 'le/live' / self.domain
        self.lineage.mkdir(parents=True)
        self.target = self.root / 'tls'
        self.target.mkdir()
        (self.target / 'server.crt').write_bytes(b'old certificate')
        (self.target / 'server.key').write_bytes(b'old private key')
        (self.target / 'server.key').chmod(0o600)
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-days', '7', '-subj', '/CN=' + self.domain,
                        '-keyout', str(self.lineage / 'privkey.pem'),
                        '-out', str(self.lineage / 'fullchain.pem')],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        patches = [patch.object(hook, 'LE_ROOT', self.root / 'le'),
                   patch.object(hook, 'STATE_ROOT', self.root / 'state'),
                   patch.object(hook, 'TARGETS', {'salesbuddy': (self.domain, str(self.target))}),
                   patch.object(hook.socket, 'gethostname', return_value='salesbuddy'),
                   patch.object(hook.os, 'geteuid', return_value=0),
                   patch.dict(os.environ, {'RENEWED_LINEAGE': str(self.lineage)})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def exercise(self, fail_reload=False):
        commands = []
        def run(*args, cwd=None):
            commands.append(args)
            if args[0] == 'openssl':
                subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif fail_reload and args[:2] == ('systemctl', 'reload'):
                raise subprocess.CalledProcessError(1, args)
        with patch.object(hook, 'run', side_effect=run):
            hook.main()
        return commands

    def test_apply_valid_pair_and_reload(self):
        calls = self.exercise()
        self.assertEqual((self.target / 'server.crt').read_bytes(), (self.lineage / 'fullchain.pem').read_bytes())
        self.assertEqual((self.target / 'server.key').read_bytes(), (self.lineage / 'privkey.pem').read_bytes())
        self.assertEqual((self.target / 'server.key').stat().st_mode & 0o777, 0o600)
        self.assertIn(('nginx', '-t'), calls)
        self.assertIn(('systemctl', 'reload', 'nginx'), calls)
        self.assertTrue((self.root / 'state/last-deploy.json').is_file())

    def test_reload_failure_restores_both_files(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.exercise(fail_reload=True)
        self.assertEqual((self.target / 'server.crt').read_bytes(), b'old certificate')
        self.assertEqual((self.target / 'server.key').read_bytes(), b'old private key')
        self.assertEqual((self.target / 'server.key').stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.root / 'state').exists())

    def test_other_lineage_does_not_modify_service(self):
        with patch.dict(os.environ, {'RENEWED_LINEAGE': '/etc/letsencrypt/live/unrelated'}):
            self.assertEqual(self.exercise(), [])
        self.assertEqual((self.target / 'server.crt').read_bytes(), b'old certificate')


if __name__ == '__main__':
    unittest.main()
