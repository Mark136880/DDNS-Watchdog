# -*- coding: utf-8 -*-
"""日志：控制台 + 轮转文件。"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from . import APP_NAME, paths


def setup(console=True, level=logging.DEBUG):
    """幂等初始化，多次调用不会重复挂 handler。"""
    os.makedirs(paths.log_dir(), exist_ok=True)
    log = logging.getLogger(APP_NAME)
    log.setLevel(level)
    log.propagate = False
    for handler in list(log.handlers):
        log.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_handler = RotatingFileHandler(
        paths.log_path(), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    log.addHandler(file_handler)

    if console:
        stream = logging.StreamHandler(sys.stdout)
        stream.setLevel(logging.INFO)
        try:
            stream.stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        stream.setFormatter(fmt)
        log.addHandler(stream)
    return log


def get_logger():
    return logging.getLogger(APP_NAME)


def tail_lines(count=20, max_bytes=64 * 1024):
    """读取日志末尾若干行，供 GUI 状态页展示。"""
    path = paths.log_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read().decode("utf-8", "ignore")
    except OSError:
        return []
    lines = [line for line in data.splitlines() if line.strip()]
    return lines[-count:]
