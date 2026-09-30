"""GUI entry point; validation modes never apply anything to a real game."""
import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description="鸣潮 3.7 UHD 资源与回退工具")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--self-test", action="store_true", help="仅在临时模拟目录中测试")
    group.add_argument("--ui-smoke-test", action="store_true", help="打开界面后自动关闭，不检测或修改游戏")
    group.add_argument("--inspect", metavar="GAME", help="只读检测游戏与官方清单")
    parser.add_argument("--offline", action="store_true", help="只读检测时不访问网络")
    parser.add_argument("--report", type=Path, help="将验证结果保存为 JSON")
    args = parser.parse_args()
    if args.self_test:
        from wuwa_uhd.selftest import run_tests
        result = run_tests()
    elif args.ui_smoke_test:
        import tkinter as tk
        from wuwa_uhd.gui import App, configure_dpi
        configure_dpi()
        root = tk.Tk()
        app = App(root, auto_detect=False)
        root.update()
        result = {"ok": True, "buttons": len(app.buttons), "game_path_empty": app.game.get() == "",
                  "no_game_operations": True, "window_size": [root.winfo_width(), root.winfo_height()]}
        root.after(1800, root.destroy)
        root.mainloop()
    elif args.inspect:
        from wuwa_uhd.core import Manager
        try:
            result = {"ok": True, "inspection": Manager(args.inspect).inspect(online=not args.offline)}
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
    else:
        from wuwa_uhd.gui import run
        run()
        return 0
    if args.report:
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if sys.stdout:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
