# -*- coding: utf-8 -*-
"""DNSPod / 腾讯云 DNSPod 的记录读写。"""

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import UA
from .config import describe_target

# 探测与写入都不应经过系统代理
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_AUTH_KEYWORDS = ("auth", "login", "token", "signature", "secret", "denied",
                  "forbidden", "unauthorized", "认证", "密钥", "签名", "令牌", "鉴权")


class ProviderError(RuntimeError):
    """接口调用失败。message 直接面向用户，hints 是可操作的排查建议。"""

    def __init__(self, message, hints=None, code=""):
        super().__init__(message)
        self.hints = list(hints or [])
        self.code = code


def auth_hints(reason):
    """根据错误文本给出针对性排查步骤。"""
    text = str(reason).lower()
    if not any(word in text for word in _AUTH_KEYWORDS):
        return []
    return [
        "腾讯云方式：确认 SecretId/SecretKey 是 API 密钥，且密钥未被禁用",
        "DNSPod 方式：确认是 API 令牌（ID,Token），ID 在前、逗号分隔",
        "在配置界面重新保存后会立即热更新，无需重启监控",
    ]


def _fail_from_body(body):
    try:
        payload = json.loads(body)
    except Exception:
        return ProviderError("接口返回了无法解析的内容：%s" % (body[:120] or "空响应"))
    response = payload.get("Response") or {}
    error = response.get("Error") or {}
    if error:
        return ProviderError("%s: %s" % (error.get("Code", "未知错误"),
                                        error.get("Message", "")),
                             hints=auth_hints(error.get("Message", "")),
                             code=str(error.get("Code", "")))
    return ProviderError("接口返回异常：%s" % body[:120])


def tencent_api(action, params, secret_id, secret_key, timeout=15):
    """腾讯云 API 3.0，TC3-HMAC-SHA256 签名。"""
    service = "dnspod"
    host = "dnspod.tencentcloudapi.com"
    payload = json.dumps(params, separators=(",", ":"), ensure_ascii=False)
    timestamp = int(time.time())
    date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))

    canonical_headers = ("content-type:application/json; charset=utf-8\n"
                         "host:%s\nx-tc-action:%s\n" % (host, action.lower()))
    signed_headers = "content-type;host;x-tc-action"
    hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    canonical_request = "\n".join(
        ["POST", "/", "", canonical_headers, signed_headers, hashed_payload])

    scope = "%s/%s/tc3_request" % (date, service)
    hashed_canonical = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = "\n".join(["TC3-HMAC-SHA256", str(timestamp), scope, hashed_canonical])

    def _hmac(key, msg):
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_signing = _hmac(_hmac(_hmac(("TC3" + secret_key).encode("utf-8"), date), service),
                      "tc3_request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"),
                         hashlib.sha256).hexdigest()

    headers = {
        "Authorization": ("TC3-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, "
                          "Signature=%s" % (secret_id, scope, signed_headers, signature)),
        "Content-Type": "application/json; charset=utf-8",
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Version": "2021-03-23",
        "X-TC-Timestamp": str(timestamp),
    }
    request = urllib.request.Request(
        "https://%s/" % host, data=payload.encode("utf-8"), headers=headers)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        error = _fail_from_body(body)
        error.code = error.code or ("HTTP %s" % e.code)
        raise error
    except urllib.error.URLError as e:
        raise ProviderError("连接腾讯云接口失败：%s" % e.reason,
                            hints=["检查本机网络与 DNS", "若使用了代理，注意程序会强制直连"])
    except OSError as e:
        raise ProviderError("连接腾讯云接口失败：%s" % e)

    result = json.loads(body)
    response = result.get("Response") or {}
    error = response.get("Error")
    if error:
        raise ProviderError("%s: %s" % (error.get("Code", "未知错误"),
                                        error.get("Message", "")),
                            hints=auth_hints(error.get("Message", "")),
                            code=str(error.get("Code", "")))
    return response


