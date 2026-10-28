#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DDNS-Watchdog 终端启动器（v2）。

只做终端里的事：配置向导、监控运行、运行中菜单。
图形配置请用 launcher_gui.py，两者可同时运行，保存后本进程自动热更新。
"""

import os
import sys
import time

from ddns_core import (APP_NAME, EXIT_AUTH, EXIT_CONFIG, EXIT_OK, EXIT_UNEXPECTED,
                       VERSION, config as config_mod, logging_setup, paths, runtime)
from ddns_core.engine import Monitor, SingleInstance

KEY_HINT = "监控运行中：Q 进入菜单 · 配置界面保存后会自动热更新"


# ---------------------------------------------------------------------------
# 终端输出
# ---------------------------------------------------------------------------

class Ansi(object):
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"
    enabled = False

    @classmethod
    def enable(cls):
        # 统一用 UTF-8 输出，避免 cmd 代码页把中文变成乱码
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        if os.name != "nt":
            cls.enabled = True
            return
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            try:
                kernel32.SetConsoleOutputCP(65001)
            except Exception:
                pass
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
                if kernel32.SetConsoleMode(handle, mode.value | 0x0004):
                    cls.enabled = True
        except Exception:
            cls.enabled = False

    @classmethod
    def paint(cls, text, color):
        return "%s%s%s" % (color, text, cls.RESET) if cls.enabled else text


def ok(text):
    return Ansi.paint("✔ " + text, Ansi.GREEN)


def warn(text):
    return Ansi.paint("! " + text, Ansi.YELLOW)


def err(text):
    return Ansi.paint("✘ " + text, Ansi.RED)


def info(text):
    return Ansi.paint(text, Ansi.CYAN)


def banner():
    print()
    print(info("  ╭──────────────────────────────────────────────╮"))
    print(info("  │  ") + Ansi.paint("%s v%s" % (APP_NAME, VERSION), Ansi.BOLD)
          + info("                       │"))
    print(info("  │  ") + Ansi.paint("终端监控启动器", Ansi.DIM) + info("                             │"))
    print(info("  ╰──────────────────────────────────────────────╯"))
    print()


def print_issues(error, log=None):
    """把 ConfigError 的字段级问题打印成人能看懂的样子。"""
    print()
    print(err(str(error)))
    if getattr(error, "path", None):
        print("    配置文件：%s" % error.path)
    for issue in getattr(error, "issues", []):
        print("    · %s" % issue.as_text())
    if getattr(error, "issues", None):
        print()
        print(Ansi.paint("    用 python launcher_gui.py 打开图形界面修改，"
                         "或运行 --setup 重新配置。", Ansi.DIM))
    if log is not None:
        log.error("%s", error)
        for issue in getattr(error, "issues", []):
            log.error("  · %s", issue.as_text())


# ---------------------------------------------------------------------------
# 配置向导
# ---------------------------------------------------------------------------

def ask(prompt, default="", secret=False):
    shown = default
    if secret and default:
        shown = default[:4] + "*" * 6 + default[-2:] if len(default) > 8 else "****"
    suffix = "（回车保留 %s）: " % shown if default else ": "
    while True:
        try:
            value = input(Ansi.paint(prompt + suffix, Ansi.CYAN)).strip()
        except (EOFError, KeyboardInterrupt):
            raise
        if value:
            return value
        if default:
            return default
        print("    不能为空，请重新输入")


def run_wizard(cfg_path, current=None):
    """交互式配置；校验不通过时逐条打印原因并重来。"""
    cfg = dict(current or config_mod.DEFAULT_CONFIG)
    print()
    print(Ansi.paint("─" * 56, Ansi.DIM))
    print(Ansi.paint("  配置向导", Ansi.BOLD))
    print(Ansi.paint("─" * 56, Ansi.DIM))

    has_old = bool(cfg.get("secret_id") or cfg.get("login_token"))
    if has_old:
        print(Ansi.paint("  检测到已有配置，直接回车可保留原值。", Ansi.DIM))

    while True:
        print()
        print("  认证方式： [1] 腾讯云 API 密钥    [2] DNSPod API 令牌")
        default_choice = "1" if cfg.get("provider") != "dnspod" else "2"
        choice = (input(Ansi.paint("  请选择（回车 = %s）: " % default_choice, Ansi.CYAN))
                  .strip() or default_choice)

        draft = dict(cfg)
        if choice == "2":
            draft["provider"] = "dnspod"
            print(Ansi.paint("  令牌创建：https://console.dnspod.cn/account/token/token",
                             Ansi.DIM))
            old_id, _, old_token = (cfg.get("login_token") or "").partition(",")
            token_id = ask("  令牌 ID", old_id, secret=True)
            token_secret = ask("  令牌 Token", old_token, secret=True)
            draft["login_token"] = "%s,%s" % (token_id, token_secret)
        else:
            draft["provider"] = "tencent"
            print(Ansi.paint("  API 密钥创建：https://console.cloud.tencent.com/cam/capi",
                             Ansi.DIM))
            draft["secret_id"] = ask("  SecretId", cfg.get("secret_id", ""), secret=True)
            draft["secret_key"] = ask("  SecretKey", cfg.get("secret_key", ""), secret=True)

        draft["domain"] = ask("  主域名（如 example.com）", cfg.get("domain", ""))
        draft["sub_domain"] = ask("  主机名（@ 表示根域）", cfg.get("sub_domain", "@"))
        raw_interval = ask("  检查间隔/秒（10~86400）",
                           str(cfg.get("check_interval_seconds", 300)))
        draft["check_interval_seconds"] = raw_interval

        clean, issues = config_mod.validate(draft)
        if not issues:
            break
        print()
        print(err("配置还有 %d 处问题：" % len(issues)))
        for issue in issues:
            print("    · %s" % issue.as_text())
        print()
        retry = input(Ansi.paint("  重新填写？(Y/n) : ", Ansi.CYAN)).strip().lower()
        if retry not in ("", "y", "yes"):
            raise KeyboardInterrupt
        cfg = clean

    saved = config_mod.save(clean, cfg_path, note="%s v%s 终端向导" % (APP_NAME, VERSION))
    print()
    print(ok("配置已保存：%s" % cfg_path))
    print("    认证方式：%s" % ("腾讯云 API 密钥" if saved["provider"] == "tencent"
                           else "DNSPod API 令牌"))
    print("    监控目标：%s" % config_mod.describe_target(saved))
    print("    检查间隔：%d 秒" % saved["check_interval_seconds"])
    return saved


# ---------------------------------------------------------------------------
# 运行中菜单
# ---------------------------------------------------------------------------

def show_status(monitor):
    data = runtime.read() or {}
    print()
    print(info("── 运行状态 " + "─" * 44))
    print("  监控目标：%s" % config_mod.describe_target(monitor.cfg))
    print("  上次检查：%s" % (monitor.last_check_at or "—"))
    print("  上次结果：%s" % {"ok": ok("正常"), "fail": err("取不到 IP"),
                        "error": err("DNS 更新失败")}.get(monitor.last_result, "—"))
    print("  当前 IP：%s" % (monitor.last_ip or "—"))
    print("  检查间隔：%d 秒（上限 %d）" % (monitor.interval, monitor.max_interval))
    print("  配置版本：revision %s" % monitor.cfg.get("revision"))
    if data.get("config_error"):
        print("  " + warn("上次热更新失败：%s" % data["config_error"]))
    print(info("─" * 56))


def settings_menu(monitor, cfg_path):
    """返回 continue / stop / check。"""
    while True:
        print()
        print(info("╭─ 设置菜单（监控已暂停） " + "─" * 32))
        print("│  监控目标：%s" % config_mod.describe_target(monitor.cfg))
        print("│  检查间隔：%d 秒" % monitor.interval)
        print("│")
        print("│  [1] 配置向导（改密钥 / 域名）")
        print("│  [2] 修改检查间隔")
        print("│  [3] 立即执行一次检查")
        print("│  [4] 重新加载配置文件")
        print("│  [5] 显示运行状态")
        print("│  [0] 退出程序")
        print(info("╰" + "─" * 55))
        try:
            choice = input(Ansi.paint("  请选择: ", Ansi.CYAN)).strip()
        except (EOFError, KeyboardInterrupt):
            return "stop"

        if choice == "1":
            try:
                run_wizard(cfg_path, monitor.cfg)
            except (EOFError, KeyboardInterrupt):
                print(warn("已取消"))
            monitor.reload()
        elif choice == "2":
            try:
                raw = input(Ansi.paint("  新的间隔（秒，回车取消）: ", Ansi.CYAN)).strip()
            except (EOFError, KeyboardInterrupt):
                raw = ""
            if not raw:
                continue
            draft = dict(monitor.cfg)
            draft["check_interval_seconds"] = raw
            clean, issues = config_mod.validate(draft)
            if issues:
                for issue in issues:
                    print("    · %s" % issue.as_text())
                continue
            try:
                config_mod.save(clean, cfg_path, note="终端菜单")
            except config_mod.ConfigError as e:
                print_issues(e)
                continue
            monitor.reload()
        elif choice == "3":
            result = monitor.check()
            print("  检查结果：%s" % {"ok": ok("正常"), "fail": err("取不到 IP"),
                                 "error": err("DNS 更新失败")}.get(result, result))
        elif choice == "4":
            monitor.reload()
        elif choice == "5":
            show_status(monitor)
        elif choice == "0":
            return "stop"
        else:
            print(warn("无效选择"))


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def ensure_config(cfg_path, log, force_setup=False):
    """没有配置或凭据不全时进入向导。返回可用的 cfg，或 None 表示用户放弃。"""
    try:
        cfg = config_mod.load(cfg_path)
        incomplete = False
    except config_mod.ConfigError as e:
        incomplete = True
        cfg = None
        if not config_mod.exists(cfg_path):
            print(info("首次运行，先完成配置。"))
        elif e.issues:
            print(warn("现有配置存在 %d 处问题，需要重新填写：" % len(e.issues)))
            for issue in e.issues:
                print("    · %s" % issue.as_text())
        else:
            print_issues(e, log)
        if e.path and os.path.exists(cfg_path) and not e.issues:
            print(warn("将先备份原文件为 config.invalid.json"))
            try:
                os.replace(cfg_path, cfg_path.replace(".json", ".invalid.json"))
            except OSError:
                pass

    if force_setup or incomplete:
        try:
            cfg = run_wizard(cfg_path, cfg)
        except (EOFError, KeyboardInterrupt):
            print()
            print(warn("已取消配置"))
            return None

    if cfg is None:
        print(err("没有可用配置"))
        return None
    return cfg


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    Ansi.enable()

    if "--help" in argv or "-h" in argv:
        print("%s v%s 终端启动器" % (APP_NAME, VERSION))
        print()
        print("用法：python launcher_console.py [选项]")
        print("  --setup     强制进入配置向导")
        print("  --check     只执行一次检查后退出")
        print("  --version   显示版本")
        print("  --once      同 --check")
        print()
        print("图形配置：python launcher_gui.py")
        return EXIT_OK

    if "--version" in argv:
        print("%s v%s" % (APP_NAME, VERSION))
        return EXIT_OK

    log = logging_setup.setup(console=True)
    banner()
    log.info("%s v%s 终端启动器启动（Python %s, frozen=%s）",
             APP_NAME, VERSION, sys.version.split()[0],
             bool(getattr(sys, "frozen", False)))

    cfg_path = paths.config_path()
    print(Ansi.paint("  配置文件：%s" % cfg_path, Ansi.DIM))

    force_setup = "--setup" in argv
    cfg = ensure_config(cfg_path, log, force_setup=force_setup)
    if cfg is None:
        return EXIT_CONFIG

    if "--check" in argv or "--once" in argv:
        monitor = Monitor(cfg, log, cfg_path, keyboard=False)
        result = monitor.check()
        runtime.write(state="stopped", stopped=True, last_result=result,
                      last_ip=monitor.last_ip or "",
                      started_at=monitor.started_at, last_check_at=monitor.last_check_at)
        return EXIT_OK if result == "ok" else EXIT_AUTH if result == "error" else EXIT_CONFIG

    guard = SingleInstance("Global\\%s-Monitor" % APP_NAME)
    if not guard.acquire():
        print()
        print(err("已经有一个监控进程在运行"))
        print("    同一条解析记录只应由一个实例维护，否则两边会互相覆盖。")
        print("    如需查看状态，请运行 python launcher_gui.py")
        return EXIT_CONFIG

    monitor = Monitor(cfg, log, cfg_path, keyboard=True)

    if "--setup" not in argv:
        print()
        print(Ansi.paint("  " + KEY_HINT, Ansi.DIM))
        print()

    try:
        code = monitor.run(on_key=lambda m: settings_menu(m, cfg_path))
    except KeyboardInterrupt:
        print()
        print(warn("已收到中断，正在退出"))
        code = EXIT_OK
    finally:
        guard.release()
    print(ok("已退出"))
    return code


if __name__ == "__main__":
    exit_code = EXIT_OK
    try:
        exit_code = main()
    except KeyboardInterrupt:
        exit_code = EXIT_OK
    except Exception as e:
        exit_code = EXIT_UNEXPECTED
        try:
            logging_setup.setup(console=False).exception("未预期异常：%s", e)
        except Exception:
            pass
        print()
        print(err("发生未预期异常：%s" % e))
        print("    详情已写入 logs/ddns.log")
        if not os.environ.get("DDNS_WATCHDOG_NOPAUSE"):
            try:
                input("\n按回车键退出...")
            except Exception:
                pass
    sys.exit(exit_code)
