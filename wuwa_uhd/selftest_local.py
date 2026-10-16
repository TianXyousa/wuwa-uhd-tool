"""Local preview regression tests; all resources are disposable fixtures."""
import hashlib
from pathlib import Path
import threading
from unittest import mock

from .core import Cancelled, ToolError, hash_file
from .progress import FileProgress
from .selftest import GameFixture


class LocalPreviewTests(GameFixture):
    def backup_fixture(self):
        self.m.apply()
        parent = Path(self.temp.name) / "backups"
        parent.mkdir()
        return Path(self.m.backup_remove_hd(parent)["backup_directory"])

    def all_files(self):
        return {str(p.relative_to(self.temp.name)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in Path(self.temp.name).rglob("*") if p.is_file()}

    def test_backup_inspection_is_readonly_and_distinguishes_hashes(self):
        backup = self.backup_fixture()
        before = self.all_files()
        with mock.patch.object(self.transport, "metadata", side_effect=AssertionError("network")):
            quick = self.m.inspect_hd_backup(backup)
            full = self.m.inspect_hd_backup(backup, True)
        self.assertEqual(quick["status"], "inventory_ok")
        self.assertFalse(quick["hashes_verified"])
        self.assertEqual(full["status"], "verified")
        self.assertEqual(before, self.all_files())
        self.assertIsNone(self.m.inspect(False)["hd_backup_usable"])

    def test_missing_backup_file_is_incomplete_not_usable(self):
        backup = self.backup_fixture()
        (backup / "HD" / next(iter(self.hd_files))).unlink()
        result = self.m.inspect_hd_backup(backup, True)
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["hashes_verified"])
        self.assertEqual(result["file_count"], len(self.hd_files) - 1)
        self.assertFalse(self.m.inspect(False)["hd_backup_usable"])

    def test_same_size_corruption_requires_full_hash_check(self):
        backup = self.backup_fixture()
        path = backup / "HD" / next(iter(self.hd_files))
        path.write_bytes(b"x" * path.stat().st_size)
        before = self.all_files()
        self.assertEqual(self.m.inspect_hd_backup(backup)["status"], "inventory_ok")
        with self.assertRaises(ToolError):
            self.m.inspect_hd_backup(backup, True)
        self.assertEqual(before, self.all_files())

    def test_deleted_backup_remains_visible_but_not_verified(self):
        backup = self.backup_fixture()
        self.m.delete_hd_backup(self.m.prepare_delete_hd_backup(backup), True)
        before = self.all_files()
        result = self.m.inspect_hd_backup(backup, True)
        self.assertEqual(result["status"], "deleted")
        self.assertEqual(result["file_count"], 0)
        self.assertFalse(result["hashes_verified"])
        self.assertEqual(before, self.all_files())

    def test_incomplete_deletion_is_not_a_recoverable_backup(self):
        from .core import atomic_json
        from .hd_resources import DELETION_RECEIPT
        backup = self.backup_fixture()
        plan = self.m.prepare_delete_hd_backup(backup)
        record = self.m.hd_resources()._record(self.m.read_state(), backup, plan["manifest_sha256"])
        atomic_json(backup / DELETION_RECEIPT, dict(record, stage="deleting"))
        (backup / "HD" / next(iter(self.hd_files))).unlink()
        self.assertEqual(self.m.inspect_hd_backup(backup, True)["status"], "deleting")

    def test_unknown_files_or_foreign_receipts_cannot_look_healthy(self):
        from .hd_resources import DELETION_RECEIPT
        backup = self.backup_fixture()
        unknown = backup / "HD" / "extra.txt"
        unknown.write_bytes(b"keep")
        with self.assertRaises(ToolError):
            self.m.inspect_hd_backup(backup)
        self.assertTrue(unknown.exists())
        unknown.unlink()
        (backup / DELETION_RECEIPT).write_text('{"stage":"deleted"}', encoding="utf-8")
        with self.assertRaises(ToolError):
            self.m.inspect_hd_backup(backup)

    def test_game_and_cache_cannot_be_selected_as_backup(self):
        for directory in (self.root, self.m.work, self.root.parent):
            with self.assertRaises(ToolError):
                self.m.inspect_hd_backup(directory)
        self.assertFalse(self.m.work.exists())

    def test_manifest_changed_during_hashing_is_rejected(self):
        backup = self.backup_fixture()
        h = self.m.hd_resources()
        original = h._verify
        def change(*args, **kwargs):
            result = original(*args, **kwargs)
            path = backup / "manifest-sha256.json"
            path.write_bytes(path.read_bytes() + b"\n")
            return result
        with mock.patch.object(self.m, "hd_resources", return_value=h), mock.patch.object(h, "_verify", change):
            with self.assertRaises(ToolError):
                self.m.inspect_hd_backup(backup, True)

    def test_cancelled_backup_inspection_does_not_write(self):
        backup = self.backup_fixture()
        before = self.all_files()
        self.m.cancel.set()
        with self.assertRaises(Cancelled):
            self.m.inspect_hd_backup(backup, True)
        self.assertEqual(before, self.all_files())

    def test_missing_backup_does_not_break_game_inspection(self):
        backup = self.backup_fixture()
        (backup / "manifest-sha256.json").unlink()
        result = self.m.inspect(False)
        self.assertEqual(result["status"], "active")
        self.assertFalse(result["hd_backup_usable"])
        self.assertEqual(result["hd_backup_details"]["status"], "unavailable")

    def test_resume_network_counter_excludes_existing_bytes(self):
        self.begin_partial(count=4096)
        self.m.apply()
        events = [e for e in self.events if e["kind"] == "progress"]
        self.assertEqual(events[-1]["network_bytes"], self.m.total - 4096)

    def test_reused_cache_has_zero_network_bytes(self):
        self.m.apply()
        self.m.rollback()
        self.events.clear()
        self.make_manager().apply()
        events = [e for e in self.events if e["kind"] == "progress"]
        self.assertTrue(events)
        self.assertTrue(all(e["network_bytes"] == 0 for e in events))

    def test_file_progress_reports_midfile_and_throttles(self):
        events = []
        with mock.patch("wuwa_uhd.progress.time.monotonic", return_value=0) as clock:
            progress = FileProgress(events.append, "hash", Path("large.pak"), 100)
            progress(1)
            self.assertEqual(len(events), 1)
            clock.return_value = 0.3
            progress(50)
            self.assertEqual(events[-1]["current"], 50)
            progress(100)
        self.assertEqual([e["current"] for e in events], [0, 50, 100])

    def test_hash_progress_can_cancel_before_whole_file_finishes(self):
        path = Path(self.temp.name) / "large.pak"
        path.write_bytes(b"a" * (9 * 1024 * 1024))
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        cancel = threading.Event()
        seen = []
        def progress(count):
            seen.append(count)
            cancel.set()
        with self.assertRaises(Cancelled):
            hash_file(path, cancel=cancel, progress=progress)
        self.assertLess(seen[-1], path.stat().st_size)
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_gui_offline_result_does_not_claim_online_verification(self):
        from .gui import App
        app = App.__new__(App)
        app.status, app.summary, app.progress_text, app.backup_status, app.append = [mock.Mock() for _ in range(5)]
        app.backup = mock.Mock()
        app.backup.get.return_value = ""
        app.finish("inspect", self.m.inspect(False))
        self.assertIn("未核对服务器当前版本", app.summary.set.call_args.args[0])
        self.assertNotIn("在线官方索引匹配", app.summary.set.call_args.args[0])

    def test_gui_changing_game_clears_old_backup_and_results(self):
        from .gui import App
        app = App.__new__(App)
        app.status, app.summary, app.backup, app.backup_status = [mock.Mock() for _ in range(4)]
        app.path_changed()
        app.backup.set.assert_called_once_with("")
        self.assertIn("尚未检查", app.summary.set.call_args.args[0])
