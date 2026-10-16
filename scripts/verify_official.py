"""Read-only release-input verification; fetches JSON manifests, never game files."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wuwa_uhd.core import (CDNS, CONFIG_HOSTS, CORE_FILES, INDEX_PATH, Manager,
                           RESOURCE_VERSION, VERSION, parse_manifest)


def main():
    manager = Manager(Path.cwd() / "unused-verification-root")
    manager.online_check()
    transport = manager.transport
    index = None
    for host in CONFIG_HOSTS:
        try:
            index = json.loads(transport.metadata(host + INDEX_PATH))
            break
        except (OSError, ValueError):
            continue
    if index is None:
        raise RuntimeError("Official index unavailable")
    report = {"verified_at_utc": datetime.now(timezone.utc).isoformat(),
              "official_version": VERSION, "resource_identity_version": RESOURCE_VERSION,
              "real_game_modified": False, "resource_payload_downloaded": False, "packs": {}}
    for label, entries in (("uhd", manager.entries), ("hd", manager.hd_entries), ("common", None)):
        pack = index["resourcePacks"][label]
        if pack["version"] != VERSION:
            raise RuntimeError("Official version changed during verification")
        raw = None
        for host in CDNS:
            try:
                raw = transport.metadata(host + pack["indexFile"])
                break
            except OSError:
                continue
        if raw is None or hashlib.md5(raw).hexdigest() != pack["indexFileMd5"]:
            raise RuntimeError(label + " manifest hash failed")
        if entries is not None:
            actual = parse_manifest(raw, pack["indexFileMd5"], label.upper())
            if sorted(actual, key=lambda e: e["dest"]) != sorted(entries, key=lambda e: e["dest"]):
                raise RuntimeError(label + " payloads changed; old ownership cannot be reused")
            detail = {"same_payloads_as_canonical_manifest": True, "files": len(actual)}
        else:
            actual = {e["dest"]: (e["size"], e["md5"]) for e in json.loads(raw)["resource"]}
            if any(actual.get(path) != expected for path, expected in CORE_FILES.items()):
                raise RuntimeError("Pinned current client core profile does not match official")
            detail = {"current_core_profile_matches": True, "files": len(actual)}
        report["packs"][label] = dict(detail, index_file=pack["indexFile"],
                                     index_md5=pack["indexFileMd5"], bytes=pack["size"])
    manager.online_check()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
