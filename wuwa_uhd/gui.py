from __future__ import annotations

import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import __version__
from .core import VERSION, Cancelled, Manager, ToolError, detect_game

BG = "#f4f6fa"
INK = "#172438"
MUTED = "#59677b"
ACCENT = "#2563eb"
DANGER = "#b91c1c"
DELETE_HD_WARNING = "危险：确认 UHD 能正常运行后再删除！删除后无法从这份备份恢复。"
WEGAME_HD_WARNING = (
    "建议保留 HD：若移除游戏内的 HD，WeGame 后续更新可能重新下载约 45.71 GB（42.57 GiB）的 HD 资源。"
)
WEGAME_LAUNCH_GUIDE = (
    "1. 等待 UHD 资源下载并校验完成。\n"
    "2. 打开 WeGame 中鸣潮的启动参数设置，保留原有参数，追加：\n\n"
    "    -krqlv=uhd\n\n"
    "3. 保存后，从 WeGame 启动鸣潮，再在游戏中选择极致画质。\n\n"
    "用户已实测此方式可启动 UHD 并切换极致画质。\n"
    "启动后仍需完成游戏内更新，可能包含 UHD 补丁或视频，大小以游戏提示为准。\n"
    "默认保留 HD；移除游戏内 HD 后，WeGame 更新可能将它重新下载。\n"
    "若已移除 HD 基础资源，请先恢复 HD 备份，再执行回退。\n"
    "回退到 HD 后，请在 WeGame 中移除 -krqlv=uhd，再从 WeGame 启动。\n"
    "工具不会自动修改 WeGame 设置；游戏可能继续下载热更新和视频。"
)


