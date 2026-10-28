# -*- coding: utf-8 -*-
"""配置的读取、校验、原子写入与变更检测。

两个启动器同时运行时会并发访问 config.json：
写操作走「锁文件 + 临时文件 + os.replace」，读操作失败会重试，
避免终端读到 GUI 写到一半的半截 JSON。
"""

import json
import os
import re
import time
from contextlib import contextmanager

from . import paths

try:
    import msvcrt
except ImportError:  # 非 Windows 平台
    msvcrt = None

SCHEMA_VERSION = 2

DEFAULT_STUN_SERVERS = [
    "stun.qq.com:3478",
    "stun.miwifi.com:3478",
    "stun.cloudflare.com:3478",
]

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

DEFAULT_CONFIG = {
    "schema_version": SCHEMA_VERSION,
    # 认证方式: tencent=腾讯云 API 密钥 | dnspod=DNSPod API 令牌
    "provider": "tencent",
    "secret_id": "",
    "secret_key": "",
    "login_token": "",
    "domain": "example.com",
    "sub_domain": "@",
    "check_interval_seconds": 300,
    "max_interval_seconds": 1800,
    "heal_after_failures": 1,
    "auto_renew_adapter": True,
    "stun_servers": DEFAULT_STUN_SERVERS,
    "http_sources": DEFAULT_HTTP_SOURCES,
    # 以下字段由程序维护，记录配置写入时间与版本号，供热更新判断
    "revision": 0,
    "updated_at": "",
    "updated_by": "",
}

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$")
_LABEL_RE = re.compile(r"^(\*|[a-zA-Z0-9_]([a-zA-Z0-9_-]{0,61}[a-zA-Z0-9_])?)$")
_URL_RE = re.compile(r"^https?://[^\s/]+\.[^\s]+$", re.I)
_HOSTPORT_RE = re.compile(r"^[a-zA-Z0-9.\-]+(:\d{1,5})?$")


class Issue(object):
    """一条字段级校验错误。"""

    __slots__ = ("field", "message", "hint")

    def __init__(self, field, message, hint=""):
        self.field = field
        self.message = message
        self.hint = hint

    def __repr__(self):
        return "Issue(%s: %s)" % (self.field, self.message)

    def as_text(self):
        if self.hint:
            return "%s：%s（%s）" % (self.field, self.message, self.hint)
        return "%s：%s" % (self.field, self.message)


class ConfigError(Exception):
    """配置不可用：语法错误、文件不可读或校验未通过。"""

    def __init__(self, message, issues=None, path=None):
        super().__init__(message)
        self.issues = list(issues or [])
        self.path = path

    def detail_lines(self):
        lines = [str(self)]
        for issue in self.issues:
            lines.append("  · " + issue.as_text())
        return lines


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def _as_int(value, field, low, high, issues, default=None):
    if isinstance(value, bool) or value is None or value == "":
        issues.append(Issue(field, "必须是 %d~%d 之间的整数" % (low, high)))
        return default
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        issues.append(Issue(field, "「%s」不是有效整数" % value,
                            "请填 %d~%d 之间的数字" % (low, high)))
        return default
    if not low <= number <= high:
        issues.append(Issue(field, "%d 超出允许范围" % number,
                            "请填 %d~%d 之间的整数" % (low, high)))
        return default
    return number


def _as_bool(value, field, issues, default=True):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "1", "yes", "on"):
        return True
    if isinstance(value, str) and value.strip().lower() in ("false", "0", "no", "off"):
        return False
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    issues.append(Issue(field, "必须是 true 或 false"))
    return default


def _clean_list(value, field, issues, pattern, label):
    if isinstance(value, str):
        value = [line.strip() for line in value.splitlines() if line.strip()]
    if not isinstance(value, list):
        issues.append(Issue(field, "必须是列表"))
        return []
    out = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            issues.append(Issue(field, "列表里有空值或非字符串项"))
            continue
        text = item.strip()
        if not pattern.match(text):
            issues.append(Issue(field, "「%s」不是合法的%s" % (text, label)))
            continue
        if text not in out:
            out.append(text)
    return out


