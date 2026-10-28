# -*- coding: utf-8 -*-
"""配置校验与热更新的离线测试（不联网、不写真实配置）。"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ddns_core import config as config_mod  # noqa: E402
from ddns_core import engine  # noqa: E402


class ValidationTest(unittest.TestCase):
    def base(self, **overrides):
        cfg = dict(config_mod.DEFAULT_CONFIG)
        cfg.update({
            "provider": "tencent",
            "secret_id": "AKIDxxxxxxxxxxxxxxxxxxxxxxxx",
            "secret_key": "0123456789abcdef0123456789abcdef",
            "domain": "example.net",
            "sub_domain": "@",
        })
        cfg.update(overrides)
        return cfg

    def test_valid_config_passes(self):
        _clean, issues = config_mod.validate(self.base())
        self.assertEqual([], issues)

    def test_placeholder_domain_rejected(self):
        _clean, issues = config_mod.validate(self.base(domain="example.com"))
        self.assertTrue(any(i.field == "domain" for i in issues))

    def test_domain_with_scheme_is_cleaned(self):
        clean, issues = config_mod.validate(self.base(domain="https://Example.NET/"))
        self.assertEqual([], issues)
        self.assertEqual("example.net", clean["domain"])

    def test_bad_domain_rejected(self):
        _clean, issues = config_mod.validate(self.base(domain="not a domain"))
        self.assertTrue(any(i.field == "domain" for i in issues))

    def test_bad_subdomain_rejected(self):
        _clean, issues = config_mod.validate(self.base(sub_domain="bad domain"))
        self.assertTrue(any(i.field == "sub_domain" for i in issues))

    def test_wildcard_subdomain_allowed(self):
        _clean, issues = config_mod.validate(self.base(sub_domain="*.home"))
        self.assertEqual([], issues)

    def test_string_interval_is_coerced(self):
        clean, issues = config_mod.validate(self.base(check_interval_seconds="120"))
        self.assertEqual([], issues)
        self.assertEqual(120, clean["check_interval_seconds"])

    def test_out_of_range_interval_rejected(self):
        _clean, issues = config_mod.validate(self.base(check_interval_seconds="5"))
        self.assertTrue(any(i.field == "check_interval_seconds" for i in issues))

    def test_max_interval_below_check_rejected(self):
        _clean, issues = config_mod.validate(
            self.base(check_interval_seconds=600, max_interval_seconds=300))
        self.assertTrue(any(i.field == "max_interval_seconds" for i in issues))

    def test_missing_secret_key_reported(self):
        _clean, issues = config_mod.validate(self.base(secret_key=""))
        self.assertTrue(any(i.field == "secret_key" for i in issues))

    def test_dnspod_token_format(self):
        _clean, issues = config_mod.validate(self.base(
            provider="dnspod", login_token="123456"))
        self.assertTrue(any(i.field == "login_token" for i in issues))

    def test_empty_sources_fall_back_to_default(self):
        clean, _issues = config_mod.validate(self.base(stun_servers=[], http_sources=[]))
        self.assertTrue(clean["stun_servers"])
        self.assertTrue(clean["http_sources"])

    def test_invalid_url_rejected(self):
        _clean, issues = config_mod.validate(self.base(http_sources=["4.ipw.cn"]))
        self.assertTrue(any(i.field == "http_sources" for i in issues))


class FileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ddns-test-")
        self.path = os.path.join(self.tmp, "config.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def good_config(self):
        cfg = dict(config_mod.DEFAULT_CONFIG)
        cfg.update({
            "secret_id": "AKIDxxxxxxxxxxxxxxxxxxxxxxxx",
            "secret_key": "0123456789abcdef0123456789abcdef",
            "domain": "example.net",
        })
        return cfg

    def test_save_bumps_revision(self):
        first = config_mod.save(self.good_config(), self.path)
        second = config_mod.save(self.good_config(), self.path)
        self.assertEqual(1, first["revision"])
        self.assertEqual(2, second["revision"])

    def test_save_creates_no_temp_leftover(self):
        config_mod.save(self.good_config(), self.path)
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_load_roundtrip(self):
        config_mod.save(self.good_config(), self.path)
        loaded = config_mod.load(self.path)
        self.assertEqual("example.net", loaded["domain"])

    def test_broken_json_reports_position(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('{"domain": "example.net",}')
        with self.assertRaises(config_mod.ConfigError) as ctx:
            config_mod.load(self.path)
        self.assertIn("第", str(ctx.exception))

    def test_invalid_config_raises_with_issues(self):
        cfg = self.good_config()
        cfg["domain"] = "example.com"
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        with self.assertRaises(config_mod.ConfigError) as ctx:
            config_mod.load(self.path)
        self.assertTrue(ctx.exception.issues)

    def test_missing_file_message(self):
        with self.assertRaises(config_mod.ConfigError):
            config_mod.load(self.path)


class HotReloadTest(unittest.TestCase):
    """模拟 GUI 保存后，监控侧能识别并应用新配置。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ddns-hot-")
        self.path = os.path.join(self.tmp, "config.json")
        cfg = dict(config_mod.DEFAULT_CONFIG)
        cfg.update({
            "secret_id": "AKIDxxxxxxxxxxxxxxxxxxxxxxxx",
            "secret_key": "0123456789abcdef0123456789abcdef",
            "domain": "example.net",
            "check_interval_seconds": 300,
            "heal_after_failures": 3,
        })
        self.cfg = config_mod.save(cfg, self.path)
        self.log = mock.Mock()
        self.monitor = engine.Monitor(self.cfg, self.log, self.path, keyboard=False)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_change_detected_initially(self):
        self.assertFalse(self.monitor.config_changed())

    def test_change_detected_then_applied(self):
        new_cfg = dict(self.cfg)
        new_cfg["check_interval_seconds"] = 60
        new_cfg["heal_after_failures"] = 1
        config_mod.save(new_cfg, self.path)
        self.assertTrue(self.monitor.config_changed())
        self.assertTrue(self.monitor.reload())
        self.assertEqual(60, self.monitor.interval)
        self.assertEqual(1, self.monitor.heal_after)
        self.assertFalse(self.monitor.config_changed())

    def test_invalid_change_keeps_old_config(self):
        # 绕过 save() 的校验，模拟「外部程序把值改坏」
        new_cfg = dict(self.cfg)
        new_cfg["check_interval_seconds"] = "abc"
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(new_cfg, f)
        self.assertFalse(self.monitor.reload())
        self.assertEqual(300, self.monitor.interval)
        self.assertTrue(self.monitor._config_error)

    def test_broken_json_keeps_old_config(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ not json")
        self.assertFalse(self.monitor.reload())
        self.assertEqual(300, self.monitor.interval)

    def test_target_change_resets_last_ip(self):
        self.monitor.state["last_ip"] = "1.2.3.4"
        new_cfg = dict(self.cfg)
        new_cfg["sub_domain"] = "home"
        config_mod.save(new_cfg, self.path)
        self.monitor.reload()
        self.assertIsNone(self.monitor.state["last_ip"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
