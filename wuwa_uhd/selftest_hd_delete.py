"""Backup destruction and confirmation tests use disposable fixtures only."""
import json
import os
from pathlib import Path
from unittest import mock

from .core import Cancelled, ToolError, exclusive_lock, load_json
from .hd_resources import DELETION_RECEIPT, HDResources, MANIFEST
from .selftest import GameFixture


class HDDeleteTests(GameFixture):
    def setUp(self):
        super().setUp()
        self.m.apply()
        self.h = self.m.hd_resources()
        self.backups = Path(self.temp.name) / "备份 磁盘"
        self.backups.mkdir()
        self.backup = Path(self.m.backup_remove_hd(self.backups)["backup_directory"])

    def plan(self):
        return self.m.prepare_delete_hd_backup(self.backup)

    def contents(self):
        return {str(p.relative_to(self.backup)): p.read_bytes() for p in self.backup.rglob("*") if p.is_file()}

    def test_delete_only_selected_backup_payload(self):
        (self.backup / "keep.txt").write_bytes(b"leave this log")
        other = self.backups / "other-backup"
        other.mkdir()
        (other / "keep.pak").write_bytes(b"other backup")
        game = self.snapshot()
        plan = self.plan()
        result = self.m.delete_hd_backup(plan, confirmed_playable=True)
        self.assertEqual(result["deleted_files"], len(self.hd_files))
        self.assertFalse((self.backup / "HD").exists())
        self.assertTrue((self.backup / MANIFEST).is_file())
        self.assertEqual((self.backup / "keep.txt").read_bytes(), b"leave this log")
        self.assertEqual((other / "keep.pak").read_bytes(), b"other backup")
        self.assertEqual(game, self.snapshot())
        self.assertEqual(load_json(self.backup / DELETION_RECEIPT)["stage"], "deleted")
        self.assertFalse(self.m.inspect()["hd_backup_usable"])
        with self.assertRaises(ToolError):
            self.m.restore_hd(self.backup)
        with self.assertRaises(ToolError):
            self.m.rollback()

    def test_preview_and_missing_confirmation_never_delete(self):
        before = self.contents()
        plan = self.plan()
        self.assertEqual(before, self.contents())
        for confirmation in (False, None, "yes", 1):
            with self.assertRaises(ToolError):
                self.m.delete_hd_backup(plan, confirmation)
        self.assertEqual(before, self.contents())

    def test_foreign_directory_and_game_paths_rejected(self):
        for directory in (self.root, self.m.work, self.backups, self.backup / "HD"):
            with self.assertRaises((ToolError, OSError)):
                self.m.prepare_delete_hd_backup(directory)
        self.assertTrue((self.backup / "HD").is_dir())

    def test_unknown_file_in_hd_blocks_all_deletion(self):
        (self.backup / "HD/keep.txt").write_bytes(b"unknown")
        before = self.contents()
        with self.assertRaises(ToolError):
            self.plan()
        self.assertEqual(before, self.contents())

    def test_changed_backup_after_confirmation_stops_before_deletion(self):
        plan = self.plan()
        path = self.backup / "HD" / next(iter(self.hd_files))
        path.write_bytes(b"x" * path.stat().st_size)
        before = self.contents()
        with self.assertRaises(ToolError):
            self.m.delete_hd_backup(plan, True)
        self.assertEqual(before, self.contents())

    def test_corrupt_backup_before_preview_is_not_deleted(self):
        path = self.backup / "HD" / next(iter(self.hd_files))
        path.write_bytes(b"x" * path.stat().st_size)
        plan = self.plan()
        before = self.contents()
        with self.assertRaises(ToolError):
            self.m.delete_hd_backup(plan, True)
        self.assertEqual(before, self.contents())

    def test_manifest_change_after_confirmation_stops(self):
        plan = self.plan()
        path = self.backup / MANIFEST
        path.write_bytes(path.read_bytes() + b"\n")
        before = self.contents()
        with self.assertRaises(ToolError):
            self.m.delete_hd_backup(plan, True)
        self.assertEqual(before, self.contents())

    def test_corrupt_uhd_blocks_backup_deletion(self):
        plan = self.plan()
        path = self.m.target / next(iter(self.files))
        path.write_bytes(b"x" * path.stat().st_size)
        before = self.contents()
        with self.assertRaises(ToolError):
            self.m.delete_hd_backup(plan, True)
        self.assertEqual(before, self.contents())

    def test_pending_restore_blocks_backup_deletion(self):
        with mock.patch.object(HDResources, "_copy_one", side_effect=Cancelled("paused")):
            with self.assertRaises(Cancelled):
                self.m.restore_hd(self.backup)
        before = self.contents()
        with self.assertRaises(ToolError):
            self.plan()
        self.assertEqual(before, self.contents())

    def test_running_game_and_operation_lock_block_deletion(self):
        plan = self.plan()
        before = self.contents()
        with exclusive_lock(self.m.work / "operation.lock"):
            with self.assertRaises(ToolError):
                self.m.delete_hd_backup(plan, True)
        self.m.guard = lambda root: ["Client-Win64-Shipping.exe"]
        with self.assertRaises(ToolError):
            self.m.delete_hd_backup(plan, True)
        self.assertEqual(before, self.contents())

    def test_hardlinked_backup_payload_is_rejected(self):
        path = self.backup / "HD" / next(iter(self.hd_files))
        outside = self.backups / "outside.pak"
        os.link(path, outside)
        with self.assertRaises(ToolError):
            self.plan()
        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes(), outside.read_bytes())

    def test_cancel_before_deletion_keeps_entire_backup(self):
        plan = self.plan()
        before = self.contents()
        self.m.cancel.set()
        with self.assertRaises(Cancelled):
            self.m.delete_hd_backup(plan, True)
        self.assertEqual(before, self.contents())

    def test_partial_delete_can_resume_after_new_confirmation(self):
        plan = self.plan()
        real = Path.unlink
        calls = []
        def interrupt(path, *args, **kwargs):
            if path.parent == self.backup / "HD":
                calls.append(path.name)
                if len(calls) == 2:
                    raise OSError("disk unavailable")
            return real(path, *args, **kwargs)
        with mock.patch.object(Path, "unlink", interrupt):
            with self.assertRaisesRegex(ToolError, "无法撤销"):
                self.m.delete_hd_backup(plan, True)
        with self.assertRaises(ToolError):
            self.m.restore_hd(self.backup)
        remaining = self.plan()
        self.assertTrue(remaining["resuming"])
        self.assertEqual(remaining["file_count"], len(self.hd_files) - 1)
        before = self.contents()
        with self.assertRaises(ToolError):
            self.m.delete_hd_backup(remaining)
        self.assertEqual(before, self.contents())
        self.m.delete_hd_backup(remaining, True)
        self.assertFalse((self.backup / "HD").exists())

    def test_crash_after_last_delete_can_finalize(self):
        from .core import atomic_json
        def interrupt(path, data):
            if data.get("stage") == "deleted":
                raise OSError("crash before final receipt")
            return atomic_json(path, data)
        with mock.patch("wuwa_uhd.hd_backup_delete.atomic_json", interrupt):
            with self.assertRaises(ToolError):
                self.m.delete_hd_backup(self.plan(), True)
        remaining = self.plan()
        self.assertEqual(remaining["file_count"], 0)
        self.m.delete_hd_backup(remaining, True)
        self.assertEqual(load_json(self.backup / DELETION_RECEIPT)["stage"], "deleted")

    def test_foreign_deletion_receipt_is_rejected(self):
        (self.backup / DELETION_RECEIPT).write_text('{"stage":"deleting"}', encoding="utf-8")
        before = self.contents()
        with self.assertRaises(ToolError):
            self.plan()
        self.assertEqual(before, self.contents())

    def test_legacy_bom_backup_can_be_selected_for_deletion(self):
        path = self.backup / MANIFEST
        data = load_json(path)
        del data["schema"]
        del data["version"]
        path.write_text(json.dumps(data), encoding="utf-8-sig")
        self.m.delete_hd_backup(self.plan(), True)
        self.assertFalse((self.backup / "HD").exists())

    def test_gui_both_confirmations_are_required(self):
        from .gui import App
        app = App.__new__(App)
        app.root, app.start, app.status = object(), mock.Mock(), mock.Mock()
        plan = self.plan()
        before = self.contents()
        with mock.patch("wuwa_uhd.gui.HDBackupDeleteDialog") as dialog, mock.patch("wuwa_uhd.gui.messagebox.askyesno") as final:
            dialog.return_value.result = None
            app.confirm_prepared_backup_delete(plan)
            final.assert_not_called()
            app.start.assert_not_called()
            dialog.return_value.result = True
            final.return_value = False
            app.confirm_prepared_backup_delete(plan)
            app.start.assert_not_called()
            self.assertEqual(final.call_args.kwargs["default"], "no")
            self.assertIn("正常游玩", final.call_args.args[1])
            final.return_value = True
            app.confirm_prepared_backup_delete(plan)
            app.start.assert_called_once_with("delete_hd_backup", plan, True)
        self.assertEqual(before, self.contents())

    def test_gui_unchecked_playability_checkbox_blocks_confirmation(self):
        from .gui import HDBackupDeleteDialog
        dialog = HDBackupDeleteDialog.__new__(HDBackupDeleteDialog)
        dialog.confirmed = mock.Mock()
        dialog.confirm_button = mock.Mock()
        dialog.confirmed.get.return_value = False
        self.assertFalse(dialog.validate())
        dialog._toggle()
        dialog.confirm_button.configure.assert_called_with(state="disabled")
        dialog.confirmed.get.return_value = True
        self.assertTrue(dialog.validate())

    def test_gui_selecting_backup_only_requests_readonly_preview(self):
        from .gui import App
        app = App.__new__(App)
        app.root, app.start, app.busy = object(), mock.Mock(), False
        with mock.patch("wuwa_uhd.gui.filedialog.askdirectory", return_value=str(self.backup)):
            app.confirm_delete_hd_backup()
        app.start.assert_called_once_with("prepare_delete_hd_backup", str(self.backup))
