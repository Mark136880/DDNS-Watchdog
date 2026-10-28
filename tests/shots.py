# -*- coding: utf-8 -*-
"""生成仓库首页用的界面截图。

用临时目录里的示例配置渲染，避免把真实域名与密钥带进截图。
输出：docs/screenshot-*.png
"""

import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SAMPLE = {
    "schema_version": 2,
    "provider": "tencent",
    "secret_id": "AKIDEXAMPLE0000000000000000000000",
    "secret_key": "EXAMPLEKEY0000000000000000000000",
    "login_token": "",
    "domain": "home.example.net",
    "sub_domain": "@",
    "check_interval_seconds": 300,
    "max_interval_seconds": 1800,
    "heal_after_failures": 1,
    "auto_renew_adapter": True,
    "stun_servers": ["stun.qq.com:3478", "stun.miwifi.com:3478",
                     "stun.cloudflare.com:3478"],
    "http_sources": ["https://4.ipw.cn", "https://ip.3322.net",
                     "http://members.3322.org/dyndns/getip"],
    "revision": 3,
    "updated_at": "2026-10-28 20:10:00",
    "updated_by": "图形配置界面",
}


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    tmp = tempfile.mkdtemp(prefix="ddns-shots-")
    with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as f:
        json.dump(SAMPLE, f, ensure_ascii=False, indent=2)

    from ddns_core import paths
    paths.base_dir = lambda: tmp

    import tkinter as tk
    from PIL import Image, ImageGrab
    from ui.app import App

    out_dir = os.path.join(ROOT, "docs")
    os.makedirs(out_dir, exist_ok=True)

    root = tk.Tk()
    app = App(root)
    root.update_idletasks()
    root.update()

    shots = [(0, "screenshot-connection.png"), (1, "screenshot-detection.png"),
             (2, "screenshot-heal.png"), (3, "screenshot-status.png")]
    for index, name in shots:
        app.show_page(index, animate=False)
        root.update_idletasks()
        root.update()
        time.sleep(0.25)
        root.update()
        root.attributes("-topmost", True)
        root.lift()
        root.update()
        x, y = root.winfo_rootx(), root.winfo_rooty()
        w, h = root.winfo_width(), root.winfo_height()
        image = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        if image.width > 1200:
            ratio = 1200.0 / image.width
            image = image.resize((1200, int(image.height * ratio)), Image.LANCZOS)
        path = os.path.join(out_dir, name)
        image.save(path, optimize=True)
        print("saved %s %s" % (name, image.size))

    root.destroy()
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
