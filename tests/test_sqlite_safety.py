"""Backup retention only touches disposable files under TemporaryDirectory."""
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from dance_results.sqlite_safety import KEEP_BACKUPS, audit, prune_backups, verified_backup


class BackupRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        # Glob metacharacters in a database name must be treated literally.
        self.database = self.base / 'library[1].sqlite3'
        self.directory = self.base / 'backups'
        self.directory.mkdir()

    def backup(self, label, index, directory=None):
        directory = directory or self.directory
        path = directory / (
            f'{self.database.stem}.{label}.20000101T000000{index:06d}Z.1234abcd.sqlite3'
        )
        path.write_bytes(b'temporary backup')
        return path

    def test_retention_is_per_label_and_preserves_unrelated_files(self):
        labels = ('pre-game-sync', 'pre-submit')
        backups = {
            label: [self.backup(label, index) for index in range(12)]
            for label in labels
        }
        untouched = [
            self.backup('pre-v5', 0),
            self.backup('pre-v10-old-schema', 0),
            self.backup('pre-game-sync-extra', 0),
            self.backup('pre-submit-extra', 0),
            self.directory / 'library.pre-v10-old.sqlite3',
            self.directory / 'random.sqlite3',
            self.directory / 'other.pre-submit.old.sqlite3',
            self.directory / f'{self.database.stem}.pre-submit.sqlite3',
            self.directory / f'{self.database.stem}.pre-game-sync.sqlite3',
        ]
        for path in untouched:
            path.touch()
        nested = self.directory / 'nested'
        nested.mkdir()
        untouched.append(self.backup('pre-submit', 0, nested))
        untouched.append(self.backup('pre-game-sync', 0, self.base))
        matching_directory = self.backup('pre-submit', 99)
        matching_directory.unlink()
        matching_directory.mkdir()
        untouched.append(matching_directory)

        for index, label in enumerate(labels):
            prune_backups(self.database, label, newest=backups[label][-1])
            self.assertEqual(
                [path.exists() for path in backups[label]],
                [False] * 2 + [True] * KEEP_BACKUPS,
            )
            self.assertTrue(all(path.exists() for path in untouched))
            if index == 0:
                self.assertTrue(all(path.exists() for path in backups['pre-submit']))

    def test_unknown_labels_are_never_pruned(self):
        for label in ('pre-v5', 'pre-v10-old-schema', 'unknown'):
            with self.subTest(label=label):
                paths = [self.backup(label, index) for index in range(12)]
                prune_backups(self.database, label, newest=paths[-1], keep=1)
                self.assertTrue(all(path.exists() for path in paths))

    def test_only_exact_generated_names_count_towards_retention(self):
        paths = [self.backup('pre-submit', index) for index in range(13)]
        prefix = f'{self.database.stem}.pre-submit'
        decoys = {
            f'{prefix}.0-before-upgrade.sqlite3',
            f'{prefix}.pre-submit.20000101T000000000000Z.1234abcd.sqlite3',
            paths[0].name + '-journal',
            f'{prefix}.19990101T000000000000Z.1234ABCD.sqlite3',
            f'{prefix}.20000101T000000Z.1234abcd.sqlite3',
            f'{prefix}.20000101T0000000000000Z.1234abcd.sqlite3',
            f'{prefix}.2000010T000000000000Z.1234abcd.sqlite3',
            f'{self.database.stem}.pre-v5.20000101T000000000000Z.1234abcd.sqlite3',
            f'{prefix}.20000101T000000000000Z.1234abcd.txt',
            'library1.pre-submit.20000101T000000000000Z.1234abcd.sqlite3',
        }
        contents = {}
        for name in decoys:
            path = self.directory / name
            contents[path] = name.encode('utf-8')
            path.write_bytes(contents[path])

        prune_backups(self.database, 'pre-submit', newest=paths[-1])

        self.assertEqual(set(paths[-KEEP_BACKUPS:]), set(self.directory.iterdir()) - contents.keys())
        for path, expected in contents.items():
            self.assertEqual(expected, path.read_bytes())

    def test_fewer_than_keep_valid_backups_are_untouched(self):
        paths = [self.backup('pre-submit', index) for index in range(KEEP_BACKUPS - 1)]
        decoy = self.directory / f'{self.database.stem}.pre-submit.0-renamed.sqlite3'
        decoy.write_bytes(b'user copy')
        before = {path: path.read_bytes() for path in self.directory.iterdir()}
        prune_backups(self.database, 'pre-submit', newest=paths[-1])
        self.assertEqual(before, {path: path.read_bytes() for path in self.directory.iterdir()})

    def test_non_generated_newest_prevents_pruning(self):
        paths = [self.backup('pre-submit', index) for index in range(12)]
        newest = self.directory / f'{self.database.stem}.pre-submit.0-renamed.sqlite3'
        newest.write_bytes(b'user copy')
        with patch.object(Path, 'iterdir') as scan:
            prune_backups(self.database, 'pre-submit', newest=newest)
        scan.assert_not_called()
        self.assertTrue(all(path.exists() for path in paths))
        self.assertEqual(b'user copy', newest.read_bytes())

    def test_just_created_backup_survives_clock_rollback_and_zero_keep(self):
        for label in ('pre-game-sync', 'pre-submit'):
            with self.subTest(label=label):
                paths = [self.backup(label, index) for index in range(12)]
                prune_backups(self.database, label, newest=paths[0])
                self.assertTrue(paths[0].exists())
                self.assertFalse(paths[1].exists())
                self.assertFalse(paths[2].exists())
                self.assertTrue(all(path.exists() for path in paths[3:]))
                prune_backups(self.database, label, newest=paths[0], keep=0)
                self.assertEqual([paths[0]], list(self.directory.glob(f'*.{label}.*')))

    def test_external_or_missing_new_backup_prevents_pruning(self):
        paths = [self.backup('pre-submit', index) for index in range(12)]
        outside = self.backup('pre-submit', 0, self.base)
        for newest in (outside, self.directory / 'missing.sqlite3'):
            prune_backups(self.database, 'pre-submit', newest=newest)
            self.assertTrue(all(path.exists() for path in paths))

    def test_directory_access_failure_is_logged(self):
        newest = self.backup('pre-submit', 0)
        for error in (PermissionError, ValueError, TypeError):
            with self.subTest(error=error):
                with patch.object(Path, 'iterdir', side_effect=error('locked directory')):
                    with self.assertLogs('dance_results.sqlite_safety', level='WARNING') as logs:
                        prune_backups(self.database, 'pre-submit', newest=newest)
                self.assertIn('locked directory', '\n'.join(logs.output))
                self.assertIsNotNone(logs.records[0].exc_info)
        self.assertTrue(newest.exists())

    def test_unlink_exceptions_are_logged_and_other_old_files_are_pruned(self):
        for label, error in (('pre-submit', ValueError), ('pre-game-sync', TypeError)):
            with self.subTest(error=error):
                paths = [self.backup(label, index) for index in range(12)]
                unlink = Path.unlink

                def refuse_first(path, *args, **kwargs):
                    if path == paths[0]:
                        raise error('cannot unlink')
                    return unlink(path, *args, **kwargs)

                with patch.object(Path, 'unlink', refuse_first):
                    with self.assertLogs('dance_results.sqlite_safety', level='WARNING') as logs:
                        prune_backups(self.database, label, newest=paths[-1])
                self.assertIn('cannot unlink', '\n'.join(logs.output))
                self.assertIsNotNone(logs.records[0].exc_info)
                self.assertTrue(paths[0].exists())
                self.assertFalse(paths[1].exists())
                self.assertTrue(all(path.exists() for path in paths[2:]))

    def test_redirected_backups_directory_is_never_traversed(self):
        outside = self.base / 'outside'
        outside.mkdir()
        paths = [self.backup('pre-submit', index, outside) for index in range(12)]
        self.directory.rmdir()
        if os.name == 'nt':
            subprocess.run(
                ['cmd', '/c', 'mklink', '/J', str(self.directory), str(outside)],
                check=True,
                capture_output=True,
            )
        else:
            self.directory.symlink_to(outside, target_is_directory=True)
        try:
            with self.assertLogs('dance_results.sqlite_safety', level='WARNING'):
                prune_backups(
                    self.database, 'pre-submit', newest=self.directory / paths[-1].name
                )
            self.assertTrue(all(path.exists() for path in paths))
        finally:
            if os.name == 'nt':
                self.directory.rmdir()
            else:
                self.directory.unlink()


class VerifiedBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / 'scores.sqlite3'
        self.directory = self.database.parent / 'backups'
        self.directory.mkdir()
        self.existing = {
            self.directory / 'scores.pre-submit.20000101T000000000000Z.1234abcd.sqlite3': b'old backup',
            self.directory / 'scores.pre-submit.20000101T000000000000Z.1234abcd.sqlite3-journal': b'old journal',
            self.directory / 'user-copy.sqlite3': b'user copy',
        }
        for path, contents in self.existing.items():
            path.write_bytes(contents)
        self.db = sqlite3.connect(self.database)
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE scores(value INTEGER)')
        self.db.execute('INSERT INTO scores VALUES(42)')
        self.db.execute('PRAGMA user_version=7')
        self.db.commit()
        self.db.execute('BEGIN IMMEDIATE')

    def assert_existing_unchanged(self):
        self.assertEqual(
            self.existing,
            {path: path.read_bytes() for path in self.directory.iterdir()},
        )

    def test_success_keeps_verified_backup(self):
        target = Path(verified_backup(self.db, self.database, 'pre-submit'))
        self.assertTrue(target.is_file())
        with closing(sqlite3.connect(target)) as backup:
            self.assertEqual(audit(self.db), audit(backup))
            self.assertEqual(7, backup.execute('PRAGMA user_version').fetchone()[0])
        for path, contents in self.existing.items():
            self.assertEqual(contents, path.read_bytes())

    def test_failed_integrity_removes_only_created_backup_and_its_sidecars(self):
        error = sqlite3.DatabaseError('injected backup integrity failure')
        created = []
        connections = []

        def fail_backup_check(db):
            if db is self.db:
                return
            connections.append(db)
            target = Path(db.execute('PRAGMA database_list').fetchone()[2])
            created.append(target)
            for suffix in ('-journal', '-wal', '-shm'):
                sidecar = Path(str(target) + suffix)
                sidecar.write_bytes(b'failed backup sidecar')
                created.append(sidecar)
            raise error

        with patch('dance_results.sqlite_safety.check_integrity', side_effect=fail_backup_check):
            with self.assertRaises(sqlite3.DatabaseError) as raised:
                verified_backup(self.db, self.database, 'pre-submit')
        self.assertIs(error, raised.exception)
        self.assertEqual(4, len(created))
        self.assertTrue(all(not path.exists() for path in created))
        with self.assertRaises(sqlite3.ProgrammingError):
            connections[0].execute('SELECT 1')
        self.assert_existing_unchanged()

    def test_audit_mismatch_removes_failed_backup(self):
        with patch('dance_results.sqlite_safety.audit', side_effect=[{}, {'different': True}]):
            with self.assertRaisesRegex(ValueError, 'SQLite 備份稽核失敗，未繼續寫入。'):
                verified_backup(self.db, self.database, 'pre-submit')
        self.assert_existing_unchanged()

    def test_cleanup_failure_logs_and_preserves_original_exception(self):
        error = ValueError('original verification failure')
        unlink = Path.unlink
        attempted = []

        def refuse_target(path, *args, **kwargs):
            attempted.append(path)
            if path.suffix == '.sqlite3':
                raise TypeError('cleanup failed')
            return unlink(path, *args, **kwargs)

        with patch('dance_results.sqlite_safety.check_integrity', side_effect=[None, error]):
            with patch.object(Path, 'unlink', refuse_target):
                with self.assertLogs('dance_results.sqlite_safety', level='WARNING') as logs:
                    with self.assertRaises(ValueError) as raised:
                        verified_backup(self.db, self.database, 'pre-submit')
        self.assertIs(error, raised.exception)
        self.assertEqual(4, len(attempted))
        self.assertIn('cleanup failed', '\n'.join(logs.output))
        self.assertIsNotNone(logs.records[0].exc_info)
        self.assertTrue(attempted[0].exists())
        for path, contents in self.existing.items():
            self.assertEqual(contents, path.read_bytes())

    def test_existing_target_is_never_deleted(self):
        target = self.directory / 'scores.pre-submit.20000101T000000000000Z.1234abcd.sqlite3'
        with patch('dance_results.sqlite_safety.datetime') as clock:
            clock.now.return_value.strftime.return_value = '20000101T000000000000Z'
            with patch('dance_results.sqlite_safety.uuid4') as uuid:
                uuid.return_value.hex = '1234abcd' + '0' * 24
                with self.assertRaises(FileExistsError):
                    verified_backup(self.db, self.database, 'pre-submit')
        self.assertEqual(b'old backup', target.read_bytes())
        self.assert_existing_unchanged()


if __name__ == '__main__':
    unittest.main()