class HDBackupDeleteDialog(simpledialog.Dialog):
    def __init__(self, parent, plan):
        self.plan = plan
        super().__init__(parent, "第一次确认 · 永久删除 HD 备份")

    def body(self, master):
        self.confirmed = tk.BooleanVar(master=self, value=False)
        tk.Label(master, text=DELETE_HD_WARNING, fg=DANGER, font=("Microsoft YaHei UI", 11, "bold"),
                 wraplength=700, justify="left").pack(anchor="w", padx=16, pady=(14, 10))
        text = (f"所选备份：\n{self.plan['backup_directory']}\n\n"
                f"将永久删除 {self.plan['file_count']} 个 HD 资源文件，约 {self.plan['bytes']/2**30:.2f} GiB。\n"
                "删除不经过回收站，工具无法撤销。以后需要 HD 时，须另找完整备份或重新下载。\n"
                "只处理该批次的 HD 基础包，清单与删除记录保留。\n\n"
                + WEGAME_HD_WARNING + "\n删除外部备份本身不会移除游戏内的 HD。")
        tk.Label(master, text=text, font=("Microsoft YaHei UI", 10), wraplength=700,
                 justify="left").pack(anchor="w", padx=16, pady=(0, 12))
        self.checkbox = tk.Checkbutton(master, variable=self.confirmed, fg=DANGER, activeforeground=DANGER,
            text="我已通过 WeGame 实际进入 UHD 游戏并正常游玩，确认可以删除这份备份。",
            font=("Microsoft YaHei UI", 10), wraplength=700, justify="left", command=self._toggle)
        self.checkbox.pack(anchor="w", padx=16, pady=(0, 12))
        return self.checkbox

    def buttonbox(self):
        box = ttk.Frame(self, padding=12)
        self.confirm_button = ttk.Button(box, text="已确认正常游玩，继续", style="Danger.TButton",
                                         command=self.ok, state="disabled")
        self.confirm_button.pack(side="left", padx=8)
        ttk.Button(box, text="取消并保留备份", command=self.cancel).pack(side="left", padx=8)
        box.pack()
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)

    def _toggle(self):
        self.confirm_button.configure(state="normal" if self.confirmed.get() else "disabled")

    def validate(self):
        return self.confirmed.get()

    def apply(self):
        self.result = True


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
        self.summary = tk.StringVar(value=f"官方资源索引 {VERSION} · 支持 3.7.0 / 3.7.1 核心 · 61.50 GiB")
        self.progress_text = tk.StringVar(value="未下载 UHD 资源")
        self.io_text = tk.StringVar(value="本地复制与校验进度会显示在这里")
        self.elapsed_text = tk.StringVar(value="")
        self.offline = tk.BooleanVar(value=False)
        self.backup = tk.StringVar(value="")
        self.backup_status = tk.StringVar(value="尚未选择备份。只显示本次选择或工具最近一次记录，不扫描其他备份。")
        self._build()
        self.game.trace_add("write", self.path_changed)
        self.root.after(100, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _build(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=INK, font=("Microsoft YaHei UI", 10))
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Danger.TLabel", foreground=DANGER)
        style.configure("Danger.TButton", foreground=DANGER)
        style.map("Danger.TButton", foreground=[("disabled", "#929292"), ("active", "#991b1b")])
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 23, "bold"))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(12, 9))
        style.configure("Primary.TButton", background=ACCENT, foreground="white", borderwidth=0)
        style.map("Primary.TButton", background=[("active", "#1d4ed8"), ("disabled", "#a8b9d8")])
        style.configure("TEntry", padding=8, font=("Microsoft YaHei UI", 10))
        style.configure("Horizontal.TProgressbar", troughcolor="#dce3ee", background=ACCENT, borderwidth=0)
        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="为鸣潮添加 UHD 资源", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text=f"{__version__}  /  官方下载源  /  支持回退", style="Muted.TLabel").pack(anchor="w", pady=(4, 14))
        ttk.Label(outer, text="游戏目录（包含 Client 和 Wuthering Waves.exe）").pack(anchor="w")
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(7, 12))
        self.path_entry = ttk.Entry(row, textvariable=self.game)
        self.path_entry.pack(side="left", fill="x", expand=True)
        self.browse_button = ttk.Button(row, text="选择目录", command=self.browse)
        self.browse_button.pack(side="left", padx=(8, 0))
        card = tk.Frame(outer, bg="white", highlightbackground="#dce3ee", highlightthickness=1, padx=16, pady=13)
        card.pack(fill="x", pady=(0, 12))
        tk.Label(card, textvariable=self.status, bg="white", fg=INK, font=("Microsoft YaHei UI", 12, "bold"),
                 anchor="w", justify="left", wraplength=int(780*self.scale)).pack(fill="x")
        tk.Label(card, textvariable=self.summary, bg="white", fg=MUTED, font=("Microsoft YaHei UI", 10), anchor="w", justify="left", wraplength=int(830*self.scale)).pack(fill="x", pady=(6, 0))
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill="x", pady=(0, 12))
        resources = ttk.Frame(self.tabs, padding=12)
        backups = ttk.Frame(self.tabs, padding=12)
        cleanup = ttk.Frame(self.tabs, padding=12)
        for tab, title in ((resources, "UHD 资源"), (backups, "HD 备份"), (cleanup, "清理与删除")):
            self.tabs.add(tab, text=title)
        actions = ttk.Frame(resources)
        actions.pack(fill="x")
        self.check_button = ttk.Button(actions, text="1. 只读检查", command=lambda: self.start("inspect", not self.offline.get()))
        self.check_button.pack(side="left", padx=(0, 8))
        self.apply_button = ttk.Button(actions, text="2. 下载 / 恢复 UHD", style="Primary.TButton", command=self.confirm_apply)
        self.apply_button.pack(side="left", padx=(0, 8))
        self.rollback_button = ttk.Button(actions, text="回退到 HD", command=self.confirm_rollback)
        self.rollback_button.pack(side="left", padx=(0, 8))
        self.offline_check = ttk.Checkbutton(resources, text="离线检查（不核对服务器当前版本；下载时仍须联网验证）", variable=self.offline)
        self.offline_check.pack(anchor="w", pady=(8, 4))
        secondary = ttk.Frame(resources)
        secondary.pack(fill="x", pady=(5, 10))
        self.wegame_button = ttk.Button(secondary, text="WeGame 启动说明", command=self.show_wegame_instructions)
        self.wegame_button.pack(side="left", padx=(0, 8))
        self.copy_parameter_button = ttk.Button(secondary, text="复制 -krqlv=uhd", command=self.copy_parameter)
        self.copy_parameter_button.pack(side="left")
        ttk.Label(resources, text="资源就绪后，通过 WeGame 添加参数并启动；游戏内继续完成热更新。\n热更新可能包含 UHD 补丁或视频，大小以游戏提示为准。默认保留 HD。",
                  style="Muted.TLabel", justify="left").pack(anchor="w")
        ttk.Label(backups, text="当前 HD 备份批次（可复制路径；并非全盘备份清单）").pack(anchor="w")
        backup_row = ttk.Frame(backups)
        backup_row.pack(fill="x", pady=(5, 6))
        ttk.Entry(backup_row, textvariable=self.backup, state="readonly").pack(side="left", fill="x", expand=True)
        self.select_backup_button = ttk.Button(backup_row, text="选择 / 查看", command=self.select_backup)
        self.select_backup_button.pack(side="left", padx=(8, 0))
        ttk.Label(backups, textvariable=self.backup_status, style="Muted.TLabel", justify="left",
                  wraplength=int(810*self.scale)).pack(anchor="w", pady=(0, 8))
        hd_actions = ttk.Frame(backups)
        hd_actions.pack(fill="x")
        self.remove_hd_button = ttk.Button(hd_actions, text="备份并移除 HD", command=self.confirm_remove_hd)
        self.remove_hd_button.pack(side="left", padx=(0, 8))
        self.restore_hd_button = ttk.Button(hd_actions, text="恢复 HD 备份", command=self.confirm_restore_hd)
        self.restore_hd_button.pack(side="left", padx=(0, 8))
        self.verify_backup_button = ttk.Button(hd_actions, text="只读完整校验", command=self.verify_backup)
        self.verify_backup_button.pack(side="left", padx=(0, 8))
        self.open_backup_button = ttk.Button(hd_actions, text="打开备份目录", command=self.open_backup)
        self.open_backup_button.pack(side="left")
        ttk.Label(backups, text="约 42.57 GiB · 备份优先选其他磁盘；同盘备份不会减少该磁盘总占用。",
                  style="Muted.TLabel").pack(anchor="w", pady=(8, 0))
        ttk.Label(backups, text=WEGAME_HD_WARNING, style="Danger.TLabel",
                  wraplength=int(810*self.scale), justify="left").pack(anchor="w", pady=(8, 0))
        cache_row = ttk.Frame(cleanup)
        cache_row.pack(fill="x", pady=(0, 10))
        self.cache_button = ttk.Button(cache_row, text="打开缓存目录", command=self.open_cache)
        self.cache_button.pack(side="left", padx=(0, 8))
        self.clear_button = ttk.Button(cache_row, text="清理已回退缓存", command=self.confirm_clear)
        self.clear_button.pack(side="left")
        ttk.Separator(cleanup).pack(fill="x", pady=(0, 10))
        delete_row = ttk.Frame(cleanup)
        delete_row.pack(fill="x", pady=(0, 12))
        self.delete_hd_backup_button = ttk.Button(delete_row, text="彻底删除 HD 备份", style="Danger.TButton",
                                                  command=self.confirm_delete_hd_backup)
        self.delete_hd_backup_button.pack(side="left", padx=(0, 10))
        ttk.Label(delete_row, text=DELETE_HD_WARNING, style="Danger.TLabel",
                  wraplength=int(580*self.scale)).pack(side="left")
        ttk.Label(cleanup, text="删除时重新选择具体备份批次，并保留两次确认；不会清理整个备份磁盘。",
                  style="Muted.TLabel").pack(anchor="w")
        ttk.Label(cleanup, text=WEGAME_HD_WARNING, style="Danger.TLabel",
                  wraplength=int(810*self.scale), justify="left").pack(anchor="w", pady=(8, 0))
        self.buttons = [self.check_button, self.apply_button, self.rollback_button,
                        self.wegame_button, self.cache_button, self.clear_button, self.browse_button,
                        self.remove_hd_button, self.restore_hd_button, self.delete_hd_backup_button,
                        self.select_backup_button, self.verify_backup_button, self.open_backup_button,
                        self.copy_parameter_button, self.offline_check]
        self.bar = ttk.Progressbar(outer, maximum=100, mode="determinate")
        self.bar.pack(fill="x")
        ttk.Label(outer, textvariable=self.progress_text, style="Muted.TLabel", wraplength=int(850*self.scale)).pack(anchor="w", pady=(6, 3))
        ttk.Label(outer, textvariable=self.io_text, style="Muted.TLabel", wraplength=int(850*self.scale)).pack(anchor="w", pady=(0, 8))
        log_head = ttk.Frame(outer)
        log_head.pack(fill="x")
        ttk.Label(log_head, text="操作记录").pack(side="left")
        ttk.Label(log_head, textvariable=self.elapsed_text, style="Muted.TLabel").pack(side="left", padx=12)
        self.pause_button = ttk.Button(log_head, text="停止当前操作", command=self.pause, state="disabled")
        self.pause_button.pack(side="right", padx=(8, 0))
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
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 2000:
            self.log.delete("1.0", f"{lines - 2000}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def path_changed(self, *_):
        self.status.set("目录已更换 · 请重新检查")
        self.summary.set("此目录尚未检查，之前的检查结果已清除。")
        self.backup.set("")
        self.backup_status.set("请检查游戏读取最近一次备份记录，或手动选择备份批次。")

    def show_backup(self, result):
        self.backup.set(result["backup_directory"])
        names = {"inventory_ok": "文件名与大小齐全，尚未完整校验内容", "verified": "本次 MD5 / SHA256 全部通过",
                 "incomplete": "资源缺失，不能用于完整恢复", "deleting": "永久删除未完成，不能用于恢复",
                 "deleted": "HD 资源已永久删除，仅保留清单 / 记录", "unavailable": "无法读取或备份记录不匹配"}
        text = names.get(result["status"], result["status"])
        if "file_count" in result:
            text += f" · {result['file_count']} / {result['expected_files']} 个文件 · {result['bytes']/2**30:.2f} GiB"
        self.backup_status.set(text)
        if result.get("error"):
            self.append(result["error"])

    def select_backup(self):
        folder = filedialog.askdirectory(title="选择 HD 备份批次（含 manifest-sha256.json）", parent=self.root)
        if folder:
            self.backup.set(folder)
            self.backup_status.set("尚未检查这份备份")
            self.start("inspect_hd_backup", folder)

    def verify_backup(self):
        if not self.backup.get():
            messagebox.showinfo("先选择备份", "请先“选择 / 查看”具体备份批次。", parent=self.root)
            return
        self.backup_status.set("正在只读校验；完成前不代表内容完整")
        self.start("inspect_hd_backup", self.backup.get(), True)

    def open_backup(self):
        try:
            if not self.backup.get():
                raise ToolError("请先选择备份或检查游戏以读取记录。")
            path = self.manager().hd_resources()._external(self.backup.get())
            if not path.is_dir():
                raise ToolError("备份目录不存在，可能已被移走或删除。")
            os.startfile(path)
        except (OSError, ToolError) as exc:
            messagebox.showinfo("备份目录", str(exc), parent=self.root)

    def copy_parameter(self):
        self.root.clipboard_clear()
        self.root.clipboard_append("-krqlv=uhd")
        self.append("已复制 -krqlv=uhd。请追加到 WeGame 鸣潮启动参数，并保留已有参数。")

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

    def start(self, action, *args):
        if self.busy:
            return
        self.cancel = threading.Event()
        try:
            manager = self.manager()
        except Exception as exc:
            messagebox.showerror("无法开始", str(exc), parent=self.root)
            return
        self.busy = True
        self.action = action
        self.started = time.monotonic()
        self.last_progress = (self.started, 0)
        self.speed = 0.0
        self.io_text.set("正在准备，请等待当前步骤完成")
        self.progress_text.set("尚未完成当前操作")
        if action == "inspect":
            self.summary.set("正在重新检查，之前的检查结果已失效。")
        for button in self.buttons:
            button.configure(state="disabled")
        self.path_entry.configure(state="disabled")
        self.pause_button.configure(state="normal")
        labels = {"inspect": "正在只读检查", "apply": "正在下载 / 校验 UHD", "rollback": "正在回退",
                  "clear_cache": "正在清理缓存", "backup_remove_hd": "正在备份并移除 HD",
                  "restore_hd": "正在恢复 HD 备份", "prepare_delete_hd_backup": "正在只读核对所选 HD 备份",
                  "delete_hd_backup": "正在校验并永久删除所选 HD 备份", "inspect_hd_backup": "正在只读检查备份"}
        self.status.set(labels[action])
        self.append(labels[action])
        self.bar.configure(mode="indeterminate")
        self.bar.start(15)

        def worker():
            try:
                result = getattr(manager, action)(*args)
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
            elif kind == "io_progress":
                self.io_text.set(f"{item['phase']} · 当前文件 {item['current']/2**20:.1f} / {item['total']/2**20:.1f} MiB"
                                 f" · {item['rate']/2**20:.1f} MiB/s\n{item['file']}")
            elif kind == "task_progress":
                self.bar.stop()
                self.bar.configure(mode="determinate")
                self.bar["value"] = 100 * item["current"] / max(1, item["total"])
                self.progress_text.set(f"{item['phase']} · {item['current']} / {item['total']} 个文件\n{item['file']}")
            elif kind == "progress":
                self.bar.stop()
                self.bar.configure(mode="determinate")
                current, total = item["current"], item["total"]
                self.bar["value"] = 100 * current / max(1, total)
                now = time.monotonic()
                last_time, last_bytes = self.last_progress
                if now - last_time >= 1:
                    network = item["network_bytes"]
                    self.speed = max(0, (network - last_bytes) / (now - last_time))
                    self.last_progress = (now, network)
                self.progress_text.set(f"UHD 文件数据（含缓存）{current / 2**30:.2f} / {total / 2**30:.2f} GiB · 接收 {self.speed / 2**20:.1f} MiB/s\n下载完成后还需进行 MD5 校验；复用缓存不计入接收速度。")
            elif kind in {"done", "stopped", "error"}:
                self.bar.stop()
                self.bar.configure(mode="determinate")
                self.busy = False
                for button in self.buttons:
                    button.configure(state="normal")
                self.path_entry.configure(state="normal")
                self.pause_button.configure(state="disabled")
                self.io_text.set("本次操作已结束")
                if self.action in {"apply", "rollback", "clear_cache", "backup_remove_hd", "restore_hd", "delete_hd_backup"}:
                    self.summary.set("资源状态可能已变化，请重新执行只读检查。")
                    self.backup_status.set("操作后需重新查看 / 校验备份，旧的检查结果已失效。")
                if kind == "error":
                    self.status.set("操作已停止 · 请查看原因")
                    self.append(item["text"])
                    if self.action == "inspect_hd_backup":
                        self.backup_status.set("检查未通过，请查看操作记录；未修改备份。")
                    messagebox.showerror("操作已停止", item["text"], parent=self.root)
                elif kind == "stopped":
                    self.status.set("操作已停止 · 请查看操作记录")
                    self.append(item["text"])
                    if self.action == "inspect_hd_backup":
                        self.backup_status.set("只读检查中断，未获得本次完整校验结果。")
                    if self.action == "delete_hd_backup":
                        self.append("永久删除无法撤销。剩余备份文件保留，再次选择同一批次可继续。")
                    if self.action in {"backup_remove_hd", "restore_hd"}:
                        self.append("HD 复制中断时原件与备份保留。若已进入移除 / 恢复阶段，再次选择同一操作可继续。")
                else:
                    self.finish(item["action"], item["result"])
        if self.busy:
            seconds = int(time.monotonic() - self.started)
            self.elapsed_text.set(f"已用时 {seconds//60:02d}:{seconds%60:02d}")
        self.root.after(100, self._poll)

    def finish(self, action, result):
        if action == "inspect":
            names = {"not_installed": "未安装 UHD", "downloading": "存在下载缓存", "ready": "缓存就绪",
                     "installing": "有待恢复的安装", "active": "UHD 已添加", "rolling_back": "有待恢复的回退", "parked": "已回退 / 缓存可复用"}
            self.status.set("检查通过 · " + names.get(result["status"], result["status"]))
            hd = "HD 基础包存在" if result["hd_present"] else "HD 基础包已移除；恢复备份后才能回退"
            online = "在线官方索引匹配" if result["online_verified"] else "离线检查；未核对服务器当前版本"
            self.summary.set(f"客户端 {result['version']} 核心校验通过 · {online}\n{hd} · 磁盘可用 {result['free_bytes']/2**30:.1f} GiB")
            self.append("缓存位置：" + result["cache_directory"])
            if result["hd_backup_directory"]:
                label = "HD 备份已不完整或已进入删除流程：" if result.get("hd_backup_usable") is False else "HD 备份："
                self.append(label + result["hd_backup_directory"])
                self.show_backup(result["hd_backup_details"])
            elif not self.backup.get():
                self.backup_status.set("没有工具生成的备份记录。这不代表磁盘上没有其他备份；可手动选择查看。")
            if result["hd_operation"] in {"removing", "restoring"}:
                self.status.set("有未完成的 HD 操作 · 请继续移除或恢复")
            if result["running"]:
                self.append("下载/回退前需关闭：" + "、".join(result["running"]))
            self.progress_text.set("只读检查完成；未修改游戏文件。")
        elif action == "inspect_hd_backup":
            self.show_backup(result)
            self.status.set("备份只读检查完成 · 请在 HD 备份页查看结果")
            self.progress_text.set("未修改游戏、备份或操作记录。此结果仅代表本次检查时的状态。")
            self.append("备份位置：" + result["backup_directory"])
        elif action == "apply":
            self.status.set("UHD 基础资源已添加 · 请通过 WeGame 启动")
            self.bar["value"] = 100
            self.progress_text.set("100 个文件已校验。原启动方式保留；工具没有自动启动游戏。")
            messagebox.showinfo("资源已添加 · WeGame 启动步骤", "UHD 基础资源已下载并校验，原 HD 与渠道文件保留。\n\n" + WEGAME_LAUNCH_GUIDE, parent=self.root)
        elif action == "rollback":
            self.status.set("已回退 / 无需回退 · 请移除 WeGame 中的 UHD 参数")
            self.progress_text.set("请在 WeGame 移除 -krqlv=uhd 后启动 HD。UHD 缓存保留，需要时可恢复或清理。")
        elif action == "clear_cache":
            self.status.set("缓存已清理 · 原游戏保持不变")
            self.progress_text.set(f"释放约 {result['removed_bytes']/2**30:.2f} GiB。")
        elif action == "backup_remove_hd":
            self.status.set("HD 基础资源已移除 · 请保留 WeGame 的 UHD 参数")
            self.bar["value"] = 100
            space = ("备份在同一磁盘，磁盘总占用基本不变。" if result["same_volume_backup"]
                     else f"游戏磁盘释放约 {result['freed_game_drive_bytes']/2**30:.2f} GiB。")
            self.progress_text.set(space + "恢复备份后才可回退到 HD。")
            self.append("可恢复备份：" + result["backup_directory"])
            self.backup.set(result["backup_directory"])
            self.backup_status.set("备份已生成并在本次移除前完整校验；可在这里只读复查。")
            self.tabs.select(1)
            messagebox.showinfo("HD 备份与移除完成", space + "\n\n备份位置：\n" + result["backup_directory"]
                                + "\n\n请保留 -krqlv=uhd，从 WeGame 启动测试。恢复 HD 时选择这个备份批次文件夹。", parent=self.root)
        elif action == "restore_hd":
            self.backup.set(result["backup_directory"])
            self.backup_status.set("备份保留，恢复过程中已完整校验。")
            self.status.set("HD 备份已恢复并校验 · 现在可以回退 UHD")
            self.bar["value"] = 100
            self.progress_text.set("备份保留。需要切回 HD 时，点击“回退到 HD”，再在 WeGame 移除 -krqlv=uhd。")
        elif action == "prepare_delete_hd_backup":
            self.status.set("备份清单已核对 · 等待两次确认")
            self.progress_text.set("尚未删除任何文件。")
            self.confirm_prepared_backup_delete(result)
        elif action == "delete_hd_backup":
            self.backup.set(result["backup_directory"])
            self.backup_status.set("HD 资源已永久删除；该目录仅保留清单与删除记录。")
            self.status.set("所选 HD 备份资源已永久删除 · 游戏未改动")
            self.bar["value"] = 100
            self.progress_text.set(f"删除 {result['deleted_files']} 个文件，约 {result['deleted_bytes']/2**30:.2f} GiB。此备份已不能用于恢复 HD。")
            self.append("保留清单与删除记录：" + result["backup_directory"])

    def confirm_delete_hd_backup(self):
        if self.busy:
            return
        folder = filedialog.askdirectory(title="选择要永久删除的 HD 备份批次（含 manifest-sha256.json）", parent=self.root)
        if folder:
            self.start("prepare_delete_hd_backup", folder)

    def confirm_prepared_backup_delete(self, plan):
        if HDBackupDeleteDialog(self.root, plan).result is not True:
            self.status.set("已取消删除 · 备份保留")
            return
        text = ("最后确认：你已实际进入 UHD 游戏并确认能正常游玩吗？\n\n"
                f"即将永久删除：\n{plan['backup_directory']}\\HD\n\n"
                f"{plan['file_count']} 个文件，约 {plan['bytes']/2**30:.2f} GiB。\n"
                "删除不进回收站，无法撤销；这份备份将不能再用于恢复或回退 HD。\n\n"
                + WEGAME_HD_WARNING + "\n\n"
                "确认正常运行且不再需要此备份，才选择“是”。")
        if messagebox.askyesno("第二次确认 · 确认正常运行后才删除", text, parent=self.root,
                               icon=messagebox.WARNING, default=messagebox.NO):
            self.start("delete_hd_backup", plan, True)
        else:
            self.status.set("已取消删除 · 备份保留")

    def confirm_apply(self):
        if messagebox.askokcancel("下载并添加 UHD", "将从库洛官方服务器下载约 66.04 GB 基础资源。\n\n默认保留 HD。仅在全部校验通过后添加 UHD 目录，不覆盖 HD、程序或 WeGame 渠道文件，也不自动启动游戏。\n\n游戏内仍可能更新 UHD 补丁和视频，大小以游戏提示为准。可以暂停续传；安装后可以回退。继续？", parent=self.root):
            self.start("apply")

    def confirm_rollback(self):
        if messagebox.askokcancel("回退到 HD", "请先退出游戏。若已移除 HD，请先“恢复 HD 备份”。\n\n工具会检查 HD 完整性，再将 UHD 移回缓存。HD 缺失或校验失败时不会移动 UHD。\n\n回退后请在 WeGame 中移除 -krqlv=uhd，再从 WeGame 启动；工具不会自动修改启动参数。\n\n游戏启动后产生的画质设置、热更新、视频及存档不在回退范围内。继续？", parent=self.root):
            self.start("rollback")

    def confirm_remove_hd(self):
        if self.busy:
            return
        text = (WEGAME_HD_WARNING + "\n\n请先确认 UHD 能正常游玩，然后退出游戏及更新程序。\n\n"
                "工具会先校验 UHD，复制约 45.71 GB 的 HD 基础包并核对 SHA256，全部通过后才移除游戏内的 HD。\n\n"
                "请选择游戏目录之外的备份位置，优先选其他磁盘；备份放在同一磁盘不会节省该磁盘的总空间。"
                "视频和热更新不在本次移除范围内。\n\n移除后继续使用 -krqlv=uhd；切回 HD 前必须恢复备份。继续？")
        if not messagebox.askokcancel("备份并移除 HD 基础资源", text, parent=self.root,
                                     icon=messagebox.WARNING, default=messagebox.CANCEL):
            return
        folder = filedialog.askdirectory(title="选择 HD 备份存放位置（建议其他磁盘）", parent=self.root)
        if folder:
            self.start("backup_remove_hd", folder)

    def confirm_restore_hd(self):
        if self.busy:
            return
        folder = filedialog.askdirectory(title="选择含 manifest-sha256.json 和 HD 的备份批次目录", parent=self.root)
        if folder and messagebox.askokcancel("恢复 HD 备份", "退出游戏后，将验证备份并恢复 Client/Content/HD，约需 45.71 GB 空间。\n\n备份会保留；已有 HD 目录不会被覆盖。恢复完成后，才可执行“回退到 HD”。\n\n备份：" + folder + "\n\n继续？", parent=self.root):
            self.start("restore_hd", folder)

    def confirm_clear(self):
        if messagebox.askokcancel("清理缓存并释放空间", "此操作只清理本工具的下载 / 已回退缓存。\n\n清理后恢复 UHD 需要重新下载；已启用的 UHD 必须先回退。原游戏文件不删除。\n\n确定清理？", parent=self.root):
            self.start("clear_cache")

    def show_wegame_instructions(self):
        messagebox.showinfo("通过 WeGame 启动 UHD", WEGAME_LAUNCH_GUIDE, parent=self.root)

    def pause(self):
        self.cancel.set()
        self.pause_button.configure(state="disabled")
        self.status.set("正在停止 · 等待当前读写安全结束")
        self.append("停止已请求，请等待操作记录。永久删除时已删除的文件无法撤销，其余文件保留。"
                    if self.action == "delete_hd_backup" else "停止已请求；下载缓存或 HD 备份将保留，请等待操作记录。")

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
