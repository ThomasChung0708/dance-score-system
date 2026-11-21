"""Backup retention only touches disposable files under TemporaryDirectory."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from dance_results.sqlite_safety import KEEP_BACKUPS, prune_backups


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
        with patch.object(Path, 'iterdir', side_effect=PermissionError('locked directory')):
            with self.assertLogs('dance_results.sqlite_safety', level='WARNING') as logs:
                prune_backups(self.database, 'pre-submit', newest=newest)
        self.assertIn('locked directory', '\n'.join(logs.output))
        self.assertTrue(newest.exists())

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


if __name__ == '__main__':
    unittest.main()