def validate(raw, require_credentials=True):
    """校验并归一化配置。

    返回 (cfg, issues)。issues 为空表示可用；cfg 中已把能修的类型修好，
    这样 GUI 可以直接拿归一化结果回填。
    """
    issues = []
    if not isinstance(raw, dict):
        return dict(DEFAULT_CONFIG), [Issue("config", "配置内容必须是 JSON 对象")]

    cfg = dict(DEFAULT_CONFIG)
    cfg.update(raw)

    # 认证方式
    provider = str(cfg.get("provider") or "").strip().lower()
    if provider not in ("tencent", "dnspod"):
        issues.append(Issue("provider", "认证方式只能是 tencent 或 dnspod",
                            "GUI 里选「腾讯云 API 密钥」或「DNSPod 令牌」"))
        provider = "tencent"
    cfg["provider"] = provider

    cfg["secret_id"] = str(cfg.get("secret_id") or "").strip()
    cfg["secret_key"] = str(cfg.get("secret_key") or "").strip()
    cfg["login_token"] = str(cfg.get("login_token") or "").strip()

    if provider == "tencent":
        if require_credentials:
            if not cfg["secret_id"]:
                issues.append(Issue("secret_id", "腾讯云 SecretId 为空",
                                    "在 console.cloud.tencent.com/cam/capi 创建 API 密钥"))
            elif not cfg["secret_id"].startswith("AKID") or len(cfg["secret_id"]) < 16:
                issues.append(Issue("secret_id", "SecretId 格式不像腾讯云密钥",
                                    "应以 AKID 开头"))
            if not cfg["secret_key"]:
                issues.append(Issue("secret_key", "腾讯云 SecretKey 为空",
                                    "与 SecretId 成对生成，注意不要只复制一半"))
            elif len(cfg["secret_key"]) < 16:
                issues.append(Issue("secret_key", "SecretKey 长度异常",
                                    "应为 32 位左右的字符串"))
    else:
        token = cfg["login_token"]
        if require_credentials:
            if not token:
                issues.append(Issue("login_token", "DNSPod 令牌为空",
                                    "在 console.dnspod.cn/account/token/token 创建"))
            elif "," not in token:
                issues.append(Issue("login_token", "DNSPod 令牌格式应为 ID,Token",
                                    "把令牌 ID 放在逗号前面"))
            else:
                token_id, _, token_secret = token.partition(",")
                if not token_id.strip().isdigit():
                    issues.append(Issue("login_token", "令牌 ID 部分应为纯数字"))
                if len(token_secret.strip()) < 8:
                    issues.append(Issue("login_token", "令牌 Token 部分过短"))

    # 域名
    domain = str(cfg.get("domain") or "").strip().lower()
    domain = re.sub(r"^https?://", "", domain).strip("/")
    if not domain:
        issues.append(Issue("domain", "主域名不能为空", "例如 example.com"))
    elif domain in ("example.com", "example.org", "test.com"):
        issues.append(Issue("domain", "还是示例域名，请填自己的域名"))
    elif not _DOMAIN_RE.match(domain):
        issues.append(Issue("domain", "「%s」不是合法域名" % domain,
                            "只填域名本身，不要带 http:// 或路径"))
    cfg["domain"] = domain

    # 主机名
    sub = str(cfg.get("sub_domain") or "").strip()
    sub = sub.rstrip(".") or "@"
    if sub != "@":
        bad = [p for p in sub.split(".") if not _LABEL_RE.match(p)]
        if bad:
            issues.append(Issue("sub_domain", "「%s」不是合法主机名" % sub,
                                "例如 @、ddns、home.lan；只允许字母数字和 -_"))
    cfg["sub_domain"] = sub

    cfg["check_interval_seconds"] = _as_int(
        cfg.get("check_interval_seconds"), "check_interval_seconds", 10, 86400, issues,
        DEFAULT_CONFIG["check_interval_seconds"])
    cfg["max_interval_seconds"] = _as_int(
        cfg.get("max_interval_seconds"), "max_interval_seconds", 10, 86400, issues,
        DEFAULT_CONFIG["max_interval_seconds"])
    if (cfg["max_interval_seconds"] is not None and cfg["check_interval_seconds"]
            and cfg["max_interval_seconds"] < cfg["check_interval_seconds"]):
        issues.append(Issue("max_interval_seconds", "最大间隔不能小于检查间隔"))
        cfg["max_interval_seconds"] = cfg["check_interval_seconds"]

    cfg["heal_after_failures"] = _as_int(
        cfg.get("heal_after_failures"), "heal_after_failures", 1, 100, issues,
        DEFAULT_CONFIG["heal_after_failures"])
    cfg["auto_renew_adapter"] = _as_bool(
        cfg.get("auto_renew_adapter"), "auto_renew_adapter", issues, True)

    stun = _clean_list(cfg.get("stun_servers"), "stun_servers", issues,
                       _HOSTPORT_RE, "STUN 服务器（host:port）")
    if not stun:
        issues.append(Issue("stun_servers", "至少保留 1 个 STUN 服务器",
                            "删掉全部 STUN 源会降低弱网下的检测成功率"))
        stun = list(DEFAULT_STUN_SERVERS)
    cfg["stun_servers"] = stun

    http_sources = _clean_list(cfg.get("http_sources"), "http_sources", issues,
                               _URL_RE, "HTTP 地址")
    if not http_sources:
        issues.append(Issue("http_sources", "至少保留 1 个 HTTP 检测源"))
        http_sources = list(DEFAULT_HTTP_SOURCES)
    cfg["http_sources"] = http_sources

    cfg["schema_version"] = SCHEMA_VERSION
    return cfg, issues


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------

