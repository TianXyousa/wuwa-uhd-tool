from __future__ import annotations

import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import __version__
from .core import Cancelled, Manager, ToolError, detect_game

BG = "#f4f6fa"
INK = "#172438"
MUTED = "#59677b"
ACCENT = "#2563eb"
DANGER = "#b91c1c"
DELETE_HD_WARNING = "危险：确认 UHD 能正常运行后再删除！删除后无法从这份备份恢复。"
WEGAME_LAUNCH_GUIDE = (
    "1. 等待 UHD 资源下载并校验完成。\n"
    "2. 打开 WeGame 中鸣潮的启动参数设置，保留原有参数，追加：\n\n"
    "    -krqlv=uhd\n\n"
    "3. 保存后，从 WeGame 启动鸣潮，再在游戏中选择极致画质。\n\n"
    "用户已实测此方式可启动 UHD 并切换极致画质。\n"
    "启动后需完成游戏内更新，本机曾提示约 7.9 GB；视频由游戏另行下载。\n"
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
                "只处理该批次的 HD 基础包，清单与删除记录保留。")
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
        ttk.Label(outer, text="官方下载源  /  HD 备份与恢复  /  支持回退", style="Muted.TLabel").pack(anchor="w", pady=(4, 18))
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
            "资源就绪后，在 WeGame 的鸣潮启动参数中追加 -krqlv=uhd，再从 WeGame 启动并选择极致画质。\n"
            "确认 UHD 可玩后，可备份并移除 HD 基础包；恢复 HD 备份后才可回退。视频由游戏另行下载。"
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
        self.wegame_button = ttk.Button(secondary, text="WeGame 启动说明", command=self.show_wegame_instructions)
        self.wegame_button.pack(side="left", padx=(0, 8))
        self.cache_button = ttk.Button(secondary, text="打开缓存目录", command=self.open_cache)
        self.cache_button.pack(side="left", padx=(0, 8))
        self.clear_button = ttk.Button(secondary, text="清理已回退缓存", command=self.confirm_clear)
        self.clear_button.pack(side="left")
        hd_actions = ttk.Frame(outer)
        hd_actions.pack(fill="x", pady=(0, 12))
        self.remove_hd_button = ttk.Button(hd_actions, text="备份并移除 HD", command=self.confirm_remove_hd)
        self.remove_hd_button.pack(side="left", padx=(0, 8))
        self.restore_hd_button = ttk.Button(hd_actions, text="恢复 HD 备份", command=self.confirm_restore_hd)
        self.restore_hd_button.pack(side="left")
        ttk.Label(hd_actions, text="仅基础包 · 约 42.57 GiB · 备份请优先选其他磁盘", style="Muted.TLabel").pack(side="left", padx=10)
        delete_row = ttk.Frame(outer)
        delete_row.pack(fill="x", pady=(0, 12))
        self.delete_hd_backup_button = ttk.Button(delete_row, text="彻底删除 HD 备份", style="Danger.TButton",
                                                  command=self.confirm_delete_hd_backup)
        self.delete_hd_backup_button.pack(side="left", padx=(0, 10))
        ttk.Label(delete_row, text=DELETE_HD_WARNING, style="Danger.TLabel",
                  wraplength=int(580*self.scale)).pack(side="left")
        self.buttons = [self.check_button, self.apply_button, self.rollback_button,
                        self.wegame_button, self.cache_button, self.clear_button, self.browse_button,
                        self.remove_hd_button, self.restore_hd_button, self.delete_hd_backup_button]
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
        for button in self.buttons:
            button.configure(state="disabled")
        self.path_entry.configure(state="disabled")
        self.pause_button.configure(state="normal" if action in {"apply", "inspect", "backup_remove_hd", "restore_hd",
                                                                 "prepare_delete_hd_backup", "delete_hd_backup"} else "disabled")
        labels = {"inspect": "正在只读检查", "apply": "正在下载 / 校验 UHD", "rollback": "正在回退",
                  "clear_cache": "正在清理缓存", "backup_remove_hd": "正在备份并移除 HD",
                  "restore_hd": "正在恢复 HD 备份", "prepare_delete_hd_backup": "正在只读核对所选 HD 备份",
                  "delete_hd_backup": "正在校验并永久删除所选 HD 备份"}
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
                    self.status.set("操作已停止 · 请查看操作记录")
                    self.append(item["text"])
                    if self.action == "delete_hd_backup":
                        self.append("永久删除无法撤销。剩余备份文件保留，再次选择同一批次可继续。")
                    if self.action in {"backup_remove_hd", "restore_hd"}:
                        self.append("HD 复制中断时原件与备份保留。若已进入移除 / 恢复阶段，再次选择同一操作可继续。")
                else:
                    self.finish(item["action"], item["result"])
        self.root.after(100, self._poll)

    def finish(self, action, result):
        if action == "inspect":
            names = {"not_installed": "未安装 UHD", "downloading": "存在下载缓存", "ready": "缓存就绪",
                     "installing": "有待恢复的安装", "active": "UHD 已添加", "rolling_back": "有待恢复的回退", "parked": "已回退 / 缓存可复用"}
            self.status.set("检查通过 · " + names.get(result["status"], result["status"]))
            hd = "HD 基础包存在" if result["hd_present"] else "HD 基础包已移除；恢复备份后才能回退"
            self.summary.set(f"客户端 3.7.0 核心校验通过 · 官方清单匹配\n{hd} · 磁盘可用 {result['free_bytes']/2**30:.1f} GiB")
            self.append("缓存位置：" + result["cache_directory"])
            if result["hd_backup_directory"]:
                label = "HD 备份已不完整或已进入删除流程：" if result.get("hd_backup_usable") is False else "HD 备份："
                self.append(label + result["hd_backup_directory"])
            if result["hd_operation"] in {"removing", "restoring"}:
                self.status.set("有未完成的 HD 操作 · 请继续移除或恢复")
            if result["running"]:
                self.append("下载/回退前需关闭：" + "、".join(result["running"]))
            self.progress_text.set("只读检查完成；未修改游戏文件。")
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
            messagebox.showinfo("HD 备份与移除完成", space + "\n\n备份位置：\n" + result["backup_directory"]
                                + "\n\n请保留 -krqlv=uhd，从 WeGame 启动测试。恢复 HD 时选择这个备份批次文件夹。", parent=self.root)
        elif action == "restore_hd":
            self.status.set("HD 备份已恢复并校验 · 现在可以回退 UHD")
            self.bar["value"] = 100
            self.progress_text.set("备份保留。需要切回 HD 时，点击“回退到 HD”，再在 WeGame 移除 -krqlv=uhd。")
        elif action == "prepare_delete_hd_backup":
            self.status.set("备份清单已核对 · 等待两次确认")
            self.progress_text.set("尚未删除任何文件。")
            self.confirm_prepared_backup_delete(result)
        elif action == "delete_hd_backup":
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
                "确认正常运行且不再需要此备份，才选择“是”。")
        if messagebox.askyesno("第二次确认 · 确认正常运行后才删除", text, parent=self.root,
                               icon=messagebox.WARNING, default=messagebox.NO):
            self.start("delete_hd_backup", plan, True)
        else:
            self.status.set("已取消删除 · 备份保留")

    def confirm_apply(self):
        if messagebox.askokcancel("下载并添加 UHD", "将从库洛官方服务器下载约 66.04 GB 基础资源。\n\n仅在全部校验通过后添加 UHD 目录，不覆盖 HD、程序或 WeGame 渠道文件，也不自动启动游戏。\n\n可以暂停续传；安装后可以回退。继续？", parent=self.root):
            self.start("apply")

    def confirm_rollback(self):
        if messagebox.askokcancel("回退到 HD", "请先退出游戏。若已移除 HD，请先“恢复 HD 备份”。\n\n工具会检查 HD 完整性，再将 UHD 移回缓存。HD 缺失或校验失败时不会移动 UHD。\n\n回退后请在 WeGame 中移除 -krqlv=uhd，再从 WeGame 启动；工具不会自动修改启动参数。\n\n游戏启动后产生的画质设置、热更新、视频及存档不在回退范围内。继续？", parent=self.root):
            self.start("rollback")

    def confirm_remove_hd(self):
        if self.busy:
            return
        if not messagebox.askokcancel("备份并移除 HD 基础资源", "请先确认 UHD 能正常游玩，然后退出游戏及更新程序。\n\n工具会先校验 UHD，复制约 45.71 GB 的 HD 基础包并核对 SHA256，全部通过后才移除游戏内的 HD。\n\n请选择游戏目录之外的备份位置，优先选其他磁盘；备份放在同一磁盘不会节省该磁盘的总空间。视频和热更新不在本次移除范围内。\n\n移除后继续使用 -krqlv=uhd；切回 HD 前必须恢复备份。继续？", parent=self.root):
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
