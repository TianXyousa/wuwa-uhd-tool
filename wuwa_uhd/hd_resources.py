"""Verified HD backup/removal and restore; these operations retain external backups."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import time
import uuid

from .core import (MARKER, VERSION, ToolError, atomic_json, canonical, check_cancel,
                   exclusive_lock, hash_file, load_json, no_links)

MANIFEST = "manifest-sha256.json"
DELETION_RECEIPT = ".wuwa-hd-backup-deletion.json"
PENDING = {"removing", "restoring"}


def signature(path):
    st = no_links(path).stat()
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


class HDResources:
    def __init__(self, manager):
        self.m = manager
        self.hd = manager.root / "Client/Content/HD"
        self.journal = manager.work / "hd-operation.json"
        self.removing = manager.work / "HD-removing"
        self.restoring = manager.work / "HD-restoring"
        self.entries = manager.hd_entries
        self.total = sum(e["size"] for e in self.entries)

    def _external(self, path):
        path = no_links(path)
        if str(path).startswith("\\\\"):
            raise ToolError("HD 备份暂不支持网络共享，请选择本地磁盘。")
        for protected in (self.m.root, self.m.work):
            if path.is_relative_to(protected) or protected.is_relative_to(path):
                raise ToolError("备份批次目录必须位于游戏目录和工具缓存之外。")
        return path

    def _state(self):
        no_links(self.journal)
        if not self.journal.exists():
            return None
        data = load_json(self.journal)
        owner = self.m.read_state()
        if (not isinstance(data, dict) or not owner or data.get("schema") != 1
                or data.get("owner") != owner["owner"]
                or data.get("game_root") != str(self.m.root)
                or data.get("stage") not in PENDING | {"removed", "restored"}
                or not isinstance(data.get("backup_directory"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", str(data.get("manifest_sha256", "")))):
            raise ToolError("HD 操作记录无效，请保留备份和缓存。")
        self._external(data["backup_directory"])
        return data

    def _save(self, data, stage):
        data = dict(data, stage=stage, updated_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        atomic_json(self.journal, data)
        return data

    def status(self):
        data = self._state()
        usable = None
        if data:
            backup = self._external(data["backup_directory"])
            usable = (no_links(backup / "HD").is_dir() and no_links(backup / MANIFEST).is_file()
                      and not no_links(backup / DELETION_RECEIPT).exists())
        return {"hd_present": self.hd.is_dir(),
                "hd_operation": data["stage"] if data else "none",
                "hd_backup_directory": data["backup_directory"] if data else None,
                "hd_backup_usable": usable}

    def require_no_pending(self):
        data = self._state()
        if data and data["stage"] in PENDING:
            raise ToolError("存在未完成的 HD 操作，请先再次执行“备份并移除 HD”或“恢复 HD 备份”。")
        if self.removing.exists() or self.restoring.exists():
            raise ToolError("发现未处理的 HD 暂存目录，请保留备份和缓存并联系维护者。")

    def _inventory(self, directory, entries, *, subset=False, extras=()):
        no_links(directory)
        if not directory.is_dir():
            raise ToolError(f"缺少资源目录：{directory}")
        expected = {e["name"]: e for e in entries}
        found = {}
        for path in directory.iterdir():
            no_links(path)
            if not path.is_file() or path.name not in expected.keys() | set(extras):
                raise ToolError(f"资源目录存在未知文件，已停止：{path}")
            if path.name in expected:
                if path.stat().st_size != expected[path.name]["size"]:
                    raise ToolError(f"资源大小不匹配：{path.name}")
                found[path.name] = signature(path)
        if not subset and found.keys() != expected.keys():
            raise ToolError(f"资源文件不完整：{directory}")
        return found

    def _hashes(self, path):
        before = signature(path)
        md5, sha = hashlib.md5(), hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(4 * 1024 * 1024):
                check_cancel(self.m.cancel)
                md5.update(block)
                sha.update(block)
        if signature(path) != before:
            raise ToolError(f"校验期间文件发生变化：{path.name}")
        return md5.hexdigest(), sha.hexdigest()

    def _progress(self, phase, count, total, name):
        self.m.event({"kind": "task_progress", "phase": phase, "current": count,
                      "total": total, "file": name})

    def _verify(self, directory, entries, *, records=None, subset=False, extras=(), phase="校验 HD"):
        before = self._inventory(directory, entries, subset=subset, extras=extras)
        selected = [e for e in entries if e["name"] in before]
        for i, entry in enumerate(selected, 1):
            md5, sha = self._hashes(directory / entry["name"])
            if md5 != entry["md5"] or (records is not None and sha != records[entry["name"]]["sha256"]):
                raise ToolError(f"资源校验失败：{entry['name']}；原有备份保留。")
            self._progress(phase, i, len(selected), entry["name"])
        if self._inventory(directory, entries, subset=subset, extras=extras) != before:
            raise ToolError("校验期间资源目录发生变化，已停止。")
        return before

    def verify_uhd(self, hashes=True):
        state = self.m.read_state()
        if not state or state["status"] != "active":
            raise ToolError("请先完成 UHD 安装，再备份移除 HD。")
        self.m.owned(self.m.target, state)
        if hashes:
            return self._verify(self.m.target, self.m.entries, extras=(MARKER,), phase="完整校验 UHD")
        return self._inventory(self.m.target, self.m.entries, extras=(MARKER,))

    def verify_hd_for_rollback(self):
        if not self.hd.is_dir():
            raise ToolError("HD 基础资源已移除。请先“恢复 HD 备份”，再回退 UHD；本次未移动 UHD。")
        self._verify(self.hd, self.entries, phase="校验回退所需的 HD")

    def _copy_one(self, source, target, entry, expected_sha=None):
        before = signature(source)
        no_links(target)
        md5, sha = hashlib.md5(), hashlib.sha256()
        with source.open("rb") as src, target.open("xb") as dst:
            while block := src.read(4 * 1024 * 1024):
                check_cancel(self.m.cancel)
                dst.write(block)
                md5.update(block)
                sha.update(block)
            dst.flush()
            os.fsync(dst.fileno())
        if (signature(source) != before or target.stat().st_size != entry["size"]
                or md5.hexdigest() != entry["md5"]
                or (expected_sha is not None and sha.hexdigest() != expected_sha)):
            raise ToolError(f"复制时源文件变化或校验失败：{entry['name']}；未删除原件。")
        copied_md5, copied_sha = self._hashes(target)
        if copied_md5 != entry["md5"] or copied_sha != sha.hexdigest():
            raise ToolError(f"备份写入校验失败：{entry['name']}；未删除原件。")
        return {"path": entry["name"], "size": entry["size"], "sha256": copied_sha}

    def _read_backup_manifest(self, directory, expected_manifest=None):
        directory = self._external(directory)
        path = no_links(directory / MANIFEST)
        before = signature(path)
        data = load_json(path)
        expected = {e["name"]: e for e in self.entries}
        if (not isinstance(data, dict) or data.get("algorithm") != "SHA256"
                or data.get("source_backup_hashes_matched") is not True
                or data.get("file_count") != len(expected) or data.get("total_bytes") != self.total
                or data.get("version", VERSION) != VERSION
                or not isinstance(data.get("source"), str)
                or canonical(data["source"]) != self.hd or not isinstance(data.get("files"), list)):
            raise ToolError("备份清单与当前游戏或 HD 版本不匹配。请选择本工具生成的备份批次目录。")
        records = {}
        for record in data["files"]:
            if (not isinstance(record, dict) or record.get("path") not in expected
                    or record["path"] in records or record.get("size") != expected[record["path"]]["size"]
                    or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256", "")))):
                raise ToolError("备份清单含未知、重复或不安全的文件条目。")
            records[record["path"]] = record
        if records.keys() != expected.keys():
            raise ToolError("备份清单不完整。")
        digest = hash_file(path, "sha256", self.m.cancel)
        if signature(path) != before:
            raise ToolError("读取期间备份清单发生变化，已停止。")
        if expected_manifest is not None and digest != expected_manifest:
            raise ToolError("操作中断后备份清单发生变化，拒绝继续。")
        return records, digest

    def _load_backup(self, directory, expected_manifest=None):
        directory = self._external(directory)
        if no_links(directory / DELETION_RECEIPT).exists():
            raise ToolError("这份 HD 备份已进入永久删除流程，无法用于恢复。请使用其他完整备份。")
        records, digest = self._read_backup_manifest(directory, expected_manifest)
        self._verify(directory / "HD", self.entries, records=records, phase="核对备份 SHA256")
        return records, digest

    def _lock_state(self):
        no_links(self.m.work)
        state = self.m.read_state()
        if not self.m.work.is_dir() or not state:
            raise ToolError("缺少本工具的 UHD 安装记录，无法自动处理 HD；请保留备份。")
        return state

    def _record(self, owner, directory, digest):
        return {"schema": 1, "owner": owner["owner"], "game_root": str(self.m.root),
                "backup_directory": str(directory), "manifest_sha256": digest}

    def _check_space(self, directory, size):
        if shutil.disk_usage(directory).free < size + 2 * 2**30:
            raise ToolError(f"空间不足：目标磁盘需要约 {size/2**30:.2f} GiB，并预留 2 GiB。")

    def backup_remove(self, backup_parent):
        self.m.check_root()
        self.m.idle()
        owner = self._lock_state()
        with exclusive_lock(self.m.work / "operation.lock"):
            pending = self._state()
            if pending and pending["stage"] == "removing":
                self.m.tell("继续上次已验证的 HD 移除操作：" + pending["backup_directory"])
                return self._finish_removal(pending)
            self.require_no_pending()
            uhd_before = self.verify_uhd()
            before = self._inventory(self.hd, self.entries)
            protected = self.m.preserved()
            parent = no_links(backup_parent)
            if not parent.is_dir():
                raise ToolError("请选择已存在的备份磁盘或文件夹。")
            directory = self._external(parent / ("Wuwa-HD-" + time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]))
            self._check_space(parent, self.total)
            directory.mkdir()
            destination = directory / "HD"
            destination.mkdir()
            self.m.tell("HD 备份位置：" + str(directory))
            self.m.tell("校验全部通过前保留原 HD；中断复制留下的批次可手动保留或清理。")
            records = []
            for i, entry in enumerate(self.entries, 1):
                records.append(self._copy_one(self.hd / entry["name"], destination / entry["name"], entry))
                self._progress("备份并核对 SHA256", i, len(self.entries), entry["name"])
            record_map = {r["path"]: r for r in records}
            self._verify(self.hd, self.entries, records=record_map, phase="移除前复核原 HD")
            self._verify(destination, self.entries, records=record_map, phase="移除前复核备份")
            if before != self._inventory(self.hd, self.entries) or uhd_before != self.verify_uhd(hashes=False):
                raise ToolError("备份期间 HD 或 UHD 资源发生变化；备份保留，未移除 HD。")
            self.m.verify_preserved(protected)
            self.m.idle()
            manifest = {"schema": 1, "version": VERSION, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "source": str(self.hd), "backup": str(destination), "file_count": len(records),
                        "total_bytes": self.total, "algorithm": "SHA256",
                        "source_backup_hashes_matched": True, "files": records}
            atomic_json(directory / MANIFEST, manifest)
            digest = hash_file(directory / MANIFEST, "sha256", self.m.cancel)
            data = self._save(self._record(owner, directory, digest), "removing")
            return self._finish_removal(data)

    def _finish_removal(self, data):
        self.m.check_root()
        self.m.idle()
        uhd_before = self.verify_uhd()
        protected = self.m.preserved()
        directory = self._external(data["backup_directory"])
        records, _ = self._load_backup(directory, data["manifest_sha256"])
        for path in (self.hd, self.removing, self.restoring):
            no_links(path)
        if self.restoring.exists() or (self.hd.exists() and self.removing.exists()):
            raise ToolError("HD 移除暂存目录冲突，请保留全部文件。")
        if self.hd.exists():
            before = self._verify(self.hd, self.entries, records=records, phase="移除前校验 HD")
            self.m.idle()
            self.m.verify_preserved(protected)
            if before != self._inventory(self.hd, self.entries) or uhd_before != self.verify_uhd(hashes=False):
                raise ToolError("移除前资源发生变化，已停止。")
            check_cancel(self.m.cancel)
            # Atomic same-volume removal from the game's search path; backup already verified.
            os.rename(no_links(self.hd), no_links(self.removing))
        if self.removing.exists():
            remaining = self._verify(self.removing, self.entries, records=records, subset=True,
                                     phase="校验待移除 HD")
            self.m.idle()
            for name, stamp in remaining.items():
                check_cancel(self.m.cancel)
                path = no_links(self.removing / name)
                if signature(path) != stamp:
                    raise ToolError("待移除文件发生变化，备份保留，已停止。")
                path.unlink()
            no_links(self.removing).rmdir()
        self._save(data, "removed")
        same_volume = directory.stat().st_dev == self.m.root.stat().st_dev
        self.m.tell("HD 基础资源已移出游戏。恢复 HD 备份后才可回退；请保留 WeGame 的 -krqlv=uhd 参数。")
        return {"status": "hd_removed", "backup_directory": str(directory), "removed_bytes": self.total,
                "same_volume_backup": same_volume, "freed_game_drive_bytes": 0 if same_volume else self.total}

    def restore(self, backup_directory):
        self.m.check_root(allow_missing_resources=True)
        self.m.idle()
        owner = self._lock_state()
        directory = self._external(backup_directory)
        with exclusive_lock(self.m.work / "operation.lock"):
            data = self._state()
            resuming = bool(data and data["stage"] == "restoring")
            if data and data["stage"] == "removing":
                raise ToolError("请先再次执行“备份并移除 HD”完成中断的移除，再恢复备份。")
            if resuming and canonical(data["backup_directory"]) != directory:
                raise ToolError("请先选择上次恢复操作使用的同一份备份。")
            if not resuming:
                self.require_no_pending()
                if self.hd.exists():
                    raise ToolError("游戏已有 HD 目录，拒绝覆盖。")
            records, digest = self._load_backup(directory, data["manifest_sha256"] if resuming else None)
            for path in (self.hd, self.restoring):
                no_links(path)
            if resuming and self.hd.exists() and not self.restoring.exists():
                self._verify(self.hd, self.entries, records=records, phase="核对已恢复 HD")
                self._save(data, "restored")
                return {"status": "hd_restored", "backup_directory": str(directory), "bytes": self.total}
            if self.hd.exists():
                raise ToolError("游戏已出现 HD 目录，拒绝覆盖。")
            if not resuming:
                data = self._save(self._record(owner, directory, digest), "restoring")
            no_links(self.restoring).mkdir(exist_ok=True)
            expected = {e["name"]: e for e in self.entries}
            # A cancelled copy may leave only an explicitly named partial file in this owned stage.
            for path in self.restoring.iterdir():
                no_links(path)
                if not path.is_file() or (path.name not in expected and path.name.removesuffix(".part") not in expected):
                    raise ToolError("恢复暂存目录存在未知文件，拒绝修改。")
            protected = self.m.preserved()
            needed = sum(e["size"] for e in self.entries if not (self.restoring / e["name"]).exists())
            self._check_space(self.m.work, needed)
            for i, entry in enumerate(self.entries, 1):
                target = no_links(self.restoring / entry["name"])
                if target.exists():
                    md5, sha = self._hashes(target)
                    if (target.stat().st_size != entry["size"] or md5 != entry["md5"]
                            or sha != records[entry["name"]]["sha256"]):
                        raise ToolError("已恢复的暂存文件损坏，拒绝覆盖。")
                else:
                    partial = no_links(target.with_name(target.name + ".part"))
                    if partial.exists():
                        partial.unlink()
                    self._copy_one(directory / "HD" / entry["name"], partial, entry, records[entry["name"]]["sha256"])
                    os.rename(no_links(partial), no_links(target))
                self._progress("恢复 HD 并核对 SHA256", i, len(self.entries), entry["name"])
            self._verify(self.restoring, self.entries, records=records, phase="启用前核对 HD")
            self.m.check_root(allow_missing_resources=True)
            self.m.verify_preserved(protected)
            self.m.idle()
            check_cancel(self.m.cancel)
            if no_links(self.hd).exists():
                raise ToolError("恢复时游戏出现 HD 目录，拒绝覆盖。")
            os.rename(no_links(self.restoring), self.hd)
            self._save(data, "restored")
            self.m.tell("HD 已恢复并通过校验。备份仍保留；现在可以回退 UHD，再移除 WeGame 的 UHD 参数。")
            return {"status": "hd_restored", "backup_directory": str(directory), "bytes": self.total}
