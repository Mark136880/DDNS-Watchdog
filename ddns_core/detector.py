# -*- coding: utf-8 -*-
"""公网 IP 检测：STUN 优先，HTTP 兜底，失败源动态降权。"""

import os
import random
import socket
import struct
import urllib.request

from . import UA
from .config import DEFAULT_HTTP_SOURCES, DEFAULT_STUN_SERVERS

_MAGIC_COOKIE = 0x2112A442
_ATTR_MAPPED = 0x0001
_ATTR_XOR_MAPPED = 0x0020
_BINDING_REQUEST = 0x0001
_BINDING_RESPONSE = 0x0101

# DDNS 要拿的是本机真实出口 IP，必须绕过系统代理
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 明确不该写进 A 记录的段（私网、保留、组播、基准测试、文档示例等）
_RESERVED = (
    ("0.0.0.0", 8),
    ("10.0.0.0", 8),
    ("100.64.0.0", 10),
    ("127.0.0.0", 8),
    ("169.254.0.0", 16),
    ("172.16.0.0", 12),
    ("192.0.0.0", 24),
    ("192.0.2.0", 24),
    ("192.168.0.0", 16),
    ("198.18.0.0", 15),
    ("198.51.100.0", 24),
    ("203.0.113.0", 24),
    ("224.0.0.0", 4),
    ("240.0.0.0", 4),
)


def _ip_to_int(ip):
    a, b, c, d = (int(x) for x in ip.split("."))
    return (a << 24) | (b << 16) | (c << 8) | d


def _in_reserved(ip):
    value = _ip_to_int(ip)
    for base, prefix in _RESERVED:
        mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
        if value & mask == _ip_to_int(base) & mask:
            return True
    return False


def is_valid_ipv4(value):
    if not isinstance(value, str):
        return False
    parts = value.strip().split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit() or not 0 <= int(part) <= 255:
            return False
    return True


def is_usable_ip(value):
    """能否作为公网 A 记录的值。"""
    if not is_valid_ipv4(value):
        return False
    return not _in_reserved(value.strip())


def stun_query(server, port=3478, timeout=5):
    """RFC 5389 Binding Request，返回映射后的公网 IPv4，失败返回 None。"""
    transaction_id = os.urandom(12)
    request = struct.pack(">HHI", _BINDING_REQUEST, 0, _MAGIC_COOKIE) + transaction_id
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(request, (server, port))
        data, _ = sock.recvfrom(2048)
    except (OSError, socket.timeout):
        return None
    finally:
        sock.close()

    if len(data) < 20:
        return None
    msg_type, _length, magic = struct.unpack(">HHI", data[:8])
    if msg_type != _BINDING_RESPONSE or magic != _MAGIC_COOKIE:
        return None
    # 事务 ID 必须与请求一致，避免把过期的迟到响应当成自己的
    if data[8:20] != transaction_id:
        return None

    offset = 20
    while offset + 4 <= len(data):
        attr_type, attr_len = struct.unpack(">HH", data[offset:offset + 4])
        value = data[offset + 4:offset + 4 + attr_len]
        if attr_type == _ATTR_XOR_MAPPED and len(value) >= 8:
            ip_int = struct.unpack(">I", value[4:8])[0] ^ _MAGIC_COOKIE
            return socket.inet_ntoa(struct.pack(">I", ip_int))
        if attr_type == _ATTR_MAPPED and len(value) >= 8:
            return socket.inet_ntoa(value[4:8])
        offset += 4 + attr_len + ((4 - attr_len % 4) % 4)
    return None


def http_query(url, timeout=8, opener=None):
    """从纯文本接口里捞第一个可用 IPv4。"""
    opener = opener or _OPENER
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with opener.open(request, timeout=timeout) as response:
            text = response.read(512).decode("utf-8", "ignore")
    except Exception:
        return None
    for token in text.replace(",", " ").split():
        token = token.strip("<>\"'()[]{}")
        if is_usable_ip(token):
            return token
    return None


class IPDetector(object):
    def __init__(self, cfg, log, opener=None):
        self.log = log
        self.opener = opener or _OPENER
        self.fail_cnt = {}
        self.configure(cfg)

    def configure(self, cfg):
        self.stun_servers = list(cfg.get("stun_servers") or DEFAULT_STUN_SERVERS)
        self.http_sources = list(cfg.get("http_sources") or DEFAULT_HTTP_SOURCES)

    def _rank(self, sources):
        return sorted(sources, key=lambda s: (self.fail_cnt.get(s, 0), random.random()))

    def get_ip(self):
        """返回 (ip, source)；全部失败返回 (None, None)。"""
        for entry in self._rank(self.stun_servers):
            host, _, port = entry.partition(":")
            try:
                port_number = int(port or 3478)
            except ValueError:
                self.fail_cnt[entry] = self.fail_cnt.get(entry, 0) + 1
                self.log.debug("STUN 源端口无效，已跳过: %s", entry)
                continue
            ip = stun_query(host, port_number)
            if is_usable_ip(ip):
                self.fail_cnt[entry] = 0
                return ip, "STUN:" + entry
            self.fail_cnt[entry] = self.fail_cnt.get(entry, 0) + 1
            self.log.debug("STUN 源失败: %s", entry)

        for url in self._rank(self.http_sources):
            ip = http_query(url, opener=self.opener)
            if is_usable_ip(ip):
                self.fail_cnt[url] = 0
                return ip, "HTTP:" + url
            self.fail_cnt[url] = self.fail_cnt.get(url, 0) + 1
            self.log.debug("HTTP 源失败: %s", url)
        return None, None
