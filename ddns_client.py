#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容入口。

v2 起拆成了两个启动器：
    launcher_console.py  终端监控
    launcher_gui.py      图形配置

这个文件保留下来，让旧的「python ddns_client.py」快捷方式继续可用。
"""

import sys

from launcher_console import main

if __name__ == "__main__":
    print("提示：ddns_client.py 已拆分为 launcher_console.py（终端）"
          "与 launcher_gui.py（图形配置），本次自动转调终端启动器。\n")
    sys.exit(main())
