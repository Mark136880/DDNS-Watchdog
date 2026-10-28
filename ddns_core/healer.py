# -*- coding: utf-8 -*-
"""网络自愈：flushdns + 可选 release/renew。"""

import subprocess
import time


def _run(args, timeout, log, label):
    try:
        completed = subprocess.run(args, capture_output=True, timeout=timeout)
        if completed.returncode == 0:
            log.info("%s 完成 (rc=0)", label)
            return True
        detail = (completed.stderr or b"").decode("mbcs", "ignore").strip()
        log.warning("%s 返回码 %s%s", label, completed.returncode,
                    ("：" + detail) if detail else "")
        return False
    except FileNotFoundError:
        log.error("%s 无法执行：系统里找不到该命令", label)
    except subprocess.TimeoutExpired:
        log.error("%s 超时（%ss）", label, timeout)
    except Exception as e:
        log.error("%s 失败：%s", label, e)
    return False


def heal_network(log, do_renew=True, settle=5, should_stop=None):
    """返回是否有任何一步执行成功。should_stop 用于中途响应退出请求。"""
    log.warning("=== 触发网络自愈 ===")
    healed = _run(["ipconfig", "/flushdns"], 30, log, "flushdns")

    if do_renew:
        log.warning("执行 ipconfig /release + /renew（等同插拔网线，会短暂断网）")
        _run(["ipconfig", "/release"], 60, log, "ipconfig /release")
        if should_stop is None or not should_stop():
            time.sleep(2)
            healed = _run(["ipconfig", "/renew"], 120, log, "ipconfig /renew") or healed
    else:
        log.info("已按配置跳过 release/renew（auto_renew_adapter=false）")

    if settle:
        time.sleep(settle)
    return healed