def dnspod_api(endpoint, params, login_token, timeout=15):
    data = dict(params)
    data["login_token"] = login_token
    data["format"] = "json"
    request = urllib.request.Request(
        "https://dnsapi.cn/" + endpoint,
        data=urllib.parse.urlencode(data).encode("utf-8"),
        headers={"User-Agent": UA},
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        try:
            return json.loads(body)
        except Exception:
            raise ProviderError("HTTP %s: %s" % (e.code, body[:120] or e.reason),
                                hints=auth_hints(body))
    except urllib.error.URLError as e:
        raise ProviderError("连接 DNSPod 接口失败：%s" % e.reason,
                            hints=["检查本机网络与 DNS"])
    return json.loads(body)


def find_record(cfg):
    """查询目标 A 记录。

    返回 (state, payload, message)：
        ("found", (record_id, line, value), "")
        ("missing", None, "")
        ("error", None, 错误描述)
    """
    provider = cfg.get("provider", "tencent")
    try:
        if provider == "tencent":
            response = tencent_api("DescribeRecordList", {
                "Domain": cfg["domain"],
                "Subdomain": cfg["sub_domain"],
                "RecordType": "A",
            }, cfg["secret_id"], cfg["secret_key"])
            for rec in response.get("RecordList") or []:
                if rec.get("Name") == cfg["sub_domain"] and rec.get("Type") == "A":
                    return "found", (rec.get("RecordId"), rec.get("Line"),
                                     rec.get("Value")), ""
            return "missing", None, ""

        response = dnspod_api("Record.List", {
            "domain": cfg["domain"],
            "sub_domain": cfg["sub_domain"],
            "record_type": "A",
        }, cfg["login_token"])
    except ProviderError as e:
        return "error", None, str(e)
    except Exception as e:  # 网络层之外的意外
        return "error", None, "查询记录失败：%s" % e

    status = response.get("status") or {}
    if status.get("code") != "1":
        return "error", None, "Record.List 返回异常：%s" % status.get("message", "未知错误")
    for rec in response.get("records") or []:
        if rec.get("name") == cfg["sub_domain"] and rec.get("type") == "A":
            return "found", (rec.get("id"), rec.get("line"), rec.get("value")), ""
    return "missing", None, ""


def apply_record(cfg, ip, dryrun=False):
    """确保目标记录指向 ip。返回 (ok, action, message, hints)。"""
    target = describe_target(cfg)
    if dryrun:
        return True, "dryrun", "[DRYRUN] 测试模式，未实际修改 DNS（目标 %s -> %s）" % (
            target, ip), []

    state, payload, message = find_record(cfg)
    if state == "error":
        return False, "query", message, auth_hints(message)

    provider = cfg.get("provider", "tencent")
    if state == "missing":
        try:
            if provider == "tencent":
                tencent_api("CreateRecord", {
                    "Domain": cfg["domain"],
                    "SubDomain": cfg["sub_domain"],
                    "RecordType": "A",
                    "RecordLine": "默认",
                    "Value": ip,
                    "TTL": 600,
                }, cfg["secret_id"], cfg["secret_key"])
                return True, "create", "已创建 A 记录 %s -> %s" % (target, ip), []
            response = dnspod_api("Record.Create", {
                "domain": cfg["domain"],
                "sub_domain": cfg["sub_domain"],
                "record_type": "A",
                "record_line_id": "default",
                "value": ip,
                "ttl": 600,
            }, cfg["login_token"])
            if (response.get("status") or {}).get("code") == "1":
                return True, "create", "已创建 A 记录 %s -> %s" % (target, ip), []
            detail = (response.get("status") or {}).get("message", "未知错误")
            return False, "create", "创建记录失败：%s" % detail, []
        except ProviderError as e:
            return False, "create", "创建记录失败：%s" % e, e.hints

    record_id, line, current_value = payload
    if current_value == ip:
        return True, "none", "DNS 记录已是最新（%s）" % ip, []
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
            return True, "modify", "已更新 %s：%s -> %s" % (target, current_value, ip), []
        response = dnspod_api("Record.Modify", {
            "domain": cfg["domain"],
            "record_id": record_id,
            "sub_domain": cfg["sub_domain"],
            "record_type": "A",
            "record_line": line or "默认",
            "value": ip,
            "ttl": 600,
        }, cfg["login_token"])
        if (response.get("status") or {}).get("code") == "1":
            return True, "modify", "已更新 %s：%s -> %s" % (target, current_value, ip), []
        detail = (response.get("status") or {}).get("message", "未知错误")
        return False, "modify", "修改记录失败：%s" % detail, []
    except ProviderError as e:
        return False, "modify", "修改记录失败：%s" % e, e.hints


def check_credentials(cfg, timeout=12):
    """只读校验：能否列出记录。返回 (ok, message, hints)。"""
    try:
        state, _payload, message = find_record(cfg)
    except ProviderError as e:
        return False, str(e), e.hints
    if state == "error":
        return False, message, auth_hints(message)
    return True, "凭据有效，接口可以正常访问 %s" % cfg.get("domain", ""), []
