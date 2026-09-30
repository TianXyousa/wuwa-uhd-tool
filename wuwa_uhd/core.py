from __future__ import annotations

import concurrent.futures
import contextlib
import ctypes
import gzip
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

VERSION = "3.7.0"
MANIFEST_MD5 = "bb8104efa17446c5cac8f35f1593fe59"
PACK_PATH = "launcher/game/G152/10003/3.7.0/983fc4b836c240319aa3a0fc2c9f8514/"
INDEX_PATH = "launcher/game/10003_oLNgHF1CESo51DGHN2odtp40e3oI1HfZ/G152/official/index.json"
CONFIG_HOSTS = (
    "https://prod-volcdn-gamestarter.kurogame.xyz/",
    "https://prod-tencentcdn-gamestarter.kurogame.com/",
    "https://prod-cn-alicdn-gamestarter.kurogame.com/",
)
CDNS = (
    "https://pcdownload-huoshan.aki-game.com/",
    "https://pcdownload-aliyun.aki-game.com/",
    "https://pcdownload-qcloud.aki-game.com/",
)
ALLOWED_HOSTS = {urllib.parse.urlsplit(u).hostname for u in CONFIG_HOSTS + CDNS}
CORE_FILES = {
    "Client/Binaries/Win64/Client-Win64-Shipping.exe": (169828120, "587e3e09018f3ab320f5e8c1da6bfa81"),
    "Client/Binaries/Win64/Client-Win64-ShippingBase.dll": (38673736, "5aa83a185ca0c85513063869847229b8"),
    "Wuthering Waves.exe": (484040, "c8d13bdca9f2c2a997800b8c71769db3"),
}
CHANNEL_FILES = (
    "Client/Binaries/Win64/ThirdParty/KrPcSdk_Mainland/KRSDKRes/KRSDKConfig.json",
    "Client/Binaries/Win64/ThirdParty/KrPcSdk_Mainland/KRSDKEx.dll",
    "LocalGameResources.json", "launcherDownloadConfig.json",
)
MARKER = ".wuwa-uhd-owner.json"
STATUSES = {"downloading", "ready", "installing", "active", "rolling_back", "parked"}


class ToolError(Exception):
    pass


class Cancelled(ToolError):
    pass


def check_cancel(event):
    if event.is_set():
        raise Cancelled("已暂停，下载进度保留在缓存中。")


def canonical(path):
    return Path(os.path.abspath(os.fspath(path)))


def no_links(path):
    """Reject junctions, symlinks and other reparse points before any access."""
    path = canonical(path)
    for item in reversed((path, *path.parents)):
        try:
            st = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:
            raise ToolError(f"为防止操作到其他目录，不支持符号链接或目录联接：{item}")
        if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
            raise ToolError(f"为防止改动共享文件，不支持硬链接：{item}")
    return path


