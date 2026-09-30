from __future__ import annotations

import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .core import Cancelled, Manager, ToolError, detect_game

BG = "#f4f6fa"
INK = "#172438"
MUTED = "#59677b"
ACCENT = "#2563eb"


class App:
    def __init__(self, root, *, auto_detect=True):
        self.root = root
        root.title(f"鸣潮 UHD 资源工具  {__version__}")
        system_scale = float(root.tk.call("tk", "scaling")) / (96 / 72)
        self.scale = max(0.8, min(system_scale, (root.winfo_screenwidth() - 80) / 940,
                                  (root.winfo_screenheight() - 100) / 770))
        root.tk.call("tk", "scaling", self.scale * 96 / 72)
        root.geometry(f"{int(940 * self.scale)}x{int(770 * self.scale)}")
        root.minsize(int(840 * self.scale), int(720 * self.scale))
        icon = Path(__file__).parent / "data" / "app.ico"
        if icon.is_file():
            root.iconbitmap(default=str(icon))
        root.configure(bg=BG)
        self.queue = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.started = 0.0
        self.last_progress = (0.0, 0)
        self.speed = 0.0
        self.game = tk.StringVar(value=detect_game() if auto_detect else "")
        self.status = tk.StringVar(value="等待检查 · 当前游戏不会自动更新")
        self.summary = tk.StringVar(value="3.7.0 国服基础客户端 · 100 个 UHD 文件 · 61.50 GiB")
        self.progress_text = tk.StringVar(value="未下载 UHD 资源")
        self._build()
        self.root.after(100, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _build(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=INK, font=("Microsoft YaHei UI", 10))
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 23, "bold"))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(12, 9))
        style.configure("Primary.TButton", background=ACCENT, foreground="white", borderwidth=0)
        style.map("Primary.TButton", background=[("active", "#1d4ed8"), ("disabled", "#a8b9d8")])
        style.configure("TEntry", padding=8, font=("Microsoft YaHei UI", 10))
        style.configure("Horizontal.TProgressbar", troughcolor="#dce3ee", background=ACCENT, borderwidth=0)
        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="为鸣潮添加 UHD 资源", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="官方下载源  /  保留 HD 与渠道文件  /  支持回退", style="Muted.TLabel").pack(anchor="w", pady=(4, 18))
        ttk.Label(outer, text="游戏目录（包含 Client 和 Wuthering Waves.exe）").pack(anchor="w")
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(7, 12))
        self.path_entry = ttk.Entry(row, textvariable=self.game)
        self.path_entry.pack(side="left", fill="x", expand=True)
        self.browse_button = ttk.Button(row, text="选择目录", command=self.browse)
        self.browse_button.pack(side="left", padx=(8, 0))
        card = tk.Frame(outer, bg="white", highlightbackground="#dce3ee", highlightthickness=1, padx=16, pady=13)
        card.pack(fill="x", pady=(0, 12))
        tk.Label(card, textvariable=self.status, bg="white", fg=INK, font=("Microsoft YaHei UI", 12, "bold"), anchor="w").pack(fill="x")
        tk.Label(card, textvariable=self.summary, bg="white", fg=MUTED, font=("Microsoft YaHei UI", 10), anchor="w", justify="left", wraplength=int(830*self.scale)).pack(fill="x", pady=(6, 0))
        info = (
            "下载全部校验成功后，仅添加 Client/Content/UHD。原程序、HD 包及 WeGame 渠道配置不改写。\n"
            "“回退到 HD”会将本工具添加的 UHD 移回缓存；回退后仍按原来的 WeGame 方式启动。\n"
            "WeGame 下 UHD 的登录与画质效果尚未实测。UHD 启动需单独确认，游戏可能继续下载热更新和视频。"
        )
        ttk.Label(outer, text=info, style="Muted.TLabel", wraplength=int(850*self.scale), justify="left").pack(anchor="w", pady=(0, 14))
        actions = ttk.Frame(outer)
        actions.pack(fill="x")
        self.check_button = ttk.Button(actions, text="1. 只读检查", command=lambda: self.start("inspect"))
        self.check_button.pack(side="left", padx=(0, 8))
        self.apply_button = ttk.Button(actions, text="2. 下载 / 恢复 UHD", style="Primary.TButton", command=self.confirm_apply)
        self.apply_button.pack(side="left", padx=(0, 8))
        self.rollback_button = ttk.Button(actions, text="回退到 HD", command=self.confirm_rollback)
        self.rollback_button.pack(side="left", padx=(0, 8))
        self.pause_button = ttk.Button(actions, text="暂停", command=self.pause, state="disabled")
        self.pause_button.pack(side="right")
        secondary = ttk.Frame(outer)
        secondary.pack(fill="x", pady=(9, 14))
        self.launch_button = ttk.Button(secondary, text="以 UHD 启动（待验证）", command=self.confirm_launch)
        self.launch_button.pack(side="left", padx=(0, 8))
        self.cache_button = ttk.Button(secondary, text="打开缓存目录", command=self.open_cache)
        self.cache_button.pack(side="left", padx=(0, 8))
        self.clear_button = ttk.Button(secondary, text="清理已回退缓存", command=self.confirm_clear)
        self.clear_button.pack(side="left")
        self.buttons = [self.check_button, self.apply_button, self.rollback_button,
                        self.launch_button, self.cache_button, self.clear_button, self.browse_button]
        self.bar = ttk.Progressbar(outer, maximum=100, mode="determinate")
        self.bar.pack(fill="x")
        ttk.Label(outer, textvariable=self.progress_text, style="Muted.TLabel", wraplength=int(850*self.scale)).pack(anchor="w", pady=(6, 12))
        log_head = ttk.Frame(outer)
        log_head.pack(fill="x")
        ttk.Label(log_head, text="操作记录").pack(side="left")
        ttk.Button(log_head, text="导出日志", command=self.export_log).pack(side="right")
        frame = ttk.Frame(outer)
        frame.pack(fill="both", expand=True, pady=(6, 0))
        self.log = tk.Text(frame, height=10, bg="#ffffff", fg=INK, font=("Microsoft YaHei UI", 9),
                           relief="flat", borderwidth=0, padx=12, pady=10, wrap="word", state="disabled")
        scroll = ttk.Scrollbar(frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.append("工具已就绪。点击“只读检查”不会写入游戏目录，也不会下载游戏资源。")

    def append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S") + "  " + str(text) + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def browse(self):
        path = filedialog.askdirectory(title="选择鸣潮游戏根目录", initialdir=self.game.get() or None)
        if path:
            self.game.set(path)
            self.status.set("目录已更换 · 请重新检查")

    def manager(self):
        value = self.game.get().strip()
        if not value:
            raise ToolError("请先选择游戏目录。")
        return Manager(value, event=self.queue.put, cancel=self.cancel)

    def start(self, action):
        if self.busy:
            return
        self.cancel = threading.Event()
        try:
            manager = self.manager()
        except Exception as exc:
            messagebox.showerror("无法开始", str(exc), parent=self.root)
            return
        self.busy = True
        self.started = time.monotonic()
        self.last_progress = (self.started, 0)
        self.speed = 0.0
        for button in self.buttons:
            button.configure(state="disabled")
        self.path_entry.configure(state="disabled")
        self.pause_button.configure(state="normal" if action in {"apply", "inspect"} else "disabled")
        labels = {"inspect": "正在只读检查", "apply": "正在下载 / 校验 UHD", "rollback": "正在回退",
                  "clear_cache": "正在清理缓存", "launch_uhd": "正在检查启动条件"}
        self.status.set(labels[action])
        self.append(labels[action])
        self.bar.configure(mode="indeterminate")
        self.bar.start(15)

        def worker():
            try:
                result = getattr(manager, action)()
                self.queue.put({"kind": "done", "action": action, "result": result})
            except Cancelled as exc:
                self.queue.put({"kind": "stopped", "text": str(exc)})
            except Exception as exc:
                self.queue.put({"kind": "error", "text": str(exc) or type(exc).__name__})
        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        for _ in range(200):
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            kind = item["kind"]
            if kind == "log":
                self.append(item["text"])
            elif kind == "progress":
                self.bar.stop()
                self.bar.configure(mode="determinate")
                current, total = item["current"], item["total"]
                self.bar["value"] = 100 * current / max(1, total)
                now = time.monotonic()
                last_time, last_bytes = self.last_progress
                if now - last_time >= 1:
                    self.speed = max(0, (current - last_bytes) / (now - last_time))
                    self.last_progress = (now, current)
                self.progress_text.set(f"{current / 2**30:.2f} / {total / 2**30:.2f} GiB · {self.speed / 2**20:.1f} MiB/s · {item['file']}\n文件下载完成后还需进行 MD5 校验。")
            elif kind in {"done", "stopped", "error"}:
                self.bar.stop()
                self.bar.configure(mode="determinate")
                self.busy = False
                for button in self.buttons:
                    button.configure(state="normal")
                self.path_entry.configure(state="normal")
                self.pause_button.configure(state="disabled")
                if kind == "error":
                    self.status.set("操作已停止 · 请查看原因")
                    self.append(item["text"])
                    messagebox.showerror("操作已停止", item["text"], parent=self.root)
                elif kind == "stopped":
                    self.status.set("已暂停 · 可继续下载")
                    self.append(item["text"])
                else:
                    self.finish(item["action"], item["result"])
        self.root.after(100, self._poll)

    def finish(self, action, result):
        if action == "inspect":
            names = {"not_installed": "未安装 UHD", "downloading": "存在下载缓存", "ready": "缓存就绪",
                     "installing": "有待恢复的安装", "active": "UHD 已添加", "rolling_back": "有待恢复的回退", "parked": "已回退 / 缓存可复用"}
            self.status.set("检查通过 · " + names.get(result["status"], result["status"]))
            self.summary.set(f"客户端 3.7.0 核心校验通过 · 官方清单匹配\n需新增 {result['download_bytes']/2**30:.2f} GiB · 当前磁盘可用 {result['free_bytes']/2**30:.1f} GiB")
            self.append("缓存位置：" + result["cache_directory"])
            if result["running"]:
                self.append("下载/回退前需关闭：" + "、".join(result["running"]))
            self.progress_text.set("只读检查完成；未修改游戏文件。")
        elif action == "apply":
            self.status.set("UHD 基础资源已添加 · 游戏内效果待验证")
            self.bar["value"] = 100
            self.progress_text.set("100 个文件已校验。原启动方式保留；工具没有自动启动游戏。")
            messagebox.showinfo("资源已添加", "UHD 基础资源已下载并校验。\n\n原 HD 与 WeGame 渠道文件保留。点击“以 UHD 启动”可单独尝试；WeGame 登录和 UHD 效果尚未验证。", parent=self.root)
        elif action == "rollback":
            self.status.set("已回退 / 无需回退 · 按原方式启动 HD")
            self.progress_text.set("UHD 缓存保留，未释放其占用空间。需要时可恢复，或清理缓存。")
        elif action == "clear_cache":
            self.status.set("缓存已清理 · 原游戏保持不变")
            self.progress_text.set(f"释放约 {result['removed_bytes']/2**30:.2f} GiB。")
        else:
            self.status.set("已请求 UHD 启动 · 请在游戏中确认")
            self.append("启动请求已发送；这不代表登录或极致画质已验证成功。")

    def confirm_apply(self):
        if messagebox.askokcancel("下载并添加 UHD", "将从库洛官方服务器下载约 66.04 GB 基础资源。\n\n仅在全部校验通过后添加 UHD 目录，不覆盖 HD、程序或 WeGame 渠道文件，也不自动启动游戏。\n\n可以暂停续传；安装后可以回退。继续？", parent=self.root):
            self.start("apply")

    def confirm_rollback(self):
        if messagebox.askokcancel("回退到 HD", "请先退出游戏。\n\n仅将本工具添加的 UHD 目录移回缓存，保留以便恢复。原 HD 与原来的 WeGame 启动方式不改动。\n\n游戏启动后产生的画质设置、热更新、视频及存档不在回退范围内。继续？", parent=self.root):
            self.start("rollback")

    def confirm_clear(self):
        if messagebox.askokcancel("清理缓存并释放空间", "此操作只清理本工具的下载 / 已回退缓存。\n\n清理后恢复 UHD 需要重新下载；已启用的 UHD 必须先回退。原游戏文件不删除。\n\n确定清理？", parent=self.root):
            self.start("clear_cache")

    def confirm_launch(self):
        if messagebox.askokcancel("尝试 UHD 启动", "将用原游戏程序加上 -krqlv=uhd 参数启动，不修改快捷方式或 WeGame 配置。\n\nWeGame 登录和 UHD 效果尚未实测。请保持 WeGame 客户端登录；如无法登录或出现异常，请退出后回退，不要改渠道配置。\n\n游戏可能下载后续资源并修改自身设置。现在启动？", parent=self.root):
            self.start("launch_uhd")

    def pause(self):
        self.cancel.set()
        self.pause_button.configure(state="disabled")
        self.status.set("正在暂停 · 等待当前网络读取返回")
        self.append("暂停已请求；网络超时最长约 15 秒，已下载数据将保留。")

    def open_cache(self):
        try:
            manager = self.manager()
            if not manager.work.is_dir():
                raise ToolError("尚未创建缓存目录。")
            from .core import no_links
            no_links(manager.work)
            os.startfile(manager.work)
        except Exception as exc:
            messagebox.showinfo("缓存目录", str(exc), parent=self.root)

    def export_log(self):
        filename = filedialog.asksaveasfilename(title="导出操作记录", defaultextension=".txt", initialfile="鸣潮UHD工具日志.txt", filetypes=[("文本文件", "*.txt")])
        if filename:
            try:
                from .core import canonical, no_links
                destination = no_links(filename)
                if self.game.get().strip() and destination.is_relative_to(canonical(self.game.get().strip())):
                    raise ToolError("请将操作日志保存到游戏目录之外。")
                Path(filename).write_text(self.log.get("1.0", "end-1c"), encoding="utf-8")
            except (OSError, ToolError) as exc:
                messagebox.showerror("无法导出", str(exc), parent=self.root)

    def close(self):
        if self.busy:
            self.pause()
            messagebox.showinfo("请等待暂停完成", "正在安全停止操作。完成后可关闭窗口，缓存会保留。", parent=self.root)
            return
        self.root.destroy()


def configure_dpi():
    if os.name == "nt":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except OSError:
            pass


def run():
    configure_dpi()
    root = tk.Tk()
    App(root)
    root.mainloop()
