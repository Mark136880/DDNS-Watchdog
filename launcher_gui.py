#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DDNS-Watchdog 图形配置启动器（v2）。

只负责配置：不跑监控、不改 DNS。
保存后若终端监控正在运行，它会检测到 config.json 变化并自动热更新。
"""

import os
import sys


def _enable_dpi_awareness():
    if os.name != "nt":
        return
    try:
        import ctypes
        # PROCESS_SYSTEM_DPI_AWARE：让字体按系统缩放渲染，避免糊
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main():
    if "--version" in sys.argv:
        from ddns_core import APP_NAME, VERSION
        print("%s v%s" % (APP_NAME, VERSION))
        return 0

    try:
        import tkinter as tk
    except ImportError:
        print("当前 Python 没有自带 tkinter，无法打开图形界面。")
        print("可以用 python launcher_console.py 在终端里配置。")
        return 3

    from ddns_core import APP_NAME, VERSION, logging_setup
    from ddns_core.engine import SingleInstance

    log = logging_setup.setup(console=False)
    guard = SingleInstance("Global\\%s-Gui" % APP_NAME)
    if not guard.acquire():
        _enable_dpi_awareness()
        root = tk.Tk()
        root.withdraw()
        from tkinter import messagebox
        messagebox.showinfo(APP_NAME, "配置界面已经在运行了。")
        root.destroy()
        return 0

    _enable_dpi_awareness()
    root = tk.Tk()
    try:
        from ui.app import App
        App(root)
    except Exception as e:  # 构造失败也要让用户看到原因
        log.exception("图形界面初始化失败：%s", e)
        from tkinter import messagebox
        messagebox.showerror(
            "%s v%s 启动失败" % (APP_NAME, VERSION),
            "界面初始化失败：\n%s\n\n详细信息见 logs/ddns.log" % e)
        root.destroy()
        guard.release()
        return 2

    log.info("%s v%s 图形配置界面已打开", APP_NAME, VERSION)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        guard.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
