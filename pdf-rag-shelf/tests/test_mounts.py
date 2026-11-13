from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import shutil
from rag_shelf.domain import Profile
from rag_shelf.filesystem import MountedSources
from rag_shelf.storage import Repository
from rag_shelf.service import IndexService
from test_python import FakeEmbedder, FakeExtractor

class MutableMounts(MountedSources):
    snapshot = 'original nested mount'

    def capture(self, root):
        return self.snapshot


class MountSafetyTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'papers'
        self.root.mkdir()
        self.paper = self.root / 'first.pdf'
        self.paper.write_bytes(b'first')
        self.mounts = MutableMounts()
        self.repo = Repository(self.base / 'index.sqlite3', self.mounts)
        self.addCleanup(self.repo.close)
        self.profile = Profile('nomic-embed-text-v2-moe:latest', 'digest')
        self.service = IndexService(FakeEmbedder(), FakeExtractor(), self.repo, lambda *_: None)
        self.service.index(self.root, self.profile)

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def test_nested_mount_disappearance_blocks_preview(self):
        before = self.repo.stats()
        self.paper.unlink()
        self.mounts.snapshot = 'underlying empty directory'
        with self.assertRaisesRegex(ValueError, 'Mounted sources changed'):
            self.repo.prune()
        self.assertEqual(self.repo.stats(), before)

    def test_index_cannot_silently_replace_missing_mount_baseline(self):
        self.mounts.snapshot = 'new filesystem'
        with self.assertRaisesRegex(ValueError, 'Mounted sources changed'):
            self.service.index(self.root, self.profile, accept_source_baseline=True)
        self.assertEqual(self.repo.get('mounted_sources'), 'original nested mount')

    def test_mount_disappearance_between_preview_and_apply_preserves_vectors(self):
        self.paper.unlink()
        preview = self.repo.prune()
        self.mounts.snapshot = 'missing mount'
        with self.assertRaises(ValueError):
            self.repo.prune(preview['token'])
        self.assertEqual(self.repo.stats()['chunks'], 2)

    def test_changed_mount_during_scan_prevents_completion(self):
        original = self.service._index_one
        def changed(*args):
            value = original(*args)
            self.mounts.snapshot = 'mount changed during scan'
            return value
        with patch.object(self.service, '_index_one', changed):
            with self.assertRaises(ValueError):
                self.service.index(self.root, self.profile)
        self.assertEqual(self.repo.get('scan_complete'), '0')
        self.assertEqual(self.repo.get('mounted_sources'), 'original nested mount')

    def test_legacy_mount_baseline_needs_explicit_adoption(self):
        with self.repo.transaction():
            self.repo.db.execute("DELETE FROM meta WHERE key='mounted_sources'")
        with self.assertRaisesRegex(ValueError, 'baseline'):
            self.repo.prune()
        with self.assertRaisesRegex(ValueError, '--accept-source-baseline'):
            self.service.index(self.root, self.profile)
        self.assertEqual(self.service.index(self.root, self.profile, accept_source_baseline=True), 0)
        self.assertEqual(self.repo.prune()['chunks'], 0)

    def test_mountinfo_records_same_device_bind_and_escaped_paths(self):
        nested = self.root / 'Sound files'
        nested.mkdir()
        table = self.base / 'mountinfo'
        escaped = str(nested).replace(' ', '\\040')
        table.write_text('1 0 1:1 / / rw - ext4 /dev/root rw\n'
                         f'2 1 1:1 /source {escaped} rw - ext4 /dev/root rw\n')
        tracker = MountedSources(table)
        snapshot = tracker.capture(self.root)
        self.assertIn('Sound files', snapshot)
        table.write_text('1 0 1:1 / / rw - ext4 /dev/root rw\n')
        with self.assertRaisesRegex(ValueError, 'Mounted sources changed'):
            tracker.validate(self.root, snapshot)

    def test_unreadable_mount_table_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'Cannot inspect'):
            MountedSources(self.base / 'absent').capture(self.root)

    def test_same_root_identity_change_can_be_verified_by_relocation(self):
        self.mounts.snapshot = 'legitimately remounted source'
        self.assertEqual(self.service.index(self.root, self.profile, relocate=True), 0)
        self.assertEqual(self.repo.get('mounted_sources'), 'legitimately remounted source')
        self.assertEqual(self.repo.stats()['files'], 1)

    def test_relocation_flag_cannot_accept_an_empty_missing_mount(self):
        self.paper.unlink()
        self.mounts.snapshot = 'missing source'
        with self.assertRaisesRegex(ValueError, 'no matching copy'):
            self.service.index(self.root, self.profile, relocate=True)
        self.assertEqual(self.repo.stats()['chunks'], 2)
        self.assertEqual(self.repo.get('mounted_sources'), 'original nested mount')

    def test_relocation_swaps_mount_baseline_only_on_commit(self):
        target = self.base / 'target'
        shutil.copytree(self.root, target)
        self.mounts.snapshot = 'target mounts'
        self.repo.bind(self.profile, target, True)
        self.assertEqual(self.repo.get('mounted_sources'), 'original nested mount')
        self.repo.cancel_relocation()
        self.service.index(target, self.profile, relocate=True)
        self.assertEqual(self.repo.get('mounted_sources'), 'target mounts')
