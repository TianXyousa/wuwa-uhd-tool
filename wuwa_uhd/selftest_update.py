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
        self.assertEqual(set(CLIENT_PROFILES), {"3.7.0", "3.7.1"})
        self.assertEqual(set(CLIENT_PROFILES["3.7.0"]), set(CLIENT_PROFILES[VERSION]))
        self.assertEqual(RESOURCE_VERSION, "3.7.0")
        self.assertNotEqual(MANIFEST_MD5, ONLINE_MANIFEST_MD5)

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
