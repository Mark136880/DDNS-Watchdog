#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DDNS-Watchdog —— 健壮的 Windows DDNS 更新工具（DNSPod/腾讯云版）

特性：
1. 多源公网 IP 检测：STUN 协议（UDP，不走 HTTP/DNS 解析链路）+ 多个 HTTP 接口，
   一个源失败立即换下一个，并记录失败源进行动态降权。
2. 指数退避重试：网络抖动时逐步拉长检查间隔，避免刷屏式请求。
3. 网络自愈：所有检测源连续失败达到阈值时，自动 flushdns，
   可选自动 ipconfig /release + /renew（等同插拔网线），然后重新检测。
4. 状态缓存：IP 没变不打扰 DNS API；本地记录与 DNS 记录 drift 时自动修复。
5. 完整日志：logs/ddns.log + 控制台输出。
"""

import hashlib
import hmac
import json
import logging
import os
import random
import socket
import struct
import subprocess
import sys
import time
try:
    import msvcrt  # Windows 控制台按键检测
except ImportError:
    msvcrt = None
import urllib.error
import urllib.parse
import urllib.request

APP_NAME = "DDNS-Watchdog"
VERSION = "1.0.0"
UA = "%s/%s" % (APP_NAME, VERSION)

DEFAULT_HTTP_SOURCES = [
    # 纯 IPv4 源优先（双栈网络下这些接口只会返回 IPv4）
    "https://4.ipw.cn",
    "https://ip.3322.net",
    "http://members.3322.org/dyndns/getip",
    "https://api-ipv4.ip.sb/ip",
    "http://ipv4.icanhazip.com",
    # 以下源双栈下可能返回 IPv6，解析器会自动丢弃，仅作兜底
    "https://myip.ipip.net",
    "https://api.ipify.org",
]

# DDNS 必须探测本机真实公网 IP，强制不走系统代理直连
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

DEFAULT_STUN_SERVERS = [
    "stun.qq.com:3478",
    "stun.miwifi.com:3478",
    "stun.cloudflare.com:3478",
]

DEFAULT_CONFIG = {
    # 认证方式: "tencent"=腾讯云API密钥(SecretId/SecretKey) | "dnspod"=DNSPod API令牌
    "provider": "tencent",
    # 腾讯云 API 密钥 (https://console.cloud.tencent.com/cam/capi)
    "secret_id": "",
    "secret_key": "",
    # DNSPod API 令牌，格式 "ID,Token"，在
    # https://console.dnspod.cn/account/token/token 创建
    "login_token": "",
    "domain": "example.com",
    "sub_domain": "@",
    # 检查间隔（秒）
    "check_interval_seconds": 300,
    # 失败后的最大重试间隔（秒），指数退避上限
    "max_interval_seconds": 1800,
    # 所有检测源连续失败多少轮后触发网络自愈
    "heal_after_failures": 1,
    # 自愈时是否执行 ipconfig /release + /renew（等同插拔网线，期间会短暂断网几秒）
    "auto_renew_adapter": True,
    # STUN 服务器（UDP 检测公网 IP，最抗 DNS/HTTP 故障）
    "stun_servers": DEFAULT_STUN_SERVERS,
    # HTTP 检测接口（按顺序尝试）
    "http_sources": DEFAULT_HTTP_SOURCES,
}

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def is_valid_ipv4(s):
    if not isinstance(s, str):
        return False
    parts = s.strip().split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not p.isdigit() or not 0 <= int(p) <= 255:
            return False
    return True


def is_usable_ip(ip):
    """排除私网/保留地址，防止拿到内网或 CDN 出口 IP"""
    if not is_valid_ipv4(ip):
        return False
    a, b = (int(x) for x in ip.split(".")[:2])
    if a == 10 or a == 127 or a == 0:
        return False
    if a == 172 and 16 <= b <= 31:
        return False
    if a == 192 and b == 168:
        return False
    if a == 169 and b == 254:
        return False
    if a == 100 and 64 <= b <= 127:
        return False
    return True

# ---------------------------------------------------------------------------
# STUN 检测（RFC 5389 精简实现，UDP 直取公网 IP）
# ---------------------------------------------------------------------------

def stun_query(server, port=3478, timeout=5):
    """向 STUN 服务器发送 Binding Request，返回映射后的公网 IPv4，失败返回 None"""
    tid = os.urandom(12)
    msg = struct.pack(">HHI", 0x0001, 0, 0x2112A442) + tid
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(msg, (server, port))
        data, _ = s.recvfrom(2048)
    except (OSError, socket.timeout):
        return None
    finally:
        s.close()
    if len(data) < 20:
        return None
    msg_type, _msg_len, magic = struct.unpack(">HHI", data[:8])
    if msg_type != 0x0101 or magic != 0x2112A442:
        return None
    offset = 20
    while offset + 4 <= len(data):
        attr_type, attr_len = struct.unpack(">HH", data[offset:offset + 4])
        val = data[offset + 4:offset + 4 + attr_len]
        if attr_type == 0x0020 and len(val) >= 8:  # XOR-MAPPED-ADDRESS
            port_x = struct.unpack(">H", val[2:4])[0] ^ 0x2112
            ip_x = struct.unpack(">I", val[4:8])[0] ^ 0x2112A442
            return socket.inet_ntoa(struct.pack(">I", ip_x))
        if attr_type == 0x0001 and len(val) >= 8:  # MAPPED-ADDRESS
            return socket.inet_ntoa(val[4:8])
        offset += 4 + attr_len + ((4 - attr_len % 4) % 4)
    return None

# ---------------------------------------------------------------------------
# HTTP 检测
# ---------------------------------------------------------------------------

def http_query(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            text = resp.read(256).decode("utf-8", "ignore")
    except Exception:
        return None
    for token in text.replace(",", " ").split():
        if is_usable_ip(token):
            return token
    return None

# ---------------------------------------------------------------------------
# 多源检测（带失败降权记忆）
# ---------------------------------------------------------------------------

class IPDetector:
    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self.stun_servers = list(cfg.get("stun_servers") or DEFAULT_STUN_SERVERS)
        self.http_sources = list(cfg.get("http_sources") or DEFAULT_HTTP_SOURCES)
        # 失败计数：失败多的源排到队尾
        self.fail_cnt = {}

    def _rank(self, sources):
        return sorted(sources, key=lambda s: (self.fail_cnt.get(s, 0), random.random()))

    def get_ip(self):
        """依次尝试 STUN 与 HTTP 源，返回 (ip, source) 或 (None, None)"""
        for entry in self._rank(self.stun_servers):
            host, _, port = entry.partition(":")
            ip = stun_query(host, int(port or 3478))
            if is_usable_ip(ip):
                self.fail_cnt[entry] = 0
                return ip, "STUN:" + entry
            self.fail_cnt[entry] = self.fail_cnt.get(entry, 0) + 1
            self.log.debug("STUN 源失败: %s", entry)
        for url in self._rank(self.http_sources):
            ip = http_query(url)
            if is_usable_ip(ip):
                self.fail_cnt[url] = 0
                return ip, "HTTP:" + url
            self.fail_cnt[url] = self.fail_cnt.get(url, 0) + 1
            self.log.debug("HTTP 源失败: %s", url)
        return None, None

# ---------------------------------------------------------------------------
# 腾讯云 API 3.0（dnspod 产品，TC3-HMAC-SHA256 签名）
# ---------------------------------------------------------------------------

def tencent_api(action, params, secret_id, secret_key, timeout=15):
    service = "dnspod"
    host = "dnspod.tencentcloudapi.com"
    payload = json.dumps(params, separators=(",", ":"), ensure_ascii=False)
    ts = int(time.time())
    date = time.strftime("%Y-%m-%d", time.gmtime(ts))

    # 1. 拼接规范请求串
    canonical_headers = ("content-type:application/json; charset=utf-8\n"
                         "host:%s\nx-tc-action:%s\n" % (host, action.lower()))
    signed_headers = "content-type;host;x-tc-action"
    hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    canonical_request = "\n".join(
        ["POST", "/", "", canonical_headers, signed_headers, hashed_payload])

    # 2. 拼接待签名串
    scope = "%s/%s/tc3_request" % (date, service)
    hashed_canonical = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = "\n".join(["TC3-HMAC-SHA256", str(ts), scope, hashed_canonical])

    # 3. 计算签名
    def _hmac(key, msg):
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()
    k_signing = _hmac(_hmac(_hmac(("TC3" + secret_key).encode("utf-8"), date), service),
                      "tc3_request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"),
                         hashlib.sha256).hexdigest()

    # 4. 发送请求
    headers = {
        "Authorization": ("TC3-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, "
                          "Signature=%s" % (secret_id, scope, signed_headers, signature)),
        "Content-Type": "application/json; charset=utf-8",
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Version": "2021-03-23",
        "X-TC-Timestamp": str(ts),
    }
    req = urllib.request.Request(
        "https://%s/" % host, data=payload.encode("utf-8"), headers=headers)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
    r = json.loads(body)
    err = (r.get("Response") or {}).get("Error")
    if err:
        raise RuntimeError("%s: %s" % (err.get("Code"), err.get("Message")))
    return r["Response"]

# ---------------------------------------------------------------------------
# DNSPod API
# ---------------------------------------------------------------------------

def dnspod_api(endpoint, params, login_token, timeout=15):
    data = dict(params)
    data["login_token"] = login_token
    data["format"] = "json"
    req = urllib.request.Request(
        "https://dnsapi.cn/" + endpoint,
        data=urllib.parse.urlencode(data).encode("utf-8"),
        headers={"User-Agent": UA},
    )
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # DNSPod 认证失败等场景会返回 401/402，但 body 里仍是带原因的 JSON
        body = e.read().decode("utf-8", "ignore")
        try:
            return json.loads(body)
        except Exception:
            raise RuntimeError("HTTP %s: %s" % (e.code, body[:200] or e.reason))

def _auth_fail_hint(msg):
    log = logging.getLogger(APP_NAME)
    low = str(msg).lower()
    if not ("auth" in low or "login" in low or "token" in low or "signature"
            in low or "secret" in low or "denied" in low or "登录" in str(msg)
            or "认证" in str(msg) or "密钥" in str(msg) or "签名" in str(msg)):
        return
    log.error("认证失败排查提示：")
    log.error("1) 腾讯云方式：确认是 API 密钥 SecretId/SecretKey（控制台 "
              "console.cloud.tencent.com/cam/capi），密钥未禁用")
    log.error("2) DNSPod 方式：确认是 DNSPod API 令牌（ID,Token），顺序 ID 在前")
    log.error("3) 修改后运行 DDNS-Watchdog.exe --setup 更新配置")


def find_record(cfg):
    """查询 A 记录，返回 (record_id, line, value) 或 None；出错返回 ('ERROR',..)"""
    provider = cfg.get("provider", "dnspod")
    try:
        if provider == "tencent":
            r = tencent_api("DescribeRecordList", {
                "Domain": cfg["domain"],
                "Subdomain": cfg["sub_domain"],
                "RecordType": "A",
            }, cfg["secret_id"], cfg["secret_key"])
            for rec in r.get("RecordList") or []:
                if rec.get("Name") == cfg["sub_domain"] and rec.get("Type") == "A":
                    return (rec.get("RecordId"), rec.get("Line"), rec.get("Value"))
            return None
        else:
            r = dnspod_api("Record.List", {
                "domain": cfg["domain"],
                "sub_domain": cfg["sub_domain"],
                "record_type": "A",
            }, cfg["login_token"])
    except Exception as e:
        logging.getLogger(APP_NAME).error("查询记录列表失败: %s", e)
        _auth_fail_hint(e)
        return ("ERROR", None, None)
    if r.get("status", {}).get("code") != "1":
        msg = r.get("status", {}).get("message", "未知错误")
        logging.getLogger(APP_NAME).error("Record.List 返回异常: %s", msg)
        _auth_fail_hint(msg)
        return ("ERROR", None, None)
    for rec in r.get("records", []):
        if rec.get("name") == cfg["sub_domain"] and rec.get("type") == "A":
            return (rec.get("id"), rec.get("line"), rec.get("value"))
    return None

def update_record(cfg, ip):
    """确保 sub.domain 的 A 记录指向 ip。返回 (ok, action, message)"""
    if os.environ.get("DDNS_WATCHDOG_DRYRUN"):
        # 测试保护模式：完整走检测流程但绝不真实写 DNS
        logging.getLogger(APP_NAME).info("[DRYRUN] 测试模式，跳过真实 DNS 更新: %s", ip)
        return True, "dryrun", "[DRYRUN] 测试模式，未实际更新 DNS"
    provider = cfg.get("provider", "dnspod")
    found = find_record(cfg)
    if found == ("ERROR", None, None):
        return False, "query", "查询 DNS 记录失败"
    if found is None:
        try:
            if provider == "tencent":
                r = tencent_api("CreateRecord", {
                    "Domain": cfg["domain"],
                    "SubDomain": cfg["sub_domain"],
                    "RecordType": "A",
                    "RecordLine": "默认",
                    "Value": ip,
                    "TTL": 600,
                }, cfg["secret_id"], cfg["secret_key"])
                return True, "create", "已创建 A 记录 %s.%s -> %s" % (
                    cfg["sub_domain"], cfg["domain"], ip)
            else:
                r = dnspod_api("Record.Create", {
                    "domain": cfg["domain"],
                    "sub_domain": cfg["sub_domain"],
                    "record_type": "A",
                    "record_line_id": "default",
                    "value": ip,
                    "ttl": 600,
                }, cfg["login_token"])
                if r.get("status", {}).get("code") == "1":
                    return True, "create", "已创建 A 记录 %s.%s -> %s" % (
                        cfg["sub_domain"], cfg["domain"], ip)
                return False, "create", "创建记录失败: %s" % r.get("status", {}).get("message")
        except Exception as e:
            _auth_fail_hint(e)
            return False, "create", "创建记录请求失败: %s" % e

    record_id, line, cur_value = found
    if cur_value == ip:
        return True, "none", "DNS 记录已是最新 (%s)" % ip
    try:
        if provider == "tencent":
            tencent_api("ModifyRecord", {
                "Domain": cfg["domain"],
                "RecordId": record_id,
                "SubDomain": cfg["sub_domain"],
                "RecordType": "A",
                "RecordLine": line or "默认",
                "Value": ip,
                "TTL": 600,
            }, cfg["secret_id"], cfg["secret_key"])
            return True, "modify", "已更新 %s.%s: %s -> %s" % (
                cfg["sub_domain"], cfg["domain"], cur_value, ip)
        else:
            r = dnspod_api("Record.Modify", {
                "domain": cfg["domain"],
                "record_id": record_id,
                "sub_domain": cfg["sub_domain"],
                "record_type": "A",
                "record_line": line or "默认",
                "value": ip,
                "ttl": 600,
            }, cfg["login_token"])
            if r.get("status", {}).get("code") == "1":
                return True, "modify", "已更新 %s.%s: %s -> %s" % (
                    cfg["sub_domain"], cfg["domain"], cur_value, ip)
            return False, "modify", "修改记录失败: %s" % r.get("status", {}).get("message")
    except Exception as e:
        _auth_fail_hint(e)
        return False, "modify", "修改记录请求失败: %s" % e

# ---------------------------------------------------------------------------
# 网络自愈
# ---------------------------------------------------------------------------

def heal_network(log, do_renew):
    log.warning("=== 触发网络自愈 ===")
    try:
        r = subprocess.run(["ipconfig", "/flushdns"], capture_output=True, timeout=30)
        log.info("flushdns 完成 (rc=%s)", r.returncode)
    except Exception as e:
        log.error("flushdns 失败: %s", e)
    if do_renew:
        log.warning("执行 ipconfig /release + /renew（等同插拔网线，短暂断网中...）")
        try:
            subprocess.run(["ipconfig", "/release"], capture_output=True, timeout=60)
            time.sleep(2)
            r = subprocess.run(["ipconfig", "/renew"], capture_output=True, timeout=120)
            log.info("ipconfig /renew 完成 (rc=%s)", r.returncode)
        except Exception as e:
            log.error("release/renew 失败: %s", e)
    time.sleep(5)

# ---------------------------------------------------------------------------
# 主程序
# ---------------------------------------------------------------------------

def setup_logging(log_dir):
    os.makedirs(log_dir, exist_ok=True)
    log = logging.getLogger(APP_NAME)
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(os.path.join(log_dir, "ddns.log"), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    try:
        ch.stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ch.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(ch)
    return log

def load_or_create_config(path):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        return None
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # 补齐缺失键
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg

def pause_if_interactive():
    """双击运行时出错退出前等待按键，避免窗口一闪而过"""
    if os.environ.get("DDNS_WATCHDOG_NOPAUSE"):
        return
    try:
        input("\n按回车键退出...")
    except Exception:
        pass


def save_config(path, cfg):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def ask(prompt, default, secret=False):
    """带默认值的输入；secret 显示掩码"""
    shown = default if not secret else (
        default[:4] + "****" + default[-4:] if len(default) > 8 else "****")
    suffix = " (直接回车 = %s): " % shown if default else ": "
    while True:
        val = input(prompt + suffix).strip()
        if val:
            return val
        if default:
            return default
        print("  不能为空，请重新输入。")


def run_setup(cfg_path, cfg):
    """终端交互向导：配置/修改认证方式、域名等"""
    print("=" * 56)
    print("%s v%s — 配置向导" % (APP_NAME, VERSION))
    print("=" * 56)

    cur = cfg or dict(DEFAULT_CONFIG)
    old_provider = cur.get("provider", "dnspod")
    has_old = False
    if old_provider == "tencent" and cur.get("secret_id"):
        has_old = True
    elif old_provider == "dnspod" and cur.get("login_token") \
            and "在这里填" not in cur.get("login_token", ""):
        has_old = True
    if has_old:
        print("检测到已有配置，直接回车可保留原值。")
    print("认证方式:  [1] 腾讯云 API 密钥 (SecretId/SecretKey)")
    print("           [2] DNSPod API 令牌 (ID,Token)")
    default_choice = "1" if old_provider == "tencent" else "2"
    choice = input("请选择 (直接回车 = 方式%s): " % default_choice).strip() \
        or default_choice

    new_cfg = dict(cur)
    if choice == "1":
        new_cfg["provider"] = "tencent"
        old_sid = cur.get("secret_id", "") if has_old else ""
        old_skey = cur.get("secret_key", "") if has_old else ""
        new_cfg["secret_id"] = ask("请输入 SecretId", old_sid, secret=True)
        new_cfg["secret_key"] = ask("请输入 SecretKey", old_skey, secret=True)
    else:
        new_cfg["provider"] = "dnspod"
        old_login = cur.get("login_token", "")
        old_id, _, old_tok = old_login.partition(",")
        print("令牌在这里创建: https://console.dnspod.cn/account/token/token")
        token_id = ask("请输入 API 令牌 ID", old_id if has_old else "", secret=True)
        token_secret = ask("请输入 API 令牌 Token", old_tok if has_old else "",
                           secret=True)
        new_cfg["login_token"] = "%s,%s" % (token_id, token_secret)

    domain = ask("主域名 (如 example.com)", cur.get("domain", ""))
    sub_domain = ask("DDNS 主机名 (直接回车 = 根域 @，即域名本身)",
                     cur.get("sub_domain", ""))
    interval_raw = ask("检查间隔/秒 (建议 300，范围 10~86400)",
                       str(cur.get("check_interval_seconds", 300)))
    if interval_raw.isdigit() and 10 <= int(interval_raw) <= 86400:
        cur_interval = int(interval_raw)
    else:
        print("  间隔无效，保留原值 %s 秒" % cur.get("check_interval_seconds", 300))
        cur_interval = cur.get("check_interval_seconds", 300)
    new_cfg["domain"] = domain
    new_cfg["sub_domain"] = sub_domain
    new_cfg["check_interval_seconds"] = cur_interval

    save_config(cfg_path, new_cfg)
    print("\n✔ 已保存到: %s" % cfg_path)
    print("  认证方式: %s" % ("腾讯云 API 密钥" if new_cfg["provider"] == "tencent"
                          else "DNSPod API 令牌"))
    print("  监控目标: %s" % (domain if sub_domain == "@"
                          else "%s.%s" % (sub_domain, domain)))
    print("  检查间隔: %d 秒" % cur_interval)
    return new_cfg


def confirm_start():
    try:
        ans = input("\n是否立即启动监控? (Y/n): ").strip().lower()
    except Exception:
        ans = "n"
    return ans in ("", "y", "yes")


def wait_interruptible(seconds):
    """等待 seconds 秒；期间按 Q 键可中断返回 True。无控制台时退化为普通 sleep"""
    if os.environ.get("DDNS_WATCHDOG_NOPAUSE") or msvcrt is None:
        time.sleep(seconds)
        return False
    end = time.time() + seconds
    while time.time() < end:
        try:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch.lower() in ("q", "s"):
                    print("\n[检测到 Q 键，暂停监控，进入设置菜单]")
                    return True
        except Exception:
            # 无真实控制台（如被重定向/服务环境），退化为普通 sleep
            time.sleep(seconds)
            return False
        time.sleep(0.2)
    return False


def settings_menu(cfg, cfg_path, detector, state, state_path, log):
    """监控暂停时的设置菜单。返回更新后的 cfg；返回 None 表示退出程序"""
    while True:
        display = cfg["domain"] if cfg.get("sub_domain") == "@" \
            else "%s.%s" % (cfg.get("sub_domain"), cfg.get("domain"))
        print("\n" + "=" * 56)
        print("设置菜单（监控已暂停）")
        print("=" * 56)
        print("  当前监控: %s" % display)
        print("  当前间隔: %d 秒" % cfg.get("check_interval_seconds", 300))
        print("  -----------------------------------------")
        print("  [1] 重新配置密钥/域名（进入配置向导）")
        print("  [2] 修改检查间隔")
        print("  [3] 立即执行一次检查")
        print("  [4] 返回监控")
        print("  [0] 退出程序")
        try:
            choice = input("请选择: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if choice == "1":
            try:
                cfg = run_setup(cfg_path, cfg)
            except (EOFError, KeyboardInterrupt):
                print("\n已取消配置。")
        elif choice == "2":
            try:
                raw = input("新的检查间隔（秒，10~86400，直接回车取消）: ").strip()
            except (EOFError, KeyboardInterrupt):
                raw = ""
            if raw:
                if raw.isdigit() and 10 <= int(raw) <= 86400:
                    cfg["check_interval_seconds"] = int(raw)
                    save_config(cfg_path, cfg)
                    print("✔ 已保存，返回监控后生效")
                else:
                    print("✘ 请输入 10~86400 之间的整数")
        elif choice == "3":
            result = do_check(cfg, detector, state, state_path, log)
            print("检查结果: %s" % {"ok": "正常", "fail": "IP 检测失败",
                                   "error": "DNS 更新失败"}.get(result, result))
        elif choice == "4":
            return cfg
        elif choice == "0":
            return None
        else:
            print("无效选择，请重新输入。")


def do_check(cfg, detector, state, state_path, log):
    """执行一轮检测+更新。返回 'ok' | 'fail'(IP检测失败) | 'error'(DNS更新失败)"""
    ip, source = detector.get_ip()
    if ip is None:
        return "fail"

    if ip == state.get("last_ip"):
        # 本地 IP 未变，但周期性核对 DNS 记录，防止漂移
        found = find_record(cfg)
        if found == ("ERROR", None, None):
            log.error("DNS 记录核对失败（网络或 API 异常），下轮重试")
            return "error"
        if found is not None and found[2] != ip:
            log.warning("检测到 DNS 记录漂移: %s -> %s，自动修复", found[2], ip)
            ok, _, msg = update_record(cfg, ip)
            log.info(msg)
            if ok:
                state["last_ip"] = ip
                _save_state(state_path, state)
        else:
            log.info("公网 IP 未变化 (当前: %s | 来源: %s)，DNS 正常", ip, source)
        return "ok"

    # 换 IP 前用第二个源交叉确认，防止单个源返回错误 IP
    confirm, source2 = detector.get_ip()
    if confirm and confirm != ip:
        log.warning("两个源结果不一致（%s vs %s），以后者为准，下轮继续核对",
                    ip, confirm)
        ip, source = confirm, source2
    log.info("检测到公网 IP 变化: %s -> %s (来源: %s)",
             state.get("last_ip"), ip, source)
    ok, action, msg = update_record(cfg, ip)
    log.info(msg)
    if ok:
        state["last_ip"] = ip
        _save_state(state_path, state)
    return "ok" if ok else "error"


def main():
    frozen = bool(getattr(sys, "frozen", False))
    bdir = base_dir()
    cfg_path = os.path.join(bdir, "config.json")
    log = setup_logging(os.path.join(bdir, "logs"))

    log.info("%s v%s 启动 (Python %s, frozen=%s)", APP_NAME, VERSION,
             sys.version.split()[0], frozen)

    if "--version" in sys.argv:
        return 0

    # --setup：显式进入配置向导（修改配置）
    if "--setup" in sys.argv and not os.path.exists(cfg_path):
        save_config(cfg_path, dict(DEFAULT_CONFIG))
    cfg = load_or_create_config(cfg_path)
    provider = cfg.get("provider", "dnspod") if cfg else "dnspod"
    token_ok = bool(cfg) and (
        (provider == "tencent" and cfg.get("secret_id") and cfg.get("secret_key"))
        or (provider == "dnspod" and cfg.get("login_token")
            and "在这里填" not in cfg.get("login_token", "")))

    # 首次运行 / Token 未填 / --setup → 进入交互向导
    if cfg is None or not token_ok or "--setup" in sys.argv:
        if "--setup" in sys.argv and cfg is None:
            cfg = dict(DEFAULT_CONFIG)
        try:
            cfg = run_setup(cfg_path, cfg)
        except (EOFError, KeyboardInterrupt):
            print("\n已取消配置。")
            return 1
        if not confirm_start():
            log.info("配置已保存，下次运行将直接启动监控。")
            pause_if_interactive()
            return 0

    token_ok = bool(cfg) and (
        (cfg.get("provider") == "tencent" and cfg.get("secret_id") and cfg.get("secret_key"))
        or (cfg.get("provider") != "tencent" and cfg.get("login_token")
            and "在这里填" not in cfg.get("login_token", "")))
    if not token_ok:
        log.error("认证信息尚未填写完整！")
        log.error("配置路径: %s", cfg_path)
        log.error("请运行 DDNS-Watchdog.exe --setup 重新进入配置向导。")
        pause_if_interactive()
        return 1

    detector = IPDetector(cfg, log)
    state_path = os.path.join(bdir, "state.json")
    state = {"last_ip": None}
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            pass

    interval = cfg["check_interval_seconds"]
    max_interval = cfg["max_interval_seconds"]
    heal_after = max(1, int(cfg["heal_after_failures"]))
    fail_streak = 0
    display_target = cfg["domain"] if cfg["sub_domain"] == "@" \
        else "%s.%s" % (cfg["sub_domain"], cfg["domain"])

    log.info("监控: %s | 检查间隔 %ds", display_target, interval)

    if msvcrt is not None and not os.environ.get("DDNS_WATCHDOG_NOPAUSE"):
        log.info("提示：监控运行中随时按 Q 键进入设置菜单")

    while True:
        result = do_check(cfg, detector, state, state_path, log)
        if result == "fail":
            fail_streak += 1
            log.error("第 %d 轮：所有检测源均失败，无法获取公网 IP", fail_streak)
            if fail_streak >= heal_after:
                heal_network(log, cfg.get("auto_renew_adapter", True))
                fail_streak = 0
            interval = min(int(interval * 1.5) + 10, max_interval)
            log.info("退避至 %ds 后重试", interval)
        else:
            fail_streak = 0
            interval = cfg["check_interval_seconds"]

        # 可中断等待：按 Q 进入设置菜单
        if wait_interruptible(interval):
            new_cfg = settings_menu(cfg, cfg_path, detector, state, state_path, log)
            if new_cfg is None:
                log.info("已退出程序")
                return 0
            cfg = new_cfg
            detector = IPDetector(cfg, log)  # 配置可能已变，重建检测器
            interval = cfg["check_interval_seconds"]
            fail_streak = 0
            display_target = cfg["domain"] if cfg["sub_domain"] == "@" \
                else "%s.%s" % (cfg["sub_domain"], cfg["domain"])
            log.info("恢复监控: %s | 检查间隔 %ds", display_target, interval)


def _save_state(path, state):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass


if __name__ == "__main__":
    exit_code = 0
    try:
        exit_code = main()
    except KeyboardInterrupt:
        print("已退出")
    except Exception as e:
        # 未预期异常：记录日志、提示、等待按键，避免双击运行时闪退
        try:
            logging.getLogger(APP_NAME).exception("发生未预期异常: %s", e)
        except Exception:
            print("发生未预期异常: %s" % e)
        pause_if_interactive()
        exit_code = 2
    sys.exit(exit_code)
