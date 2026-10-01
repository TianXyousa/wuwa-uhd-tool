"""Destructive paths are exercised only on small temporary game fixtures."""
import hashlib
import json
import os
from pathlib import Path
from unittest import mock

from .core import HD_MANIFEST_MD5, Cancelled, ToolError, bundled_manifest, parse_manifest
from .hd_resources import HDResources, MANIFEST
from .selftest import GameFixture


class HDTests(GameFixture):
    def setUp(self):
        super().setUp()
        self.m.apply()
        self.h = self.m.hd_resources()
        self.backups = Path(self.temp.name) / "备份 磁盘"
        self.backups.mkdir()

    def remove(self):
        result = self.m.backup_remove_hd(self.backups)
        return Path(result["backup_directory"])

    def test_official_hd_manifest_is_pinned(self):
        entries = parse_manifest(bundled_manifest("HD"), HD_MANIFEST_MD5, "HD")
        self.assertEqual(len(entries), 100)
        self.assertEqual(sum(e["size"] for e in entries), 45713445823)

    def test_backup_remove_inspect_restore_rollback(self):
        active = self.snapshot()
        backup = self.remove()
        self.assertFalse(self.h.hd.exists())
        inspection = self.m.inspect()
        self.assertFalse(inspection["hd_present"])
        self.assertEqual(inspection["hd_operation"], "removed")
        for name, content in self.hd_files.items():
            self.assertEqual((backup / "HD" / name).read_bytes(), content)
        self.m.restore_hd(backup)
        self.assertEqual(active, self.snapshot())
        self.assertTrue((backup / MANIFEST).exists())
        self.m.rollback()
        self.assertEqual(self.original, self.snapshot())

    def test_rollback_without_hd_preserves_uhd(self):
        self.remove()
        before = self.snapshot()
        with self.assertRaisesRegex(ToolError, "恢复 HD"):
            self.m.rollback()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.m.read_state()["status"], "active")

    def test_rollback_with_corrupt_hd_preserves_uhd(self):
        name = next(iter(self.hd_files))
        (self.h.hd / name).write_bytes(b"x" * len(self.hd_files[name]))
        before = self.snapshot()
        with self.assertRaises(ToolError):
            self.m.rollback()
        self.assertEqual(before, self.snapshot())

    def test_corrupt_uhd_blocks_hd_removal(self):
        name = next(iter(self.files))
        (self.m.target / name).write_bytes(b"x" * len(self.files[name]))
        before = self.snapshot()
        with self.assertRaises(ToolError):
            self.remove()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_unknown_hd_file_blocks_removal(self):
        (self.h.hd / "user.txt").write_bytes(b"keep")
        before = self.snapshot()
        with self.assertRaises(ToolError):
            self.remove()
        self.assertEqual(before, self.snapshot())

    def test_backup_inside_game_or_cache_is_rejected(self):
        before = self.snapshot()
        for parent in (self.m.root, self.h.hd, self.m.work):
            with self.assertRaises(ToolError):
                self.m.backup_remove_hd(parent)
        self.assertEqual(before, self.snapshot())

    def test_backup_copy_failure_keeps_original(self):
        before = self.snapshot()
        with mock.patch.object(HDResources, "_copy_one", side_effect=OSError("disk disconnected")):
            with self.assertRaises(OSError):
                self.remove()
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.h.journal.exists())

    def test_corrupted_backup_before_commit_preserves_hd(self):
        original_copy = HDResources._copy_one
        def corrupt(h, source, target, entry, expected_sha=None):
            record = original_copy(h, source, target, entry, expected_sha)
            target.write_bytes(b"x" * entry["size"])
            return record
        before = self.snapshot()
        with mock.patch.object(HDResources, "_copy_one", corrupt):
            with self.assertRaises(ToolError):
                self.remove()
        self.assertEqual(before, self.snapshot())

    def test_cancel_during_copy_keeps_original(self):
        before = self.snapshot()
        def stop(*args, **kwargs):
            self.m.cancel.set()
            raise Cancelled("stop")
        with mock.patch.object(HDResources, "_copy_one", stop):
            with self.assertRaises(Cancelled):
                self.remove()
        self.assertEqual(before, self.snapshot())

    def test_game_starting_during_backup_blocks_removal(self):
        original_copy = HDResources._copy_one
        def start_game(h, *args, **kwargs):
            result = original_copy(h, *args, **kwargs)
            self.m.guard = lambda root: ["Client-Win64-Shipping.exe"]
            return result
        before = self.snapshot()
        with mock.patch.object(HDResources, "_copy_one", start_game):
            with self.assertRaises(ToolError):
                self.remove()
        self.assertEqual(before, self.snapshot())

    def test_channel_change_during_backup_blocks_removal(self):
        original_copy = HDResources._copy_one
        def change_channel(h, *args, **kwargs):
            result = original_copy(h, *args, **kwargs)
            (self.root / "LocalGameResources.json").write_bytes(b"new channel")
            return result
        with mock.patch.object(HDResources, "_copy_one", change_channel):
            with self.assertRaises(ToolError):
                self.remove()
        self.assertTrue(self.h.hd.is_dir())

    def test_out_of_space_does_not_create_backup(self):
        with mock.patch("wuwa_uhd.hd_resources.shutil.disk_usage", return_value=mock.Mock(free=1)):
            with self.assertRaises(ToolError):
                self.remove()
        self.assertEqual(list(self.backups.iterdir()), [])
        self.assertTrue(self.h.hd.is_dir())

    def test_crash_before_remove_rename_resumes(self):
        real = os.rename
        def crash(src, dst):
            if Path(src) == self.h.hd:
                raise OSError("power loss before rename")
            return real(src, dst)
        with mock.patch("wuwa_uhd.hd_resources.os.rename", crash):
            with self.assertRaises(OSError):
                self.remove()
        self.assertTrue(self.h.hd.is_dir())
        self.assertEqual(self.h._state()["stage"], "removing")
        backup = self.remove()
        self.assertFalse(self.h.hd.exists())
        self.m.restore_hd(backup)
        self.assertTrue(self.h.hd.is_dir())

    def test_partial_delete_resumes_with_verified_backup(self):
        real = Path.unlink
        calls = []
        def crash(path, *args, **kwargs):
            if path.parent == self.h.removing:
                calls.append(path.name)
                if len(calls) == 2:
                    raise OSError("power loss during removal")
            return real(path, *args, **kwargs)
        with mock.patch.object(Path, "unlink", crash):
            with self.assertRaises(OSError):
                self.remove()
        self.assertFalse(self.h.hd.exists())
        self.assertTrue(self.h.removing.exists())
        backup = self.remove()
        self.assertFalse(self.h.removing.exists())
        self.m.restore_hd(backup)
        self.assertTrue(self.h.hd.exists())

    def test_missing_backup_blocks_resumed_delete(self):
        with mock.patch.object(Path, "unlink", side_effect=OSError("stop")):
            with self.assertRaises(OSError):
                self.remove()
        backup = Path(self.h._state()["backup_directory"])
        (backup / "HD" / next(iter(self.hd_files))).unlink()
        before = {p.name: p.read_bytes() for p in self.h.removing.iterdir()}
        with self.assertRaises(ToolError):
            self.remove()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.h.removing.iterdir()})

    def test_unknown_quarantine_file_is_not_deleted(self):
        with mock.patch.object(Path, "unlink", side_effect=OSError("stop")):
            with self.assertRaises(OSError):
                self.remove()
        foreign = self.h.removing / "keep.txt"
        foreign.write_bytes(b"keep")
        with self.assertRaises(ToolError):
            self.remove()
        self.assertEqual(foreign.read_bytes(), b"keep")

    def test_same_drive_backup_reports_no_net_space_saving(self):
        result = self.m.backup_remove_hd(self.backups)
        self.assertTrue(result["same_volume_backup"])
        self.assertEqual(result["freed_game_drive_bytes"], 0)

    def test_restore_refuses_existing_hd(self):
        backup = self.remove()
        self.h.hd.mkdir()
        keep = self.h.hd / "keep.txt"
        keep.write_bytes(b"keep")
        with self.assertRaises(ToolError):
            self.m.restore_hd(backup)
        self.assertEqual(keep.read_bytes(), b"keep")

    def test_restore_rejects_tampered_backup(self):
        backup = self.remove()
        path = backup / "HD" / next(iter(self.hd_files))
        path.write_bytes(b"x" * path.stat().st_size)
        with self.assertRaises(ToolError):
            self.m.restore_hd(backup)
        self.assertFalse(self.h.hd.exists())

    def test_restore_rejects_path_traversal_manifest(self):
        backup = self.remove()
        path = backup / MANIFEST
        data = json.loads(path.read_text("utf-8"))
        data["files"][0]["path"] = "../../outside.pak"
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ToolError):
            self.m.restore_hd(backup)
        self.assertFalse(self.h.hd.exists())

    def test_restore_accepts_previous_manual_backup_with_bom(self):
        backup = self.remove()
        path = backup / MANIFEST
        data = json.loads(path.read_text("utf-8"))
        del data["schema"]
        del data["version"]
        path.write_text(json.dumps(data), encoding="utf-8-sig")
        self.m.restore_hd(backup)
        self.assertTrue(self.h.hd.is_dir())

    def test_restore_interrupted_copy_can_continue(self):
        backup = self.remove()
        original_copy = HDResources._copy_one
        def interrupt(h, src, dst, entry, expected_sha=None):
            dst.write_bytes(b"partial")
            raise Cancelled("stop")
        with mock.patch.object(HDResources, "_copy_one", interrupt):
            with self.assertRaises(Cancelled):
                self.m.restore_hd(backup)
        self.assertFalse(self.h.hd.exists())
        self.assertEqual(self.h._state()["stage"], "restoring")
        self.m.restore_hd(backup)
        self.assertTrue(self.h.hd.is_dir())

    def test_restore_crash_after_commit_recovers(self):
        backup = self.remove()
        real = HDResources._save
        def crash(h, data, stage):
            if stage == "restored":
                raise OSError("power loss after rename")
            return real(h, data, stage)
        with mock.patch.object(HDResources, "_save", crash):
            with self.assertRaises(OSError):
                self.m.restore_hd(backup)
        self.assertTrue(self.h.hd.exists())
        self.m.restore_hd(backup)
        self.assertEqual(self.h._state()["stage"], "restored")

    def test_restoration_stops_on_client_version_change(self):
        backup = self.remove()
        (self.root / next(iter(self.core))).write_bytes(b"new version")
        with self.assertRaises(ToolError):
            self.m.restore_hd(backup)
        self.assertFalse(self.h.hd.exists())

    def test_hardlinked_hd_is_rejected(self):
        name = next(iter(self.hd_files))
        path = self.h.hd / name
        outside = Path(self.temp.name) / "outside.pak"
        path.rename(outside)
        os.link(outside, path)
        with self.assertRaises(ToolError):
            self.remove()
        self.assertEqual(outside.read_bytes(), self.hd_files[name])

    def test_hd_actions_use_existing_operation_lock(self):
        from .core import exclusive_lock
        with exclusive_lock(self.m.work / "operation.lock"):
            with self.assertRaises(ToolError):
                self.remove()
        self.assertTrue(self.h.hd.exists())
