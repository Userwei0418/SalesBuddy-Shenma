from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from migrate import MigrationError, discover, transactional_sql


class MigrationFilesTest(unittest.TestCase):
    def test_discovers_legacy_and_future_versions_in_numeric_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'migrations').mkdir()
            for path in ['V035__legacy.sql', 'migrations/V100__new.sql', 'migrations/V040__new.sql']:
                (root / path).write_text('SELECT 1;')
            self.assertEqual([m.key for m in discover(root)], ['V035', 'V040', 'V100'])

    def test_duplicate_versions_fail_before_connecting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'migrations').mkdir()
            (root / 'V035__legacy.sql').write_text('SELECT 1;')
            (root / 'migrations/V035__different.sql').write_text('SELECT 2;')
            with self.assertRaises(MigrationError):
                discover(root)

    def test_only_outer_transaction_and_pg_dump_guards_are_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'V040__test.sql'
            path.write_text('-- comment\nBEGIN;\nDO $$ BEGIN PERFORM 1; END $$;\nCOMMIT;\n')
            self.assertIn('DO $$ BEGIN PERFORM 1; END $$;', transactional_sql(path))
            self.assertNotIn('COMMIT;', transactional_sql(path))
            path.write_text('\\restrict key\nSELECT 1;\n\\unrestrict key\n')
            self.assertEqual(transactional_sql(path).strip(), 'SELECT 1;')

    def test_unexpected_directives_and_transaction_control_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'V040__test.sql'
            for sql in ['\\i other.sql', 'BEGIN;\nSELECT 1;', 'SELECT 1;\nCOMMIT;']:
                path.write_text(sql)
                with self.assertRaises(MigrationError):
                    transactional_sql(path)

    def test_real_baseline_and_all_migrations_are_parseable(self):
        root = Path(__file__).resolve().parents[1]
        self.assertGreaterEqual(len(discover(root)), 5)
        for path in [root / 'baseline/V001__current_schema.sql', *(migration.path for migration in discover(root))]:
            self.assertTrue(transactional_sql(path).strip())


if __name__ == '__main__':
    unittest.main()
