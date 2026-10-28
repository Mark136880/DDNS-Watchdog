# -*- coding: utf-8 -*-
"""路径解析：打包成 exe 后以 exe 所在目录为基准。"""

import os
import sys


def base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # ddns_core/paths.py -> 上一级才是项目根目录
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_path():
    return os.path.join(base_dir(), "config.json")


def config_lock_path():
    return os.path.join(base_dir(), "config.lock")


def state_path():
    return os.path.join(base_dir(), "state.json")


def runtime_path():
    """监控进程写的心跳文件，GUI 读它显示运行状态。"""
    return os.path.join(base_dir(), "runtime.json")


def trigger_path():
    """GUI 点「立即检查」时创建，监控进程看到后立刻跑一轮并删除。"""
    return os.path.join(base_dir(), "check.now")


def log_dir():
    return os.path.join(base_dir(), "logs")


def log_path():
    return os.path.join(log_dir(), "ddns.log")
