"""Read-only, snapshot-based HD backup inspection; never creates a lock or journal."""
from .core import ToolError, no_links
from .hd_resources import DELETION_RECEIPT, signature


def inspect_backup(manager, directory, *, verify=False):
    h = manager.hd_resources()
    directory = h._external(directory)
    records, digest = h._read_backup_manifest(directory)
    receipt_path = no_links(directory / DELETION_RECEIPT)
    receipt_stamp = signature(receipt_path) if receipt_path.exists() else None
    receipt = None
    if receipt_stamp:
        from .hd_backup_delete import BackupDeletion
        owner = manager.read_state()
        if not owner:
            raise ToolError("缺少安装记录，无法核对这份备份的删除记录。")
        receipt = BackupDeletion(manager)._receipt(directory, digest, owner)
    payload = no_links(directory / "HD")
    found = h._inventory(payload, h.entries, subset=True) if payload.exists() else {}
    complete = len(found) == len(h.entries)
    status = "inventory_ok" if complete else "incomplete"
    if receipt:
        status = receipt["stage"]
        if status == "deleted" and found:
            raise ToolError("删除记录与现存资源矛盾，请保留文件并检查。")
    elif verify and complete:
        checked = h._verify(payload, h.entries, records=records, phase="只读校验备份 MD5 / SHA256")
        if checked != found:
            raise ToolError("检查期间备份文件发生变化，请重新检查。")
        status = "verified"
    h._read_backup_manifest(directory, digest)
    after_receipt = signature(receipt_path) if no_links(receipt_path).exists() else None
    after_files = h._inventory(payload, h.entries, subset=True) if no_links(payload).exists() else {}
    if after_receipt != receipt_stamp or after_files != found:
        raise ToolError("检查期间备份或删除记录发生变化，请重新检查。")
    return {"backup_directory": str(directory), "status": status,
            "file_count": len(found), "expected_files": len(h.entries),
            "bytes": sum(e["size"] for e in h.entries if e["name"] in found),
            "hashes_verified": status == "verified"}
