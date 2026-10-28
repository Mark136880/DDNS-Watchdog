# -*- coding: utf-8 -*-
"""运行时心跳：监控进程写，GUI 读，用来判断「终端是否在跑」。

心跳文件只用于显示，丢掉了不影响 DDNS 功能。
"""

import json
import os
import time

from . import VERSION, paths

# 心跳超过这个时间没更新，就认为监控进程已经不在了
STALE_AFTER = 180.0


def _atomic_write(path, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        # 心跳写失败不应影响 DDNS 主流程
        try:
            os.remove(tmp)
        except OSError:
            pass


def write(**fields):
    data = {
        "pid": os.getpid(),
        "version": VERSION,
        "updated_at": time.time(),
    }
    data.update(fields)
    _atomic_write(paths.runtime_path(), data)
    return data


def read():
    path = paths.runtime_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def clear():
    try:
        os.remove(paths.runtime_path())
    except OSError:
        pass


def _pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_alive(data=None):
    """心跳新鲜且进程还在，才算「终端正在运行」。"""
    data = data if data is not None else read()
    if not data:
        return False
    if data.get("stopped"):
        return False
    age = time.time() - float(data.get("updated_at") or 0)
    if age > max(STALE_AFTER, float(data.get("interval") or 0) * 2 + 60):
        return False
    return _pid_alive(data.get("pid"))


def summary(data=None):
    """给 GUI 用的状态摘要。"""
    data = data if data is not None else read()
    if not data:
        return {"state": "stopped", "text": "未运行", "detail": "监控未启动"}
    if is_alive(data):
        last = data.get("last_check_at") or ""
        return {"state": "running", "text": "运行中",
                "detail": "最后检查 %s" % last if last else "正在启动"}
    if data.get("stopped"):
        return {"state": "stopped", "text": "已停止", "detail": "上次运行已退出"}
    return {"state": "stale", "text": "无响应",
            "detail": "心跳超过 %d 秒未更新" % int(STALE_AFTER)}


def request_check():
    """GUI 请求立即检查：创建一个标记文件，监控进程看到后执行并删除。"""
    try:
        with open(paths.trigger_path(), "w", encoding="utf-8") as f:
            f.write(str(time.time()))
        return True
    except OSError:
        return False


def consume_check_request():
    path = paths.trigger_path()
    if not os.path.exists(path):
        return False
    try:
        os.remove(path)
    except OSError:
        pass
    return True
