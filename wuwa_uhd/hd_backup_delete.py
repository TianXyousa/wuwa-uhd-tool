"""Explicit, confirmed deletion of one verified external HD backup's payload."""
from .core import Cancelled, ToolError, atomic_json, check_cancel, exclusive_lock, load_json, no_links
from .hd_resources import DELETION_RECEIPT, signature


class BackupDeletion:
    def __init__(self, manager):
        self.m = manager
        self.h = manager.hd_resources()

    def _receipt(self, directory, digest, owner):
        path = no_links(directory / DELETION_RECEIPT)
        if not path.exists():
            return None
        data = load_json(path)
        if (not isinstance(data, dict) or data.get("schema") != 1
                or data.get("owner") != owner["owner"] or data.get("game_root") != str(self.m.root)
                or data.get("backup_directory") != str(directory) or data.get("manifest_sha256") != digest
                or data.get("stage") not in {"deleting", "deleted"}):
            raise ToolError("HD 备份删除记录无效，拒绝继续。")
        return data

    def prepare(self, backup_directory):
        """Read-only plan bound to this path, manifest, and exact remaining files."""
        self.m.check_root()
        self.m.idle()
        owner = self.h._lock_state()
        self.h.require_no_pending()
        self.h.verify_uhd(hashes=False)
        directory = self.h._external(backup_directory)
        _, digest = self.h._read_backup_manifest(directory)
        receipt = self._receipt(directory, digest, owner)
        if receipt and receipt["stage"] == "deleted":
            raise ToolError("这份备份的 HD 资源已经永久删除，无需再次操作。")
        payload = no_links(directory / "HD")
        if payload.exists() or not receipt:
            found = self.h._inventory(payload, self.h.entries, subset=bool(receipt))
        else:
            found = {}  # A crash after rmdir but before the final receipt can be finalized.
        sizes = {e["name"]: e["size"] for e in self.h.entries}
        return {"backup_directory": str(directory), "manifest_sha256": digest,
                "files": {name: list(stamp) for name, stamp in found.items()},
                "file_count": len(found), "bytes": sum(sizes[name] for name in found),
                "resuming": receipt is not None}

    def delete(self, plan, *, confirmed_playable=False):
        if confirmed_playable is not True:
            raise ToolError("必须先确认已实际进入游戏并正常游玩，再次确认后才能永久删除 HD 备份。")
        if not isinstance(plan, dict) or not isinstance(plan.get("backup_directory"), str):
            raise ToolError("缺少待删除备份的确认清单。")
        owner = self.h._lock_state()
        with exclusive_lock(self.m.work / "operation.lock"):
            if self.prepare(plan["backup_directory"]) != plan:
                raise ToolError("备份内容已变化，请重新选择并确认；本次未删除。")
            directory = self.h._external(plan["backup_directory"])
            payload = no_links(directory / "HD")
            protected = self.m.preserved()
            uhd_before = self.h.verify_uhd()
            records, _ = self.h._read_backup_manifest(directory, plan["manifest_sha256"])
            if payload.exists():
                self.h._verify(payload, self.h.entries, records=records, subset=plan["resuming"],
                               phase="永久删除前核对 HD 备份")
            if self.prepare(directory) != plan or self.h.verify_uhd(hashes=False) != uhd_before:
                raise ToolError("确认后资源发生变化，已停止；本次未删除。")
            self.m.verify_preserved(protected)
            check_cancel(self.m.cancel)
            receipt_path = no_links(directory / DELETION_RECEIPT)
            receipt = dict(self.h._record(owner, directory, plan["manifest_sha256"]), stage="deleting")
            atomic_json(receipt_path, receipt)
            self.m.tell("永久删除已确认，仅处理这一批次的 HD 基础包：" + str(payload))
            try:
                for i, (name, stamp) in enumerate(plan["files"].items(), 1):
                    check_cancel(self.m.cancel)
                    self.m.idle()
                    path = no_links(payload / name)
                    if list(signature(path)) != stamp:
                        raise ToolError("待删除备份文件发生变化，已停止。")
                    path.unlink()
                    self.h._progress("永久删除 HD 备份", i, plan["file_count"], name)
                if no_links(payload).exists():
                    payload.rmdir()  # Never recurse; unknown files are left untouched.
                atomic_json(receipt_path, dict(receipt, stage="deleted"))
            except (OSError, ToolError) as exc:
                message = (str(exc) + "\n已删除的备份文件无法撤销，其余文件保留；"
                           "可重新选择同一批次并再次确认以继续删除。")
                if isinstance(exc, Cancelled):
                    raise Cancelled(message) from exc
                raise ToolError(message) from exc
            self.m.tell("所选 HD 备份资源已永久删除，不经回收站；清单与删除记录保留。游戏文件未改动。")
            return {"status": "hd_backup_deleted", "backup_directory": str(directory),
                    "deleted_files": plan["file_count"], "deleted_bytes": plan["bytes"]}