@contextmanager
def _file_lock(path, timeout=5.0):
    """跨进程写锁。拿不到锁时抛 ConfigError，由调用方给出提示。"""
    handle = open(path, "a+b")
    deadline = time.time() + timeout
    locked = False
    try:
        if msvcrt is not None:
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError:
                    if time.time() > deadline:
                        raise ConfigError("配置文件正被另一个程序占用，请稍后重试",
                                          path=path)
                    time.sleep(0.05)
        yield
    finally:
        if locked and msvcrt is not None:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        handle.close()


def _atomic_write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(6):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            # 另一端刚好在读，稍等重试
            if attempt == 5:
                raise
            time.sleep(0.05)


def load(path=None, require_credentials=True, allow_missing=False):
    """读取并校验配置；失败抛 ConfigError（含字段级 issues）。"""
    path = path or paths.config_path()
    if not os.path.exists(path):
        if allow_missing:
            cfg = dict(DEFAULT_CONFIG)
            cfg["_missing"] = True
            return cfg
        raise ConfigError("配置文件不存在", path=path)

    last_error = None
    for attempt in range(4):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            break
        except json.JSONDecodeError as e:
            last_error = e
            time.sleep(0.08)
        except OSError as e:
            raise ConfigError("配置文件读取失败：%s" % e, path=path)
    else:
        raise ConfigError(
            "配置不是合法 JSON（第 %d 行第 %d 列）：%s"
            % (last_error.lineno, last_error.colno, last_error.msg),
            path=path)

    cfg, issues = validate(raw, require_credentials=require_credentials)
    if issues:
        raise ConfigError("配置校验未通过（%d 项）" % len(issues), issues=issues, path=path)
    return cfg


def save(cfg, path=None, note="", bump_revision=True):
    """校验后原子写入；返回实际落盘的配置（含新 revision）。"""
    path = path or paths.config_path()
    clean, issues = validate(cfg)
    if issues:
        raise ConfigError("保存失败，配置校验未通过", issues=issues, path=path)

    with _file_lock(path + ".lock"):
        current_revision = 0
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    current_revision = int(json.load(f).get("revision") or 0)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                current_revision = 0
        clean["revision"] = current_revision + 1 if bump_revision else current_revision
        clean["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if note:
            clean["updated_by"] = note
        _atomic_write(path, clean)
    return clean


def fingerprint(path=None):
    """用于热更新检测：文件元信息变化即视为「可能已修改」。"""
    path = path or paths.config_path()
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def exists(path=None):
    return os.path.exists(path or paths.config_path())


def describe_target(cfg):
    sub = cfg.get("sub_domain") or "@"
    domain = cfg.get("domain") or ""
    return domain if sub == "@" else "%s.%s" % (sub, domain)