def hash_file(path, algorithm="md5", cancel=None):
    no_links(path)
    h = hashlib.new(algorithm)
    with open(path, "rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            if cancel is not None:
                check_cancel(cancel)
            h.update(block)
    return h.hexdigest()


def atomic_json(path, value):
    no_links(path)
    temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    no_links(temp)
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def load_json(path, limit=2 * 1024 * 1024):
    no_links(path)
    if path.stat().st_size > limit:
        raise ToolError(f"配置文件过大，已停止：{path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ToolError(f"配置损坏，已停止以保护原文件：{path.name}") from exc


def validate_url(url):
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS
            or parts.username or parts.password or parts.port not in (None, 443)):
        raise ToolError("只允许访问工具内置的库洛官方 HTTPS 下载域名。")


class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class OfficialTransport:
    def open(self, url, offset=0, metadata=False):
        validate_url(url)
        headers = {"User-Agent": "WuwaUHDTool/1.0", "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        opener = urllib.request.build_opener(OfficialRedirect())
        return opener.open(urllib.request.Request(url, headers=headers), timeout=15)

    def metadata(self, url):
        with self.open(url, metadata=True) as response:
            raw = response.read(3 * 1024 * 1024 + 1)
        if len(raw) > 3 * 1024 * 1024:
            raise ToolError("官方索引超过允许大小。")
        if raw.startswith(b"\x1f\x8b"):
            import io
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                raw = stream.read(3 * 1024 * 1024 + 1)
        if len(raw) > 3 * 1024 * 1024:
            raise ToolError("解压后的官方索引超过允许大小。")
        return raw


def parse_manifest(raw, expected=MANIFEST_MD5):
    if hashlib.md5(raw).hexdigest() != expected:
        raise ToolError("UHD 清单校验失败，拒绝下载或写入。")
    try:
        entries = json.loads(raw)["resource"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ToolError("UHD 清单格式无效。") from exc
    if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
        raise ToolError("UHD 清单文件数量异常。")
    names = set()
    result = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ToolError("资源清单条目无效。")
        dest, size, digest = entry.get("dest"), entry.get("size"), entry.get("md5")
        if (not isinstance(dest, str)
                or not re.fullmatch(r"Client/Content/UHD/[A-Za-z0-9_.-]+\.(pak|sig)", dest)
                or ".." in dest or PurePosixPath(dest).name.startswith(".")
                or isinstance(size, bool) or not isinstance(size, int) or not 0 < size < 100 * 2**30
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", digest)):
            raise ToolError("清单包含不安全的路径、大小或校验值。")
        name = PurePosixPath(dest).name
        if name.casefold() in names:
            raise ToolError("清单包含重复文件名。")
        names.add(name.casefold())
        result.append({"dest": dest, "name": name, "size": size, "md5": digest.lower()})
    return result


def bundled_manifest():
    return (Path(__file__).parent / "data" / "uhd-3.7.0.json").read_bytes()


def processes_for_game(root):
    """Inspect process image paths only; never read account or launch tokens."""
    if os.name != "nt":
        return []
    from ctypes import wintypes as w
    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ProcessID", w.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", w.DWORD),
                    ("cntThreads", w.DWORD), ("th32ParentProcessID", w.DWORD),
                    ("pcPriClassBase", w.LONG), ("dwFlags", w.DWORD), ("szExeFile", w.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    kernel.Process32FirstW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ToolError("无法检测运行中的游戏，已停止。")
    blocked = []
    prefix = str(canonical(root)).casefold().rstrip("\\/") + os.sep
    always = {"client-win64-shipping.exe", "wuthering waves.exe", "launcher_main.exe", "launcher_updater.exe"}
    try:
        ent = Entry()
        ent.dwSize = ctypes.sizeof(ent)
        more = kernel.Process32FirstW(snapshot, ctypes.byref(ent))
        while more:
            name = ent.szExeFile
            handle = kernel.OpenProcess(0x1000, False, ent.th32ProcessID)
            matched = False
            if handle:
                try:
                    length = w.DWORD(32768)
                    buffer = ctypes.create_unicode_buffer(length.value)
                    if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                        matched = buffer.value.casefold().startswith(prefix)
                finally:
                    kernel.CloseHandle(handle)
            if matched or name.casefold() in always:
                blocked.append(name)
            more = kernel.Process32NextW(snapshot, ctypes.byref(ent))
    finally:
        kernel.CloseHandle(snapshot)
    return sorted(set(blocked))


@contextlib.contextmanager
def exclusive_lock(path):
    no_links(path)
    stream = path.open("a+b")
    locked = False
    try:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise ToolError("另一个工具实例正在操作此游戏，请等待其完成。") from exc
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


class Manager:
    def __init__(self, game, *, manifest=None, expected_md5=MANIFEST_MD5,
                 core_files=None, transport=None, guard=None, event=None, cancel=None):
        self.root = canonical(game)
        self.manifest_raw = bundled_manifest() if manifest is None else manifest
        self.manifest_md5 = expected_md5
        self.entries = parse_manifest(self.manifest_raw, expected_md5)
        self.core_files = CORE_FILES if core_files is None else core_files
        self.transport = transport or OfficialTransport()
        self.guard = guard or processes_for_game
        self.event = event or (lambda value: None)
        self.cancel = cancel or threading.Event()
        tag = hashlib.sha256(str(self.root).casefold().encode("utf-8")).hexdigest()[:12]
        self.work = self.root.parent / (".wuwa-uhd-" + tag)
        self.cache = self.work / "UHD"
        self.parts = self.work / "parts"
        self.target = self.root / "Client" / "Content" / "UHD"
        self.state_path = self.work / "state.json"
        self.total = sum(e["size"] for e in self.entries)
        self._progress_lock = threading.Lock()
        self._received = {}
        self._last_progress = 0.0

    def tell(self, text):
        self.event({"kind": "log", "text": text})

    def progress(self, name, size, force=False):
        with self._progress_lock:
            self._received[name] = size
            now = time.monotonic()
            if force or now - self._last_progress >= 0.2:
                self._last_progress = now
                self.event({"kind": "progress", "current": sum(self._received.values()),
                            "total": self.total, "file": name})

    def check_root(self, hashes=True):
        if str(self.root).startswith("\\\\"):
            raise ToolError("不支持网络共享目录，请选择本地游戏目录。")
        for path in (self.root, self.root / "Client/Content/HD", self.work, self.cache,
                     self.parts, self.target, self.state_path):
            no_links(path)
        if not self.root.is_dir() or not (self.root / "Client/Content/HD").is_dir():
            raise ToolError("请选择已有 HD 版游戏的根目录，其中应包含 Client 和 Wuthering Waves.exe。")
        for rel, (size, digest) in self.core_files.items():
            path = no_links(self.root / rel)
            if not path.is_file() or path.stat().st_size != size:
                raise ToolError(f"客户端版本不匹配：{rel}。工具仅支持已核验的 3.7.0 基础客户端。")
            if hashes and hash_file(path, cancel=self.cancel) != digest:
                raise ToolError(f"客户端校验不匹配：{rel}。请勿在其他版本或改动过的客户端上使用。")

    def idle(self):
        running = self.guard(self.root)
        if running:
            raise ToolError("请先关闭游戏及库洛更新程序：" + "、".join(running))

    def read_state(self):
        if not self.state_path.exists():
            return None
        state = load_json(self.state_path)
        if (not isinstance(state, dict) or state.get("schema") != 1
                or state.get("game_root") != str(self.root)
                or state.get("version") != VERSION
                or state.get("manifest_md5") != self.manifest_md5
                or state.get("status") not in STATUSES
                or not re.fullmatch(r"[0-9a-f]{32}", str(state.get("owner", "")))):
            raise ToolError("缓存记录与当前游戏不匹配，已停止。请勿手动更改 state.json。")
        return state

    def save_state(self, state, status):
        state["status"] = status
        state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        atomic_json(self.state_path, state)

    def owned(self, directory, state):
        no_links(directory)
        marker = directory / MARKER
        if not directory.is_dir() or not marker.is_file():
            raise ToolError("此 UHD 目录不属于本工具，拒绝覆盖或回退。")
        info = load_json(marker)
        if info != {"owner": state["owner"], "manifest_md5": self.manifest_md5,
                    "game_root": str(self.root)}:
            raise ToolError("UHD 所有权记录不匹配，拒绝移动。")
        for path in directory.iterdir():
            no_links(path)
            if not path.is_file():
                raise ToolError("UHD 目录存在未知子目录，已停止。")

    def recover(self, state):
        if state["status"] not in {"installing", "rolling_back"}:
            return
        target, cache = self.target.exists(), self.cache.exists()
        if target == cache:
            raise ToolError("中断恢复时发现目录冲突，请保留缓存与游戏文件并联系维护者。")
        self.owned(self.target if target else self.cache, state)
        self.save_state(state, "active" if target else "parked")
        self.tell("已恢复上次中断操作的状态记录。")

    def online_check(self):
        errors = []
        for host in CONFIG_HOSTS:
            check_cancel(self.cancel)
            try:
                obj = json.loads(self.transport.metadata(host + INDEX_PATH))
                pack = obj["resourcePacks"]["uhd"]
                if (pack["version"] != VERSION or pack["indexFileMd5"] != self.manifest_md5
                        or pack["indexFile"] != PACK_PATH + "uhd/indexFile.json"
                        or pack["baseUrl"] != PACK_PATH + "zip/"
                        or int(pack["size"]) != self.total
                        or obj["bundles"]["UHD"]["resourcePacks"] != ["common", "uhd"]):
                    raise ToolError("官方资源版本已变化。工具停止安装，请更新工具；回退仍可使用。")
                return
            except ToolError:
                raise
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors.append(type(exc).__name__)
        raise ToolError("无法核对官方当前版本，未执行安装。请检查网络后重试。" + " / ".join(errors))

    def inspect(self, online=True):
        self.tell("正在只读核对客户端版本与现有目录……")
        self.check_root()
        state = self.read_state()
        if self.target.exists():
            if state is None:
                raise ToolError("游戏已经有 UHD 目录，但不是本工具管理的；不会覆盖它。")
            self.owned(self.target, state)
        if online:
            self.online_check()
        free = shutil.disk_usage(self.root.parent).free
        return {"game_root": str(self.root), "version": VERSION,
                "status": state["status"] if state else "not_installed",
                "files": len(self.entries), "download_bytes": self.total,
                "free_bytes": free, "cache_directory": str(self.work),
                "online_verified": online, "running": self.guard(self.root),
                "target_exists": self.target.exists(), "cache_exists": self.cache.exists(),
                "runtime_verified": False}

    def preserved(self):
        snapshot = {}
        for rel in (*self.core_files, *CHANNEL_FILES):
            path = no_links(self.root / rel)
            snapshot[rel] = hash_file(path, "sha256", self.cancel) if path.is_file() else None
        return snapshot

    def verify_preserved(self, snapshot):
        if self.preserved() != snapshot:
            raise ToolError("下载期间核心或渠道文件发生变化，已停止启用 UHD。请关闭其他更新程序后重试。")

    def check_cache(self):
        permitted = {e["name"] for e in self.entries} | {MARKER}
        for folder in (self.cache, self.parts):
            no_links(folder)
            if not folder.exists():
                continue
            for path in folder.iterdir():
                no_links(path)
                allowed = permitted if folder == self.cache else {e["name"] + ".part" for e in self.entries}
                if path.name not in allowed or not path.is_file():
                    raise ToolError(f"缓存中存在未知文件，拒绝修改：{path.name}")

    def ensure_space(self):
        remaining = 0
        for e in self.entries:
            complete = self.cache / e["name"]
            part = self.parts / (e["name"] + ".part")
            for p in (complete, part):
                no_links(p)
            present = complete.stat().st_size if complete.is_file() else part.stat().st_size if part.is_file() else 0
            remaining += max(0, e["size"] - min(present, e["size"]))
        free = shutil.disk_usage(self.root.parent).free
        if free < remaining + 2 * 2**30:
            raise ToolError(f"空间不足：仍需约 {remaining / 2**30:.1f} GiB 下载空间，并预留 2 GiB。")

    def _download_one(self, entry):
        check_cancel(self.cancel)
        name, size, digest = entry["name"], entry["size"], entry["md5"]
        target = no_links(self.cache / name)
        part = no_links(self.parts / (name + ".part"))
        if target.exists():
            self.tell("校验缓存：" + name)
            if target.stat().st_size == size and hash_file(target, cancel=self.cancel) == digest:
                self.progress(name, size, True)
                return
            raise ToolError(f"已完成的缓存文件损坏：{name}。已停止，不自动覆盖它。")
        if part.exists() and part.stat().st_size > size:
            raise ToolError(f"断点文件大小异常：{name}")
        for attempt in range(6):
            check_cancel(self.cancel)
            offset = part.stat().st_size if part.exists() else 0
            self.progress(name, offset)
            if offset == size:
                self.tell("验证完整 MD5：" + name)
                if hash_file(part, cancel=self.cancel) == digest:
                    no_links(target)
                    os.rename(part, target)
                    self.progress(name, size, True)
                    self.tell("校验通过：" + name)
                    return
                # Only our partial file is discarded. Existing game files are never touched.
                part.unlink()
                self.progress(name, 0, True)
                self.tell("断点内容校验失败，重新下载：" + name)
                offset = 0
            host = CDNS[attempt % len(CDNS)]
            url = host + PACK_PATH + "zip/" + entry["dest"]
            try:
                self.tell(("续传：" if offset else "下载：") + name)
                with self.transport.open(url, offset=offset) as response:
                    status = response.status
                    encoding = response.headers.get("Content-Encoding", "identity").lower()
                    if encoding not in ("", "identity"):
                        raise ToolError("资源服务器返回了非原始编码，拒绝写入。")
                    if status == 206:
                        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                        if not match or tuple(map(int, match.groups())) != (offset, size - 1, size):
                            raise ToolError("分段下载范围与清单不符。")
                    elif status == 200:
                        offset = 0  # Server ignored Range: restart, never append a full response.
                    else:
                        raise ToolError(f"下载响应状态异常：{status}")
                    length = response.headers.get("Content-Length")
                    if length is None or not length.isdigit() or int(length) != size - offset:
                        raise ToolError("下载响应长度与清单不符。")
                    check_cancel(self.cancel)
                    no_links(part)
                    with part.open("ab" if offset else "wb") as stream:
                        position = offset
                        while True:
                            check_cancel(self.cancel)
                            block = response.read(min(1024 * 1024, size - position + 1))
                            if not block:
                                break
                            if position + len(block) > size:
                                raise ToolError("服务器返回的数据超过清单大小。")
                            stream.write(block)
                            position += len(block)
                            self.progress(name, position)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if position != size:
                        raise OSError("下载提前结束，将续传")
                # Verify now as well as at the beginning of a resumed attempt.
                self.tell("验证完整 MD5：" + name)
                if hash_file(part, cancel=self.cancel) != digest:
                    part.unlink()
                    self.progress(name, 0, True)
                    raise OSError("内容 MD5 不匹配，将更换官方 CDN")
                no_links(target)
                os.rename(part, target)
                self.progress(name, size, True)
                self.tell("校验通过：" + name)
                return
            except Cancelled:
                raise
            except (OSError, urllib.error.URLError, http.client.HTTPException, ToolError) as exc:
                if attempt == 5:
                    raise ToolError(f"下载未完成，缓存已保留：{name}；{exc}") from exc
                self.tell(f"更换官方线路重试：{name}（{exc}）")
        raise ToolError("下载未完成。")

    def apply(self):
        self.check_root()
        self.idle()
        self.online_check()
        if self.target.exists() and not self.state_path.exists():
            raise ToolError("发现已有 UHD 目录，拒绝覆盖。")
        if self.work.exists() and self.read_state() is None:
            raise ToolError("发现无状态记录的缓存目录，拒绝占用或覆盖。")
        no_links(self.work)
        self.work.mkdir(exist_ok=True)
        with exclusive_lock(self.work / "operation.lock"):
            state = self.read_state()
            if state:
                self.recover(state)
            if self.target.exists():
                if state and state["status"] == "active":
                    self.owned(self.target, state)
                    raise ToolError("UHD 已启用，无需再次安装。")
                raise ToolError("发现已有 UHD 目录，拒绝覆盖。")
            if state and state["status"] == "active":
                raise ToolError("记录显示 UHD 已启用，但目录缺失。请保留缓存并检查游戏目录。")
            if state is None:
                state = {"schema": 1, "owner": uuid.uuid4().hex, "game_root": str(self.root),
                         "version": VERSION, "manifest_md5": self.manifest_md5}
            self.save_state(state, "downloading")
            before = self.preserved()
            for folder in (self.cache, self.parts):
                no_links(folder)
                folder.mkdir(exist_ok=True)
            self.check_cache()
            if (self.cache / MARKER).exists():
                self.owned(self.cache, state)
            self.ensure_space()
            self.tell("只写入独立缓存。全部文件通过 MD5 后才会添加 UHD 目录。")
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                    futures = [pool.submit(self._download_one, e) for e in self.entries]
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            future.result()
                        except BaseException:
                            self.cancel.set()
                            for pending in futures:
                                pending.cancel()
                            raise
            except BaseException:
                self.tell("游戏目录未启用新资源，下载缓存已保留。")
                raise
            check_cancel(self.cancel)
            self.online_check()
            self.check_root()
            self.idle()
            self.verify_preserved(before)
            self.check_cache()
            if self.target.exists():
                raise ToolError("下载期间出现了 UHD 目录，拒绝覆盖。")
            marker = {"owner": state["owner"], "manifest_md5": self.manifest_md5,
                      "game_root": str(self.root)}
            atomic_json(self.cache / MARKER, marker)
            state["preserved_sha256"] = before
            self.save_state(state, "installing")
            no_links(self.target)
            no_links(self.cache)
            # Same-volume directory rename: no copying over existing game files.
            os.rename(self.cache, self.target)
            self.save_state(state, "active")
            self.tell("UHD 基础资源已添加；原 HD、程序及渠道文件未改写。游戏内效果尚待验证。")
            return {"status": "active", "files": len(self.entries), "bytes": self.total}

    def rollback(self):
        # Do not require old core hashes or online access: rollback remains available after updates.
        for path in (self.root, self.work, self.state_path, self.cache, self.target):
            no_links(path)
        self.idle()
        if not self.work.is_dir():
            raise ToolError("没有本工具的安装记录，无需回退。")
        with exclusive_lock(self.work / "operation.lock"):
            state = self.read_state()
            if state is None:
                raise ToolError("没有有效安装记录，不会移动游戏文件。")
            self.recover(state)
            if not self.target.exists():
                if state["status"] == "active":
                    raise ToolError("已启用的 UHD 目录缺失，请检查目录；不会修改记录掩盖异常。")
                self.tell("原 HD 未被替换；目前没有需要移出的 UHD 目录。缓存保留。")
                return {"status": state["status"], "changed": False}
            self.owned(self.target, state)
            if self.cache.exists():
                raise ToolError("回退缓存位置已被占用，拒绝覆盖。")
            check_cancel(self.cancel)
            self.idle()
            self.save_state(state, "rolling_back")
            no_links(self.target)
            no_links(self.cache)
            os.rename(self.target, self.cache)
            self.save_state(state, "parked")
            self.tell("已回退：UHD 已移回缓存。请按原来的 WeGame 方式启动 HD。")
            return {"status": "parked", "changed": True, "cache_kept": True}

    def clear_cache(self):
        """Explicit user action. Deletes only known tool cache files, never game paths."""
        for path in (self.root, self.work, self.state_path, self.cache, self.parts, self.target):
            no_links(path)
        if not self.work.is_dir():
            raise ToolError("没有本工具的缓存。")
        with exclusive_lock(self.work / "operation.lock"):
            state = self.read_state()
            if not state:
                raise ToolError("缺少所有权记录，拒绝清理。")
            self.recover(state)
            if self.target.exists() or state["status"] == "active":
                raise ToolError("请先回退，再清理缓存。")
            self.check_cache()
            if (self.cache / MARKER).exists():
                self.owned(self.cache, state)
            removed = 0
            for folder in (self.cache, self.parts):
                if not folder.exists():
                    continue
                for path in list(folder.iterdir()):
                    check_cancel(self.cancel)
                    no_links(path)
                    removed += path.stat().st_size
                    path.unlink()
                folder.rmdir()
            self.save_state(state, "parked")
            self.tell(f"已清理缓存，释放约 {removed / 2**30:.2f} GiB。原游戏未改动。")
            return {"status": "parked", "removed_bytes": removed}

    def launch_uhd(self):
        self.check_root()
        self.idle()
        state = self.read_state()
        if not state or state["status"] != "active":
            raise ToolError("请先完整下载并启用 UHD。")
        with exclusive_lock(self.work / "operation.lock"):
            self.owned(self.target, state)
            for entry in self.entries:
                path = no_links(self.target / entry["name"])
                if not path.is_file() or path.stat().st_size != entry["size"]:
                    raise ToolError("UHD 文件缺失或大小变化，请勿启动。")
            subprocess.Popen([str(self.root / "Wuthering Waves.exe"), "-krqlv=uhd"], cwd=self.root)
        return {"status": "launch_requested", "runtime_verified": False}


def detect_game():
    if os.name == "nt":
        import winreg
        for base in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(base, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0, winreg.KEY_READ | view) as key:
                        for i in range(winreg.QueryInfoKey(key)[0]):
                            try:
                                with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                                    title = winreg.QueryValueEx(sub, "DisplayName")[0]
                                    if title not in ("鸣潮", "Wuthering Waves"):
                                        continue
                                    command = winreg.QueryValueEx(sub, "UninstallString")[0]
                                    exe = command.split('"')[1] if command.startswith('"') else command.split('.exe')[0] + '.exe'
                                    candidate = Path(exe).parent
                                    if (candidate / "Client").is_dir():
                                        return str(candidate)
                            except OSError:
                                continue
                except OSError:
                    continue
    return ""
