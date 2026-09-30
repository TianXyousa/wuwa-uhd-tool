"""Offline acceptance tests. All game writes use newly created temporary fixtures."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from .core import (CORE_FILES, MANIFEST_MD5, MARKER, PACK_PATH, VERSION, Cancelled,
                   Manager, ToolError, atomic_json, bundled_manifest, exclusive_lock,
                   no_links, parse_manifest, validate_url)


class Response(io.BytesIO):
    def __init__(self, content, status=200, headers=None, fail_after=None):
        super().__init__(content)
        self.status = status
        self.headers = headers or {"Content-Length": str(len(content))}
        self.fail_after = fail_after

    def read(self, size=-1):
        if self.fail_after is not None:
            if self.tell() >= self.fail_after:
                raise OSError("simulated network interruption")
            size = min(size, self.fail_after - self.tell())
        return super().read(size)


class Transport:
    def __init__(self, files, digest):
        self.files = files
        self.digest = digest
        self.calls = []
        self.ignore_range = False
        self.bad_range = False
        self.bad_length = False
        self.corrupt = False
        self.version = VERSION
        self.interrupt_once = False
        self.interrupted = False

    def metadata(self, url):
        return json.dumps({"resourcePacks": {"uhd": {"version": self.version,
                           "indexFileMd5": self.digest, "indexFile": PACK_PATH + "uhd/indexFile.json",
                           "baseUrl": PACK_PATH + "zip/", "size": sum(map(len, self.files.values()))}},
                           "bundles": {"UHD": {"resourcePacks": ["common", "uhd"]}}}).encode()

    def open(self, url, offset=0, metadata=False):
        name = url.rsplit("/", 1)[-1]
        self.calls.append((name, offset))
        full = self.files[name]
        if self.corrupt:
            full = bytes(b ^ 1 for b in full)
        if self.ignore_range:
            offset = 0
        data = full[offset:]
        headers = {"Content-Length": str(len(data) + (1 if self.bad_length else 0))}
        if offset:
            headers["Content-Range"] = f"bytes {offset + (1 if self.bad_range else 0)}-{len(full)-1}/{len(full)}"
        fail_after = None
        if self.interrupt_once and not self.interrupted:
            self.interrupted = True
            fail_after = min(8192, len(data) // 2)
        return Response(data, 206 if offset else 200, headers, fail_after)


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wuwa-tool-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "测试 WeGame 游戏(2002137)"
        (self.root / "Client/Content/HD").mkdir(parents=True)
        (self.root / "Client/Content/HD/keep.pak").write_bytes(b"original HD must stay intact")
        self.core = {}
        for n, rel in enumerate(CORE_FILES):
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            data = ("original-core-" + str(n)).encode() * 200
            p.write_bytes(data)
            self.core[rel] = (len(data), hashlib.md5(data).hexdigest())
        channel = self.root / "Client/Binaries/Win64/ThirdParty/KrPcSdk_Mainland/KRSDKRes/KRSDKConfig.json"
        channel.parent.mkdir(parents=True, exist_ok=True)
        channel.write_text('{"KR_ChannelId":"167"}', encoding="utf-8")
        channel.parent.parent.joinpath("KRSDKEx.dll").write_bytes(b"original WeGame sdk")
        (self.root / "LocalGameResources.json").write_bytes(b'{"original":true}')
        self.files = {"pakchunk1-UHD-WindowsNoEditor.pak": b"abcd" * 65536,
                      "pakchunk1-UHD-WindowsNoEditor.sig": b"official-signature" * 40,
                      "pakchunk2-UHD-WindowsNoEditor.pak": b"another-pak" * 1024}
        self.raw = json.dumps({"resource": [{"dest": "Client/Content/UHD/" + name,
                               "md5": hashlib.md5(data).hexdigest(), "size": len(data)}
                              for name, data in self.files.items()]}).encode()
        self.digest = hashlib.md5(self.raw).hexdigest()
        self.transport = Transport(self.files, self.digest)
        self.events = []
        self.m = self.make_manager()
        self.original = self.snapshot()

    def make_manager(self, **kwargs):
        return Manager(self.root, manifest=self.raw, expected_md5=self.digest,
                       core_files=self.core, transport=self.transport, guard=lambda root: [],
                       event=self.events.append, **kwargs)

    def snapshot(self):
        return {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob("*") if p.is_file()}

    def begin_partial(self, name=None, count=4096):
        name = name or next(iter(self.files))
        self.m.work.mkdir()
        self.m.parts.mkdir()
        self.m.cache.mkdir()
        state = {"schema": 1, "owner": "a" * 32, "game_root": str(self.root),
                 "version": VERSION, "manifest_md5": self.digest}
        self.m.save_state(state, "downloading")
        (self.m.parts / (name + ".part")).write_bytes(self.files[name][:count])
        return name

    def test_bundled_official_manifest(self):
        entries = parse_manifest(bundled_manifest())
        self.assertEqual(len(entries), 100)
        self.assertEqual(sum(e["size"] for e in entries), 66036274508)

    def test_inspect_is_read_only(self):
        result = self.m.inspect()
        self.assertTrue(result["online_verified"])
        self.assertEqual(self.original, self.snapshot())
        self.assertFalse(self.m.work.exists())
        self.assertEqual(self.transport.calls, [])

    def test_full_apply_rollback_restore_and_clear(self):
        self.m.apply()
        state = self.m.read_state()
        self.assertEqual(state["status"], "active")
        for name, data in self.files.items():
            self.assertEqual((self.m.target / name).read_bytes(), data)
        for path, digest in self.original.items():
            self.assertEqual(hashlib.sha256((self.root / path).read_bytes()).hexdigest(), digest)
        self.m.rollback()
        self.assertEqual(self.original, self.snapshot())
        self.assertEqual(self.m.read_state()["status"], "parked")
        count = len(self.transport.calls)
        self.m.apply()
        self.assertEqual(len(self.transport.calls), count, "restore must reuse verified cache")
        self.m.rollback()
        self.m.clear_cache()
        self.assertFalse(self.m.cache.exists())
        self.assertFalse(self.m.parts.exists())
        self.assertEqual(self.original, self.snapshot())

    def test_resume_partial(self):
        name = self.begin_partial()
        self.m.apply()
        self.assertIn((name, 4096), self.transport.calls)
        self.assertEqual((self.m.target / name).read_bytes(), self.files[name])

    def test_server_ignores_range_restarts_safely(self):
        name = self.begin_partial()
        self.transport.ignore_range = True
        self.m.apply()
        self.assertEqual((self.m.target / name).read_bytes(), self.files[name])

    def test_bad_range_does_not_install(self):
        self.begin_partial()
        self.transport.bad_range = True
        with self.assertRaises(ToolError):
            self.m.apply()
        self.assertEqual(self.original, self.snapshot())

    def test_incorrect_length_does_not_install(self):
        self.transport.bad_length = True
        with self.assertRaises(ToolError):
            self.m.apply()
        self.assertEqual(self.original, self.snapshot())

    def test_corrupt_payload_never_installed(self):
        self.transport.corrupt = True
        with self.assertRaises(ToolError):
            self.m.apply()
        self.assertEqual(self.original, self.snapshot())
        self.assertFalse(self.m.target.exists())

    def test_interrupted_download_resumes(self):
        self.transport.interrupt_once = True
        self.m.apply()
        self.assertTrue(any(offset > 0 for _, offset in self.transport.calls))

    def test_cancel_keeps_original(self):
        self.m.cancel.set()
        with self.assertRaises(Cancelled):
            self.m.apply()
        self.assertEqual(self.original, self.snapshot())

    def test_version_mismatch_stops_before_writes(self):
        self.transport.version = "3.8.0"
        with self.assertRaises(ToolError):
            self.m.apply()
        self.assertFalse(self.m.work.exists())

    def test_official_version_changes_during_download_stops_commit(self):
        real_open = self.transport.open
        def changing(*args, **kwargs):
            self.transport.version = "3.8.0"
            return real_open(*args, **kwargs)
        self.transport.open = changing
        with self.assertRaises(ToolError):
            self.m.apply()
        self.assertEqual(self.original, self.snapshot())
        self.assertFalse(self.m.target.exists())

    def test_corrupt_partial_restarts_from_zero(self):
        name = self.begin_partial()
        (self.m.parts / (name + ".part")).write_bytes(b"bad!" * 1024)
        self.m.apply()
        self.assertEqual((self.m.target / name).read_bytes(), self.files[name])
        self.assertIn((name, 0), self.transport.calls)

    def test_rollback_conflict_does_not_overwrite_cache(self):
        self.m.apply()
        self.m.cache.mkdir()
        keep = self.m.cache / "unrelated.txt"
        keep.write_bytes(b"keep")
        before = self.snapshot()
        with self.assertRaises(ToolError):
            self.m.rollback()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(keep.read_bytes(), b"keep")

    def test_directory_junction_is_rejected(self):
        if os.name != "nt":
            self.skipTest("Windows junction test")
        import subprocess
        outside = Path(self.temp.name) / "unrelated-folder"
        outside.mkdir()
        keep = outside / "keep.txt"
        keep.write_bytes(b"keep")
        # Native PowerShell creates and removes the exact verified temporary test junction.
        command = f"New-Item -ItemType Junction -Path '{self.m.target}' -Target '{outside}' | Out-Null"
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command], check=True,
                       capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            with self.assertRaises(ToolError):
                self.m.apply()
            self.assertEqual(keep.read_bytes(), b"keep")
        finally:
            os.rmdir(self.m.target)

    def test_modified_core_stops_before_writes(self):
        (self.root / next(iter(self.core))).write_bytes(b"wrong")
        with self.assertRaises(ToolError):
            self.m.apply()
        self.assertFalse(self.m.work.exists())

    def test_game_running_blocks_mutation(self):
        self.m.guard = lambda root: ["Client-Win64-Shipping.exe"]
        with self.assertRaises(ToolError):
            self.m.apply()
        self.assertFalse(self.m.work.exists())

    def test_channel_changed_during_download_stops_commit(self):
        real_open = self.transport.open
        changed = threading.Event()
        def changing(*args, **kwargs):
            if not changed.is_set():
                changed.set()
                (self.root / "LocalGameResources.json").write_bytes(b"other updater changed this")
            return real_open(*args, **kwargs)
        self.transport.open = changing
        with self.assertRaises(ToolError):
            self.m.apply()
        self.assertFalse(self.m.target.exists())

    def test_existing_unmanaged_uhd_is_untouched(self):
        self.m.target.mkdir()
        keep = self.m.target / "existing.pak"
        keep.write_bytes(b"do not touch")
        with self.assertRaises(ToolError):
            self.m.apply()
        self.assertEqual(keep.read_bytes(), b"do not touch")
        self.assertFalse(self.m.work.exists())

    def test_foreign_workspace_is_untouched(self):
        self.m.work.mkdir()
        keep = self.m.work / "someone-else.txt"
        keep.write_bytes(b"keep")
        with self.assertRaises(ToolError):
            self.m.apply()
        self.assertEqual(keep.read_bytes(), b"keep")

    def test_foreign_marker_blocks_rollback(self):
        self.m.apply()
        marker = self.m.target / MARKER
        marker.write_text('{}', encoding="utf-8")
        before = self.snapshot()
        with self.assertRaises(ToolError):
            self.m.rollback()
        self.assertEqual(before, self.snapshot())

    def test_rollback_does_not_require_network_or_old_core(self):
        self.m.apply()
        (self.root / next(iter(self.core))).write_bytes(b"a future game update")
        self.transport.metadata = lambda url: (_ for _ in ()).throw(OSError("offline"))
        self.m.rollback()
        self.assertFalse(self.m.target.exists())

    def test_crash_after_install_rename_recovers(self):
        self.m.apply()
        state = self.m.read_state()
        self.m.save_state(state, "installing")
        self.m.rollback()
        self.assertEqual(self.original, self.snapshot())

    def test_crash_before_install_rename_recovers(self):
        self.m.apply()
        state = self.m.read_state()
        os.rename(self.m.target, self.m.cache)
        self.m.save_state(state, "installing")
        self.m.apply()
        self.assertEqual(self.m.read_state()["status"], "active")

    def test_crash_after_rollback_rename_recovers(self):
        self.m.apply()
        state = self.m.read_state()
        os.rename(self.m.target, self.m.cache)
        self.m.save_state(state, "rolling_back")
        self.m.rollback()
        self.assertEqual(self.m.read_state()["status"], "parked")
        self.assertEqual(self.original, self.snapshot())

    def test_duplicate_instance_lock(self):
        self.m.work.mkdir()
        with exclusive_lock(self.m.work / "operation.lock"):
            with self.assertRaises(ToolError):
                with exclusive_lock(self.m.work / "operation.lock"):
                    self.fail("lock acquired twice")

    def test_space_check_prevents_download(self):
        from collections import namedtuple
        usage = namedtuple("Usage", "total used free")(1000, 999, 1)
        with mock.patch("wuwa_uhd.core.shutil.disk_usage", return_value=usage):
            with self.assertRaises(ToolError):
                self.m.apply()
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.original, self.snapshot())

    def test_unknown_cache_file_prevents_clear(self):
        self.m.apply()
        self.m.rollback()
        foreign = self.m.cache / "unrelated.txt"
        foreign.write_bytes(b"keep")
        with self.assertRaises(ToolError):
            self.m.clear_cache()
        self.assertEqual(foreign.read_bytes(), b"keep")

    def test_active_resources_cannot_be_cleared(self):
        self.m.apply()
        before = self.snapshot()
        with self.assertRaises(ToolError):
            self.m.clear_cache()
        self.assertEqual(before, self.snapshot())

    def test_hardlink_partial_is_rejected(self):
        name = self.begin_partial()
        part = self.m.parts / (name + ".part")
        part.unlink()
        outside = Path(self.temp.name) / "unrelated-original.dat"
        outside.write_bytes(b"keep this original")
        os.link(outside, part)
        with self.assertRaises(ToolError):
            self.m.apply()
        self.assertEqual(outside.read_bytes(), b"keep this original")

    def test_manifest_path_traversal_rejected(self):
        for name in ("Client/Content/UHD/../../save.pak", "C:/outside.pak",
                     "Client/Content/UHD/a.pak:ads", "Client/Content/UHD/a\\b.pak"):
            raw = json.dumps({"resource": [{"dest": name, "size": 1, "md5": "0" * 32}]}).encode()
            with self.assertRaises(ToolError):
                parse_manifest(raw, hashlib.md5(raw).hexdigest())

    def test_case_duplicate_manifest_rejected(self):
        raw = json.dumps({"resource": [{"dest": "Client/Content/UHD/" + name, "size": 1, "md5": "0" * 32}
                                       for name in ["test.pak", "TEST.pak"]]}).encode()
        with self.assertRaises(ToolError):
            parse_manifest(raw, hashlib.md5(raw).hexdigest())

    def test_bad_manifest_hash_rejected(self):
        with self.assertRaises(ToolError):
            parse_manifest(self.raw, "0" * 32)

    def test_untrusted_urls_rejected(self):
        for url in ("http://pcdownload-huoshan.aki-game.com/a", "https://evil.example/a",
                    "https://pcdownload-huoshan.aki-game.com.evil.example/a",
                    "https://user@pcdownload-huoshan.aki-game.com/a"):
            with self.assertRaises(ToolError):
                validate_url(url)

    def test_no_automatic_launch_during_apply_or_rollback(self):
        with mock.patch("subprocess.Popen") as popen:
            self.m.apply()
            self.m.rollback()
            popen.assert_not_called()

    def test_wegame_instructions_do_not_launch_or_modify_game(self):
        from .gui import App
        app = App.__new__(App)
        app.root = object()
        app.start = mock.Mock()
        before = self.snapshot()
        with mock.patch("wuwa_uhd.gui.messagebox.showinfo") as showinfo, mock.patch("subprocess.Popen") as popen:
            app.show_wegame_instructions()
            showinfo.assert_called_once()
            self.assertIn("-krqlv=uhd", showinfo.call_args.args[1])
            self.assertIn("WeGame", showinfo.call_args.args[1])
            popen.assert_not_called()
        app.start.assert_not_called()
        self.assertEqual(before, self.snapshot())


def run_tests():
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(AcceptanceTests)
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    return {"ok": result.wasSuccessful(), "tests_run": result.testsRun,
            "failures": len(result.failures), "errors": len(result.errors),
            "real_game_modified": False, "real_game_started": False,
            "network_used": False, "details": stream.getvalue()}
