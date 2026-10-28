# -*- coding: utf-8 -*-
"""监控循环：检测 → 更新 → 退避 → 等待，等待期间响应热更新。"""

import os
import time

from . import EXIT_OK, paths, runtime
from . import config as config_mod
from .detector import IPDetector
from .healer import heal_network
from .providers import apply_record, auth_hints, find_record

try:
    import msvcrt
except ImportError:
    msvcrt = None


class SingleInstance(object):
    """同名互斥体，防止两个监控进程同时改同一条记录。"""

    def __init__(self, name):
        self.name = name
        self._handle = None

    def acquire(self):
        if os.name != "nt":
            return True
        import ctypes
        ERROR_ALREADY_EXISTS = 183
        kernel32 = ctypes.windll.kernel32
        self._handle = kernel32.CreateMutexW(None, False, self.name)
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            self.release()
            return False
        return True

    def release(self):
        if self._handle:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None


class Monitor(object):
    """一次 run() 就是一台常驻的 DDNS 监控。"""

    def __init__(self, cfg, log, cfg_path=None, keyboard=True):
        self.log = log
        self.cfg_path = cfg_path or paths.config_path()
        self.keyboard = keyboard
        self.cfg = cfg
        self.detector = IPDetector(cfg, log)
        self.state_path = paths.state_path()
        self.state = self._load_state()
        self.interval = int(cfg["check_interval_seconds"])
        self.max_interval = int(cfg["max_interval_seconds"])
        self.heal_after = max(1, int(cfg["heal_after_failures"]))
        self.fail_streak = 0
        self.started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self.last_check_at = ""
        self.last_result = ""
        self.last_ip = self.state.get("last_ip") or ""
        self.last_source = ""
        self._stop = False
        self._fp = config_mod.fingerprint(self.cfg_path)
        self._config_error = ""

    # -- 状态 -------------------------------------------------------------
    def _load_state(self):
        import json
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except (OSError, ValueError):
                self.log.warning("state.json 损坏，已忽略")
        return {"last_ip": None}

    def _save_state(self):
        import json
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f)
            os.replace(tmp, self.state_path)
        except OSError as e:
            self.log.warning("状态文件写入失败：%s", e)

    def _heartbeat(self, next_check_at="", **extra):
        runtime.write(
            state="running",
            started_at=self.started_at,
            last_check_at=self.last_check_at,
            next_check_at=next_check_at,
            last_result=self.last_result,
            last_ip=self.last_ip or "",
            last_source=self.last_source,
            config_revision=int(self.cfg.get("revision") or 0),
            config_error=self._config_error,
            interval=self.interval,
            dryrun=bool(os.environ.get("DDNS_WATCHDOG_DRYRUN")),
            **extra
        )

    # -- 单轮检查 ---------------------------------------------------------
    def check(self):
        """执行一轮。返回 ok / fail / error。"""
        cfg = self.cfg
        ip, source = self.detector.get_ip()
        self.last_check_at = time.strftime("%Y-%m-%d %H:%M:%S")
        if not ip:
            self.log.error("本轮所有检测源均失败，拿不到公网 IP")
            self.log.error("提示：可检查网卡是否正常、是否需要代理；"
                           "连续失败会触发网络自愈")
            self.last_result = "fail"
            return "fail"

        known = self.state.get("last_ip")
        if ip == known:
            self.last_ip, self.last_source = ip, source
            return self._verify_existing(ip, source)

        confirmed, confirm_source = self._confirm(ip)
        if confirmed is None:
            self.last_result = "error"
            return "error"
        if confirmed != ip:
            self.log.warning("检测源结果不一致（%s / %s），采用多数结果 %s",
                             ip, confirmed, confirmed)
            ip, source = confirmed, confirm_source

        self.log.info("检测到公网 IP 变化：%s -> %s（来源：%s）",
                      known or "未知", ip, source)
        ok, action, message, hints = apply_record(
            cfg, ip, dryrun=bool(os.environ.get("DDNS_WATCHDOG_DRYRUN")))
        self.log.info(message) if ok else self.log.error(message)
        for hint in hints:
            self.log.error("  · %s", hint)
        if ok:
            self.state["last_ip"] = ip
            self._save_state()
            self.last_ip, self.last_source = ip, source
            self.last_result = "ok"
            return "ok"
        self.last_result = "error"
        return "error"

    def _confirm(self, first_ip):
        """用第二个（必要时第三个）源做多数确认，避免单源投毒。"""
        samples = [first_ip]
        sources = []
        for _ in range(2):
            ip, source = self.detector.get_ip()
            if not ip:
                break
            samples.append(ip)
            sources.append(source)
            if samples.count(ip) >= 2:
                break
        if not sources:
            self.log.warning("第二个检测源不可用，本轮按单源结果继续")
            return first_ip, ""
        counts = {}
        for ip in samples:
            counts[ip] = counts.get(ip, 0) + 1
        best = max(counts.items(), key=lambda kv: kv[1])
        if best[1] == 1:
            self.log.warning("多个检测源结果互不相同（%s），暂以第一个为准",
                             " / ".join(samples))
            return first_ip, ""
        if best[0] != first_ip:
            return best[0], sources[-1]
        return first_ip, ""

    def _verify_existing(self, ip, source):
        state, payload, message = find_record(self.cfg)
        if state == "error":
            self.log.error("DNS 记录核对失败：%s", message)
            for hint in auth_hints(message):
                self.log.error("  · %s", hint)
            self.last_result = "error"
            return "error"
        if state == "missing":
            self.log.warning("云端记录不存在，重新创建")
            ok, _action, msg, hints = apply_record(self.cfg, ip)
            self.log.info(msg) if ok else self.log.error(msg)
            for hint in hints:
                self.log.error("  · %s", hint)
            self.last_result = "ok" if ok else "error"
            return self.last_result
        if payload[2] != ip:
            self.log.warning("检测到 DNS 记录漂移：%s -> %s，自动修复", payload[2], ip)
            ok, _action, msg, hints = apply_record(self.cfg, ip)
            self.log.info(msg) if ok else self.log.error(msg)
            for hint in hints:
                self.log.error("  · %s", hint)
            self.last_result = "ok" if ok else "error"
            return self.last_result
        self.log.info("公网 IP 未变化（当前：%s | 来源：%s），DNS 正常", ip, source)
        self.last_result = "ok"
        return "ok"

    # -- 配置热更新 -------------------------------------------------------
    def reload(self, quiet=False):
        """重新读取配置；失败时保留旧配置并打印字段级原因。"""
        try:
            new_cfg = config_mod.load(self.cfg_path)
        except config_mod.ConfigError as e:
            self._config_error = str(e)
            self.log.error("配置热更新失败，继续使用原配置")
            for line in e.detail_lines():
                self.log.error("%s", line)
            self._fp = config_mod.fingerprint(self.cfg_path)
            return False

        self._config_error = ""
        self._apply(new_cfg)
        return True

    def _apply(self, new_cfg):
        old_target = config_mod.describe_target(self.cfg)
        new_target = config_mod.describe_target(new_cfg)
        old_sources = (list(self.cfg.get("stun_servers") or []),
                       list(self.cfg.get("http_sources") or []))
        new_sources = (list(new_cfg.get("stun_servers") or []),
                       list(new_cfg.get("http_sources") or []))

        self.cfg = new_cfg
        if old_sources != new_sources:
            self.detector.configure(new_cfg)
            self.log.info("检测源已更新（STUN %d 个 / HTTP %d 个）",
                          len(new_cfg["stun_servers"]), len(new_cfg["http_sources"]))

        self.interval = int(new_cfg["check_interval_seconds"])
        self.max_interval = int(new_cfg["max_interval_seconds"])
        self.heal_after = max(1, int(new_cfg["heal_after_failures"]))
        self.fail_streak = 0
        self._fp = config_mod.fingerprint(self.cfg_path)

        if old_target != new_target:
            # 换了域名，旧 IP 缓存不再代表新目标，强制下一轮写入
            self.state["last_ip"] = None
            self._save_state()
            self.log.warning("监控目标变更：%s -> %s", old_target, new_target)
        self.log.info("配置已热更新（revision %s，%s）：%s | 间隔 %ds | 自愈阈值 %d",
                      new_cfg.get("revision"), new_cfg.get("updated_at") or "未知时间",
                      new_target, self.interval, self.heal_after)

    def config_changed(self):
        return config_mod.fingerprint(self.cfg_path) != self._fp

    # -- 等待 -------------------------------------------------------------
    def wait(self, seconds):
        """返回 timeout / key / config / trigger / stop。"""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self._stop:
                return "stop"
            if runtime.consume_check_request():
                self.log.info("收到「立即检查」请求")
                return "trigger"
            if self.config_changed():
                return "config"
            if self.keyboard and msvcrt is not None:
                try:
                    if msvcrt.kbhit():
                        ch = msvcrt.getwch()
                        if ch.lower() in ("q", "s"):
                            return "key"
                except Exception:
                    self.keyboard = False
            time.sleep(0.15)
        return "timeout"

    def request_stop(self):
        self._stop = True

    # -- 主循环 -----------------------------------------------------------
    def run(self, on_key=None):
        target = config_mod.describe_target(self.cfg)
        self.log.info("监控：%s | 检查间隔 %ds | 自愈阈值 %d",
                      target, self.interval, self.heal_after)
        if os.environ.get("DDNS_WATCHDOG_DRYRUN"):
            self.log.warning("[DRYRUN] 测试模式已开启，不会真的修改 DNS 记录")

        self._heartbeat()
        try:
            while not self._stop:
                result = self.check()
                if result == "fail":
                    self.fail_streak += 1
                    self.log.error("第 %d 轮：所有检测源均失败，无法获取公网 IP",
                                   self.fail_streak)
                    if self.fail_streak >= self.heal_after:
                        heal_network(self.log, self.cfg.get("auto_renew_adapter", True),
                                     should_stop=lambda: self._stop)
                        self.fail_streak = 0
                    self.interval = min(int(self.interval * 1.5) + 10, self.max_interval)
                    self.log.info("退避至 %ds 后重试", self.interval)
                    if self.config_changed():
                        self.reload()
                else:
                    self.fail_streak = 0
                    self.interval = int(self.cfg["check_interval_seconds"])

                next_check = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(time.time() + self.interval))
                self._heartbeat(next_check_at=next_check)

                reason = self.wait(self.interval)
                if reason == "stop":
                    break
                if reason == "key":
                    if on_key is None:
                        continue
                    action = on_key(self)
                    if action == "stop":
                        break
                    if action == "check":
                        continue
                elif reason == "config":
                    if self.reload():
                        self.log.info("立即按新配置执行一轮检查")
                    continue
                elif reason == "trigger":
                    continue
        finally:
            runtime.write(state="stopped", stopped=True, started_at=self.started_at,
                          last_check_at=self.last_check_at, last_result=self.last_result,
                          last_ip=self.last_ip or "", config_revision=int(
                              self.cfg.get("revision") or 0))
        return EXIT_OK
