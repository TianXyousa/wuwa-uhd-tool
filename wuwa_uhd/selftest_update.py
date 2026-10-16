"""3.7.1 compatibility regressions; only disposable game fixtures are written."""
import hashlib
import json
from unittest import mock

from .core import (CLIENT_PROFILES, MANIFEST_MD5, MARKER, ONLINE_MANIFEST_MD5,
                   RESOURCE_VERSION, VERSION, ToolError, atomic_json)
from .selftest import GameFixture


class UpdateTests(GameFixture):
    def profiles(self):
        updated = dict(self.core)
        for rel in (next(iter(self.core)), "Wuthering Waves.exe"):
            content = (self.root / rel).read_bytes()
            content = bytes(x ^ 1 for x in content)
            updated[rel] = (len(content), hashlib.md5(content).hexdigest())
        self.m.core_profiles = {"3.7.0": self.core, VERSION: updated}
        return updated

    def channel_profile(self):
        self.profiles()
        channel = dict(self.core)
        for rel in (next(iter(self.core)), "Wuthering Waves.exe"):
            content = bytes(x ^ 2 for x in (self.root / rel).read_bytes())
            channel[rel] = (len(content), hashlib.md5(content).hexdigest())
        self.m.core_profiles["3.7.1 (WeGame)"] = channel
        return channel

    def use_channel_client(self):
        for rel in (next(iter(self.core)), "Wuthering Waves.exe"):
            path = self.root / rel
            path.write_bytes(bytes(x ^ 2 for x in path.read_bytes()))

    def update_fixture_client(self):
        for rel in (next(iter(self.core)), "Wuthering Waves.exe"):
            path = self.root / rel
            path.write_bytes(bytes(x ^ 1 for x in path.read_bytes()))

    def test_complete_client_profiles_are_accepted_and_reported(self):
        self.profiles()
        self.assertEqual(self.m.inspect()["version"], "3.7.0")
        self.update_fixture_client()
        result = self.m.inspect()
        self.assertEqual(result["version"], VERSION)
        self.assertEqual(result["official_version"], VERSION)
        self.assertEqual(result["resource_version"], RESOURCE_VERSION)
        self.assertFalse(self.m.work.exists())

    def test_mixed_core_versions_are_rejected(self):
        self.profiles()
        path = self.root / next(iter(self.core))
        path.write_bytes(bytes(x ^ 1 for x in path.read_bytes()))
        with self.assertRaisesRegex(ToolError, "混用"):
            self.m.apply()
        self.assertFalse(self.m.work.exists())
        self.assertEqual(self.transport.calls, [])

    def test_profiles_pin_same_complete_paths(self):
        self.assertEqual(set(CLIENT_PROFILES), {"3.7.0", "3.7.1", "3.7.1 (WeGame)"})
        self.assertEqual(set(CLIENT_PROFILES["3.7.0"]), set(CLIENT_PROFILES[VERSION]))
        self.assertEqual(set(CLIENT_PROFILES["3.7.1 (WeGame)"]), set(CLIENT_PROFILES[VERSION]))
        self.assertEqual(RESOURCE_VERSION, "3.7.0")
        self.assertNotEqual(MANIFEST_MD5, ONLINE_MANIFEST_MD5)

    def test_wegame_channel_profile_is_accepted_without_writes(self):
        self.channel_profile()
        self.use_channel_client()
        before = self.snapshot()
        self.assertEqual(self.m.inspect(False)["version"], "3.7.1 (WeGame)")
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.m.work.exists())

    def test_mixed_official_and_wegame_cores_are_rejected(self):
        self.channel_profile()
        first = self.root / next(iter(self.core))
        first.write_bytes(bytes(x ^ 1 for x in first.read_bytes()))
        launcher = self.root / "Wuthering Waves.exe"
        launcher.write_bytes(bytes(x ^ 2 for x in launcher.read_bytes()))
        with self.assertRaisesRegex(ToolError, "混用"):
            self.m.apply()
        self.assertFalse(self.m.work.exists())

    def test_wegame_apply_keeps_original_hd(self):
        self.channel_profile()
        self.use_channel_client()
        before = {name: (self.root / "Client/Content/HD" / name).read_bytes() for name in self.hd_files}
        self.m.apply()
        after = {name: (self.root / "Client/Content/HD" / name).read_bytes() for name in self.hd_files}
        self.assertEqual(before, after)
        self.assertEqual(self.m.hd_resources().status()["hd_operation"], "none")

    def test_remove_hd_warning_cancel_preserves_hd(self):
        from .gui import App, WEGAME_HD_WARNING
        app = App.__new__(App)
        app.root, app.start, app.busy = object(), mock.Mock(), False
        with mock.patch("wuwa_uhd.gui.messagebox.askokcancel", return_value=False) as confirm, \
             mock.patch("wuwa_uhd.gui.filedialog.askdirectory") as choose:
            app.confirm_remove_hd()
            self.assertIn(WEGAME_HD_WARNING, confirm.call_args.args[1])
            self.assertEqual(confirm.call_args.kwargs["default"], "cancel")
            choose.assert_not_called()
        app.start.assert_not_called()
        self.assertEqual(self.original, self.snapshot())

    def test_remove_hd_remains_available_after_explicit_confirmation(self):
        from .gui import App
        app = App.__new__(App)
        app.root, app.start, app.busy = object(), mock.Mock(), False
        with mock.patch("wuwa_uhd.gui.messagebox.askokcancel", return_value=True), \
             mock.patch("wuwa_uhd.gui.filedialog.askdirectory", return_value="selected-backup"):
            app.confirm_remove_hd()
        app.start.assert_called_once_with("backup_remove_hd", "selected-backup")

    def test_first_backup_delete_dialog_explains_redownload_and_scope(self):
        from .gui import HDBackupDeleteDialog, WEGAME_HD_WARNING
        dialog = HDBackupDeleteDialog.__new__(HDBackupDeleteDialog)
        dialog.plan = {"backup_directory": "selected-backup", "file_count": 100, "bytes": 45713445823}
        with mock.patch("wuwa_uhd.gui.tk.BooleanVar"), \
             mock.patch("wuwa_uhd.gui.tk.Label") as label, \
             mock.patch("wuwa_uhd.gui.tk.Checkbutton"):
            dialog.body(mock.Mock())
        texts = "\n".join(call.kwargs.get("text", "") for call in label.call_args_list)
        self.assertIn(WEGAME_HD_WARNING, texts)
        self.assertIn("删除外部备份本身不会移除游戏内的 HD", texts)

    def test_online_manifest_identity_is_separate_from_ownership(self):
        self.m.online_manifest_md5 = ONLINE_MANIFEST_MD5
        self.transport.digest = ONLINE_MANIFEST_MD5
        before = self.snapshot()
        self.m.online_check()
        self.assertEqual(self.m.manifest_md5, self.digest)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.m.work.exists())

    def test_stale_or_unknown_online_manifest_is_rejected_before_writes(self):
        self.m.online_manifest_md5 = ONLINE_MANIFEST_MD5
        for digest in (MANIFEST_MD5, "0" * 32):
            self.transport.digest = digest
            with self.assertRaisesRegex(ToolError, "官方资源版本已变化"):
                self.m.apply()
            self.assertFalse(self.m.work.exists())
        self.assertEqual(self.original, self.snapshot())

    def test_legacy_active_install_inspection_does_not_rewrite_records(self):
        self.m.apply()
        paths = (self.m.state_path, self.m.target / MARKER)
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
        self.assertEqual(json.loads(self.m.state_path.read_bytes())["version"], "3.7.0")
        self.profiles()
        self.update_fixture_client()
        self.assertEqual(self.m.inspect()["status"], "active")
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths})
        with self.assertRaisesRegex(ToolError, "已启用"):
            self.m.apply()

    def test_legacy_partial_download_resumes_with_updated_client(self):
        name = self.begin_partial()
        self.profiles()
        self.update_fixture_client()
        self.m.apply()
        self.assertIn((name, 4096), self.transport.calls)
        self.assertEqual(self.m.read_state()["version"], RESOURCE_VERSION)

    def test_legacy_rollback_and_cache_reuse_after_client_update(self):
        self.m.apply()
        self.profiles()
        self.update_fixture_client()
        calls = list(self.transport.calls)
        self.m.rollback()
        self.m.apply()
        self.assertEqual(calls, self.transport.calls)
        self.assertEqual(self.m.read_state()["status"], "active")

    def test_legacy_hd_backup_restores_after_client_update(self):
        self.m.apply()
        parent = self.root.parent / "backups"
        parent.mkdir()
        from pathlib import Path
        backup = Path(self.m.backup_remove_hd(parent)["backup_directory"])
        manifest = backup / "manifest-sha256.json"
        before = manifest.read_bytes()
        self.assertEqual(json.loads(before)["version"], "3.7.0")
        self.profiles()
        self.update_fixture_client()
        self.assertEqual(self.m.inspect_hd_backup(backup, True)["status"], "verified")
        self.m.restore_hd(backup)
        self.m.rollback()
        self.assertEqual(before, manifest.read_bytes())
        for name, content in self.hd_files.items():
            self.assertEqual((self.root / "Client/Content/HD" / name).read_bytes(), content)

    def test_unrelated_resource_state_version_is_rejected(self):
        self.begin_partial()
        state = self.m.read_state()
        state["version"] = "3.8.0"
        atomic_json(self.m.state_path, state)
        with self.assertRaises(ToolError):
            self.m.inspect()

    def test_unknown_server_path_is_rejected_even_with_same_hash(self):
        original = self.transport.metadata
        def metadata(url):
            data = json.loads(original(url))
            data["resourcePacks"]["uhd"]["baseUrl"] = "launcher/unverified/"
            return json.dumps(data).encode()
        with mock.patch.object(self.transport, "metadata", side_effect=metadata):
            with self.assertRaisesRegex(ToolError, "官方资源版本已变化"):
                self.m.apply()
        self.assertFalse(self.m.work.exists())
