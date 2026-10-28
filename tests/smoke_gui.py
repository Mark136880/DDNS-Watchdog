# -*- coding: utf-8 -*-
"""界面冒烟测试：构建窗口、渲染、可选截图，然后自动关闭。

不联网、不改配置，只验证控件能否正常创建与布局。
用法：python tests/smoke_gui.py [截图路径] [页面序号] [滚动量]
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    import tkinter as tk

    root = tk.Tk()
    try:
        from ui.app import App
        app = App(root)
    except Exception:
        traceback.print_exc()
        root.destroy()
        return 1

    shot = sys.argv[1] if len(sys.argv) > 1 else ""
    page_index = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    scroll = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    if page_index:
        app.show_page(page_index, animate=False)
    root.update_idletasks()
    root.update()
    if scroll:
        app.pages[page_index].scroll(scroll)
        root.update()

    print("WINDOW_OK title=%r size=%sx%s page=%d" % (
        root.title(), root.winfo_width(), root.winfo_height(), page_index))

    if shot:
        root.after(500, lambda: _capture(root, shot))
        root.after(1300, root.destroy)
    else:
        root.after(500, root.destroy)
    root.mainloop()
    return 0


def _capture(root, path):
    try:
        from PIL import ImageGrab
    except ImportError:
        print("NO_PIL")
        return
    root.attributes("-topmost", True)
    root.lift()
    root.update()
    x = root.winfo_rootx()
    y = root.winfo_rooty()
    w = root.winfo_width()
    h = root.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(path)
    print("SHOT_SAVED %dx%d" % (w, h))


if __name__ == "__main__":
    sys.exit(main())
