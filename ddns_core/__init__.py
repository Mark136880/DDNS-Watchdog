#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DDNS-Watchdog 核心包。

两个启动器共用这里的实现：
    launcher_console.py  终端监控启动器
    launcher_gui.py      图形配置启动器
"""

APP_NAME = "DDNS-Watchdog"
VERSION = "2.0.1"
UA = "%s/%s" % (APP_NAME, VERSION)

# 退出码：便于脚本/任务计划区分失败原因
EXIT_OK = 0
EXIT_UNEXPECTED = 2
EXIT_CONFIG = 3
EXIT_AUTH = 4

__all__ = ["APP_NAME", "VERSION", "UA", "EXIT_OK", "EXIT_UNEXPECTED",
           "EXIT_CONFIG", "EXIT_AUTH"]
