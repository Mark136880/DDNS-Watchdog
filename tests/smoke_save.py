# -*- coding: utf-8 -*-
"""保存链路冒烟测试：校验拦截 + 合法配置落盘。

写入的是临时目录里的文件，不会改动真实 config.json，也不联网。
"""

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pump(root, seconds=0.25):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.01)


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    import tkinter as tk
    from ui.app import App

    root = tk.Tk()
    app = App(root)
    tmp = tempfile.mkdtemp(prefix="ddns-save-")
    app.cfg_path = os.path.join(tmp, "config.json")
    failures = []

    def check(name, condition, detail=""):
        print("%-32s %s %s" % (name, "PASS" if condition else "FAIL", detail))
        if not condition:
            failures.append(name)

    page = app.pages[0]
    page.domain.set("example.com")
    page.secret_id.set("AKIDxxxxxxxxxxxxxxxxxxxxxxxx")
    page.secret_key.set("0123456789abcdef0123456789abcdef")
    app.save()
    pump(root, 0.3)
    check("非法域名被拦截", app.banner._visible and not os.path.exists(app.cfg_path))
    check("字段错误已标红", bool(page.domain._error), page.domain._error)
    check("未写入文件", not os.path.exists(app.cfg_path))

    page.domain.set("example.net")
    page.sub_domain.set("home")
    app.save()
    pump(root, 0.4)
    check("合法配置已落盘", os.path.exists(app.cfg_path))
    if os.path.exists(app.cfg_path):
        with open(app.cfg_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        check("域名写入正确", saved.get("domain") == "example.net", saved.get("domain"))
        check("主机名写入正确", saved.get("sub_domain") == "home")
        check("revision 递增", saved.get("revision") == 1, saved.get("revision"))
        check("未保存密钥以外的多余字段", "provider" in saved)
    check("脏标记已清除", not app.dirty)

    root.destroy()
    print("FAILURES=%d" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
