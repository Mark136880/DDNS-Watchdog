# -*- coding: utf-8 -*-
"""四个配置页：连接、检测、自愈、状态。"""

import os
import subprocess
import tkinter as tk

from ddns_core import config as config_mod
from ddns_core import logging_setup, paths, runtime

from . import theme, widgets


class Page(widgets.ScrollFrame):
    key = ""
    title = ""
    subtitle = ""

    def __init__(self, master, app):
        widgets.ScrollFrame.__init__(self, master, bg=theme.BG)
        self.app = app
        self.fields = {}
        self.content = tk.Frame(self.inner, bg=theme.BG)
        self.content.pack(fill="x", padx=theme.px(20), pady=(theme.px(2), theme.px(12)))
        self.build()

    def card(self, pady=(0, theme.px(8))):
        card = widgets.Card(self.content, pad=theme.px(14), parent_bg=theme.BG)
        card.pack(fill="x", pady=pady)
        return card

    def build(self):
        raise NotImplementedError

    def collect(self, draft):
        pass

    def load(self, cfg):
        pass

    def clear_errors(self):
        for widget in self.fields.values():
            if hasattr(widget, "clear_error"):
                widget.clear_error()
        for extra in getattr(self, "extra_error_labels", []):
            extra.configure(text="")

    def apply_issues(self, issues):
        matched = False
        for issue in issues:
            widget = self.fields.get(issue.field)
            if widget is not None and hasattr(widget, "show_error"):
                widget.show_error(issue.message)
                matched = True
        return matched


# ---------------------------------------------------------------------------
# 连接
# ---------------------------------------------------------------------------

class ConnectionPage(Page):
    key = "connection"
    title = "连接"
    subtitle = "认证方式与要维护的解析记录"

    def build(self):
        # 认证方式
        card = self.card()
        body = card.body
        widgets.SectionTitle(
            body, "认证方式",
            "腾讯云用 API 密钥；DNSPod 用 API 令牌。两者都只在本地保存，不会上传。").pack(fill="x")
        self.provider = widgets.SegmentedControl(
            body, [("tencent", "腾讯云 API 密钥"), ("dnspod", "DNSPod 令牌")],
            value="tencent", command=self._on_provider, width=theme.px(360))
        self.provider.pack(anchor="w", pady=(theme.px(8), theme.px(10)))

        self.tencent_group = tk.Frame(body, bg=theme.SURFACE)
        self.secret_id = widgets.TextField(
            self.tencent_group, "SecretId", hint="AKID 开头",
            placeholder="AKIDxxxxxxxxxxxxxxxx")
        self.secret_id.pack(fill="x", pady=(0, theme.px(8)))
        self.secret_key = widgets.TextField(
            self.tencent_group, "SecretKey", secret=True, hint="与 SecretId 成对")
        self.secret_key.pack(fill="x")
        self.fields["secret_id"] = self.secret_id
        self.fields["secret_key"] = self.secret_key

        self.dnspod_group = tk.Frame(body, bg=theme.SURFACE)
        self.login_token = widgets.TextField(
            self.dnspod_group, "API 令牌", secret=True,
            hint="格式 ID,Token", placeholder="123456,xxxxxxxxxxxxxxxx")
        self.login_token.pack(fill="x")
        self.fields["login_token"] = self.login_token

        self.tencent_group.pack(fill="x")

        # 解析目标
        card = self.card()
        body = card.body
        widgets.SectionTitle(body, "解析目标", "记录不存在会自动创建，只影响这一条记录").pack(fill="x")
        row = tk.Frame(body, bg=theme.SURFACE)
        row.pack(fill="x", pady=(theme.px(8), 0))
        row.grid_columnconfigure(0, weight=3)
        row.grid_columnconfigure(1, weight=2)

        self.domain = widgets.TextField(row, "主域名")
        self.domain.grid(row=0, column=0, sticky="ew", padx=(0, theme.px(12)))
        self.sub_domain = widgets.TextField(row, "主机名（@ 为根域）")
        self.sub_domain.grid(row=0, column=1, sticky="ew")
        self.fields["domain"] = self.domain
        self.fields["sub_domain"] = self.sub_domain

        self.domain.var.trace_add("write", lambda *_: self._refresh_target())
        self.sub_domain.var.trace_add("write", lambda *_: self._refresh_target())

        # 测试连接：并进同一张卡片，少一张卡、少一段留白
        tk.Frame(body, bg=theme.BORDER, height=1).pack(fill="x",
                                                       pady=(theme.px(10), theme.px(10)))
        line = tk.Frame(body, bg=theme.SURFACE)
        line.pack(fill="x")
        self.test_btn = widgets.RoundedButton(line, "测试连接", command=self._test,
                                              kind="ghost", width=theme.px(100))
        self.test_btn.pack(side="left")
        self.test_result = tk.Label(line, text="只读检查，不会修改任何记录",
                                    bg=theme.SURFACE, fg=theme.FAINT,
                                    font=theme.FONT_SMALL, anchor="w", justify="left",
                                    wraplength=theme.px(400))
        self.test_result.pack(side="left", padx=(theme.px(12), 0))

    def _on_provider(self, value):
        if value == "tencent":
            self.dnspod_group.pack_forget()
            self.tencent_group.pack(fill="x")
        else:
            self.tencent_group.pack_forget()
            self.dnspod_group.pack(fill="x")
        self.app.mark_dirty()

    def _refresh_target(self):
        domain = self.domain.get().strip() or "example.com"
        sub = (self.sub_domain.get().strip() or "@").lower()
        full = domain if sub == "@" else "%s.%s" % (sub, domain)
        # 直接显示在主机名输入框右上角，省掉一整行「最终解析」
        self.sub_domain.set_hint("→ " + full)

    def load(self, cfg):
        self.provider.set(cfg.get("provider", "tencent"), animate=False)
        self._on_provider(cfg.get("provider", "tencent"))
        self.secret_id.set(cfg.get("secret_id", ""))
        self.secret_key.set(cfg.get("secret_key", ""))
        self.login_token.set(cfg.get("login_token", ""))
        self.domain.set(cfg.get("domain", ""))
        self.sub_domain.set(cfg.get("sub_domain", "@"))
        self._refresh_target()

    def collect(self, draft):
        draft["provider"] = self.provider.value
        draft["secret_id"] = self.secret_id.get()
        draft["secret_key"] = self.secret_key.get()
        draft["login_token"] = self.login_token.get()
        draft["domain"] = self.domain.get()
        draft["sub_domain"] = self.sub_domain.get() or "@"

    def _test(self):
        draft = dict(self.app.cfg)
        self.collect(draft)
        clean, issues = config_mod.validate(draft)
        if issues:
            self.apply_issues(issues)
            self.test_result.configure(text="配置还不完整，先补全标红的项", fg=theme.DANGER)
            return

        from ddns_core import providers

        self.test_btn.set_loading(True, "测试中")
        self.test_result.configure(text="正在连接 DNS 服务商…", fg=theme.MUTED)

        def run():
            ok, message, hints = providers.check_credentials(clean)
            self.app.root.after(0, lambda: finish(ok, message, hints))

        def finish(ok, message, hints):
            self.test_btn.set_loading(False, "测试连接")
            if ok:
                self.test_result.configure(text="✔ " + message, fg=theme.SUCCESS)
            else:
                detail = message
                if hints:
                    detail += "（" + hints[0] + "）"
                self.test_result.configure(text="✘ " + detail, fg=theme.DANGER)

        import threading
        threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------------------
# 检测
# ---------------------------------------------------------------------------

class ListEditor(tk.Frame):
    """可增删的字符串列表编辑器（STUN / HTTP 源）。"""

    def __init__(self, master, placeholder="", bg=theme.SURFACE, defaults=None):
        tk.Frame.__init__(self, master, bg=bg)
        self.bg = bg
        self.placeholder = placeholder
        self.defaults = list(defaults or [])
        self.rows = []
        self.rows_box = tk.Frame(self, bg=bg)
        self.rows_box.pack(fill="x")
        footer = tk.Frame(self, bg=bg)
        footer.pack(fill="x", pady=(theme.px(8), 0))
        self.add_btn = widgets.RoundedButton(footer, "+ 添加", command=self.add_row,
                                             kind="ghost", width=theme.px(84),
                                             height=theme.px(32), bg=bg)
        self.add_btn.pack(side="left")
        if self.defaults:
            self.reset_btn = widgets.RoundedButton(footer, "恢复默认", command=self.reset,
                                                   kind="ghost", width=theme.px(92),
                                                   height=theme.px(32), bg=bg)
            self.reset_btn.pack(side="left", padx=(theme.px(8), 0))
        self.error = tk.Label(self, text="", bg=bg, fg=theme.DANGER,
                              font=theme.FONT_SMALL, anchor="w")
        self.error.pack(fill="x")

    def add_row(self, value=""):
        row = tk.Frame(self.rows_box, bg=self.bg)
        row.pack(fill="x", pady=(0, theme.px(6)))
        holder = tk.Frame(row, bg=theme.FIELD_BG, highlightthickness=1,
                          highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT)
        holder.pack(side="left", fill="x", expand=True)
        entry = tk.Entry(holder, bd=0, relief="flat", bg=theme.FIELD_BG, fg=theme.TEXT,
                         font=theme.FONT_BODY, insertbackground=theme.ACCENT)
        entry.pack(fill="x", padx=theme.px(12), pady=theme.px(8))
        entry.insert(0, value)
        entry.bind("<FocusIn>", lambda e, h=holder: h.configure(
            highlightbackground=theme.ACCENT, highlightcolor=theme.ACCENT))
        entry.bind("<FocusOut>", lambda e, h=holder: h.configure(
            highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT))
        entry.bind("<KeyRelease>", lambda e: self.app_callback())
        remove = widgets.RoundedButton(row, "移除", command=lambda r=row: self._remove(r),
                                       kind="ghost", width=theme.px(64),
                                       height=theme.px(32), bg=self.bg,
                                       font=theme.FONT_SMALL)
        remove.pack(side="left", padx=(theme.px(8), 0))
        self.rows.append((row, entry))
        self.clear_error()

    def _remove(self, row):
        for index, (candidate, _entry) in enumerate(self.rows):
            if candidate is row:
                row.destroy()
                self.rows.pop(index)
                break
        self.app_callback()

    def app_callback(self):
        if getattr(self, "on_change", None):
            self.on_change()

    def reset(self):
        for row, _entry in self.rows:
            row.destroy()
        self.rows = []
        for value in self.defaults:
            self.add_row(value)
        self.app_callback()

    def get(self):
        values = []
        for _row, entry in self.rows:
            text = entry.get().strip()
            if text:
                values.append(text)
        return values

    def set(self, values):
        for row, _entry in self.rows:
            row.destroy()
        self.rows = []
        for value in values or []:
            self.add_row(value)
        self.clear_error()

    def show_error(self, message):
        self.error.configure(text="  " + message)

    def clear_error(self):
        self.error.configure(text="")


SOURCE_HINT = "每行一个地址，按从上到下的顺序尝试"


class DetectionPage(Page):
    key = "detection"
    title = "检测"
    subtitle = "多久检查一次，以及用哪些源判断公网 IP"

    def build(self):
        card = self.card()
        body = card.body
        widgets.SectionTitle(body, "检查频率",
                             "IP 没变化时也要按这个间隔核对 DNS 记录").pack(fill="x")

        row = tk.Frame(body, bg=theme.SURFACE)
        row.pack(fill="x", pady=(theme.px(10), 0))
        row.grid_columnconfigure(0, weight=1)
        row.grid_columnconfigure(1, weight=1)

        self.interval = widgets.TextField(row, "检查间隔（秒）")
        self.interval.grid(row=0, column=0, sticky="ew", padx=(0, theme.px(14)))
        self.max_interval = widgets.TextField(row, "失败后最大间隔（秒）")
        self.max_interval.grid(row=0, column=1, sticky="ew")
        self.fields["check_interval_seconds"] = self.interval
        self.fields["max_interval_seconds"] = self.max_interval

        chips = tk.Frame(body, bg=theme.SURFACE)
        chips.pack(fill="x", pady=(theme.px(10), 0))
        tk.Label(chips, text="常用：", bg=theme.SURFACE, fg=theme.FAINT,
                 font=theme.FONT_SMALL).pack(side="left")
        self.chips = []
        for value in ("60", "300", "600", "1800"):
            chip = widgets.Chip(chips, "%s 秒" % value,
                                command=lambda text, v=value: self._use_preset(v))
            chip.pack(side="left", padx=(0, theme.px(8)))
            self.chips.append((value, chip))

        note = tk.Label(body, text="范围为 10 ~ 86400 秒；最大间隔是失败退避的上限，"
                                   "不能小于检查间隔。",
                        bg=theme.SURFACE, fg=theme.FAINT, font=theme.FONT_SMALL,
                        anchor="w", justify="left")
        note.pack(fill="x", pady=(theme.px(8), 0))
        widgets.wrap_to(body, note)

        # 检测源：STUN 与 HTTP 共用一块文本域，分段切换。
        # 七行 HTTP 源如果用逐行输入框，卡片会被撑到一屏放不下。
        card = self.card(pady=(0, 0))
        body = card.body
        widgets.SectionTitle(body, "检测源",
                             "STUN 优先，全部失败才用 HTTP；每行一个地址").pack(fill="x")
        head = tk.Frame(body, bg=theme.SURFACE)
        head.pack(fill="x", pady=(theme.px(8), 0))
        self.source_tabs = widgets.SegmentedControl(
            head, [("stun", "STUN 检测源"), ("http", "HTTP 备用源")],
            value="stun", command=self._switch_sources, width=theme.px(250))
        self.source_tabs.pack(side="left")
        self.source_hint = tk.Label(head, text="", bg=theme.SURFACE, fg=theme.FAINT,
                                    font=theme.FONT_SMALL, anchor="e")
        self.source_hint.pack(side="right")

        holder = tk.Frame(body, bg=theme.FIELD_BG, highlightthickness=1,
                          highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT)
        holder.pack(fill="x", pady=(theme.px(10), 0))
        self.source_text = tk.Text(
            holder, height=5, bd=0, relief="flat", bg=theme.FIELD_BG, fg=theme.TEXT,
            font=theme.FONT_MONO, insertbackground=theme.ACCENT, wrap="none",
            padx=theme.px(10), pady=theme.px(8), highlightthickness=0)
        self.source_text.pack(fill="x")
        self.source_text.bind("<KeyRelease>", lambda e: self.app.mark_dirty())
        self.source_text.bind("<FocusIn>", lambda e: holder.configure(
            highlightbackground=theme.ACCENT))
        self.source_text.bind("<FocusOut>", lambda e: holder.configure(
            highlightbackground=theme.BORDER))

        foot = tk.Frame(body, bg=theme.SURFACE)
        foot.pack(fill="x", pady=(theme.px(8), 0))
        widgets.RoundedButton(foot, "恢复默认", command=self._reset_sources,
                              kind="ghost", width=theme.px(86),
                              height=theme.px(30)).pack(side="left")
        self.source_error = tk.Label(foot, text=SOURCE_HINT, bg=theme.SURFACE,
                                     fg=theme.FAINT, font=theme.FONT_SMALL,
                                     anchor="w", justify="left")
        self.source_error.pack(side="left", padx=(theme.px(10), 0))

        self._sources = {"stun": list(config_mod.DEFAULT_STUN_SERVERS),
                         "http": list(config_mod.DEFAULT_HTTP_SOURCES)}
        self._active = "stun"
        self.fields["stun_servers"] = self
        self.fields["http_sources"] = self

    # -- 检测源切换 -------------------------------------------------------
    def _switch_sources(self, key):
        self._commit_sources()
        self._active = key
        self._load_source_text()
        self._sync_hint()
        self.app.mark_dirty()

    def _sync_hint(self):
        self.source_hint.configure(
            text="UDP 直连，按顺序尝试" if self._active == "stun"
            else "STUN 全失败时才用，建议保留纯 IPv4 接口")

    def _commit_sources(self):
        text = self.source_text.get("1.0", "end")
        self._sources[self._active] = [line.strip() for line in text.splitlines()
                                       if line.strip()]

    def _load_source_text(self):
        self.source_text.delete("1.0", "end")
        self.source_text.insert("1.0", "\n".join(self._sources[self._active]))

    def _reset_sources(self):
        defaults = (config_mod.DEFAULT_STUN_SERVERS if self._active == "stun"
                    else config_mod.DEFAULT_HTTP_SOURCES)
        self._sources[self._active] = list(defaults)
        self._load_source_text()
        self.clear_error()
        self.app.mark_dirty()

    def show_error(self, message):
        current = str(self.source_error.cget("text"))
        text = message if current == SOURCE_HINT else "%s；%s" % (current, message)
        self.source_error.configure(text=text, fg=theme.DANGER)

    def clear_error(self):
        self.source_error.configure(text=SOURCE_HINT, fg=theme.FAINT)

    def _use_preset(self, value):
        self.interval.set(value)
        for chip_value, chip in self.chips:
            chip.set_selected(chip_value == value)
        self.app.mark_dirty()

    def load(self, cfg):
        self.interval.set(str(cfg.get("check_interval_seconds", 300)))
        self.max_interval.set(str(cfg.get("max_interval_seconds", 1800)))
        self._sources = {"stun": list(cfg.get("stun_servers") or []),
                         "http": list(cfg.get("http_sources") or [])}
        self._active = "stun"
        self.source_tabs.set("stun", animate=False)
        self._load_source_text()
        self._sync_hint()
        self.clear_error()
        current = self.interval.get()
        for chip_value, chip in self.chips:
            chip.set_selected(chip_value == current)

    def collect(self, draft):
        draft["check_interval_seconds"] = self.interval.get()
        draft["max_interval_seconds"] = self.max_interval.get()
        self._commit_sources()
        draft["stun_servers"] = self._sources["stun"]
        draft["http_sources"] = self._sources["http"]


# ---------------------------------------------------------------------------
# 自愈
# ---------------------------------------------------------------------------

class HealPage(Page):
    key = "heal"
    title = "自愈"
    subtitle = "检测源全挂时，主动把网络拉回来"

    def build(self):
        card = self.card()
        body = card.body
        widgets.SectionTitle(
            body, "触发阈值",
            "连续多少轮全部检测源失败后执行自愈。设为 1 表示第一次失败就动手。").pack(fill="x")

        row = tk.Frame(body, bg=theme.SURFACE)
        row.pack(fill="x", pady=(theme.px(10), 0))
        self.heal_after = widgets.TextField(row, "连续失败轮数", hint="1 ~ 100")
        self.heal_after.pack(side="left", fill="x", expand=True)
        self.fields["heal_after_failures"] = self.heal_after

        chips = tk.Frame(body, bg=theme.SURFACE)
        chips.pack(fill="x", pady=(theme.px(8), 0))
        tk.Label(chips, text="常用：", bg=theme.SURFACE, fg=theme.FAINT,
                 font=theme.FONT_SMALL).pack(side="left")
        self.chips = []
        for value in ("1", "2", "3", "5"):
            chip = widgets.Chip(chips, "%s 轮" % value,
                                command=lambda text, v=value: self._use_preset(v))
            chip.pack(side="left", padx=(0, theme.px(8)))
            self.chips.append((value, chip))

        card = self.card()
        body = card.body
        head = tk.Frame(body, bg=theme.SURFACE)
        head.pack(fill="x")
        # 先放开关，再放左侧文字：否则 expand 的文字会把它挤出可视区
        self.auto_renew = widgets.Toggle(head, value=True,
                                         command=lambda v: self.app.mark_dirty())
        self.auto_renew.pack(side="right", padx=(theme.px(16), 0), anchor="n")
        left = tk.Frame(head, bg=theme.SURFACE)
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text="重连网卡", bg=theme.SURFACE, fg=theme.TEXT,
                 font=theme.FONT_CARD_TITLE, anchor="w").pack(fill="x")
        desc = tk.Label(left, text="执行 ipconfig /release 与 /renew，等同插拔网线，"
                                   "会短暂断网几秒",
                        bg=theme.SURFACE, fg=theme.MUTED, font=theme.FONT_SMALL,
                        anchor="w", justify="left", wraplength=theme.px(360))
        desc.pack(fill="x", pady=(4, 0))
        widgets.wrap_to(left, desc)
        self.fields["auto_renew_adapter"] = self.auto_renew

        tip = tk.Frame(body, bg=theme.WARNING_SOFT)
        tip.pack(fill="x", pady=(theme.px(12), 0))
        tip_label = tk.Label(tip, text="关闭后只刷新 DNS 缓存，不会断开网卡；"
                                       "如果服务端要求重新拨号才能恢复公网 IP，请保持开启。",
                             bg=theme.WARNING_SOFT, fg=theme.WARNING,
                             font=theme.FONT_SMALL, anchor="w", justify="left",
                             wraplength=theme.px(420),
                             padx=theme.px(12), pady=theme.px(10))
        tip_label.pack(fill="x")
        widgets.wrap_to(tip, tip_label, inset=theme.px(26))

    def _use_preset(self, value):
        self.heal_after.set(value)
        for chip_value, chip in self.chips:
            chip.set_selected(chip_value == value)
        self.app.mark_dirty()

    def load(self, cfg):
        self.heal_after.set(str(cfg.get("heal_after_failures", 1)))
        self.auto_renew.set(bool(cfg.get("auto_renew_adapter", True)), animate=False)
        current = self.heal_after.get()
        for chip_value, chip in self.chips:
            chip.set_selected(chip_value == current)

    def collect(self, draft):
        draft["heal_after_failures"] = self.heal_after.get()
        draft["auto_renew_adapter"] = self.auto_renew.value


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------

class StatusPage(Page):
    key = "status"
    title = "状态"
    subtitle = "终端监控进程的实时心跳"

    def build(self):
        card = self.card()
        body = card.body
        widgets.SectionTitle(body, "运行状态").pack(fill="x")
        head = tk.Frame(body, bg=theme.SURFACE)
        head.pack(fill="x", pady=(theme.px(16), 0))
        self.pill = widgets.StatusPill(head, bg=theme.SURFACE)
        self.pill.pack(side="left")
        self.detail = tk.Label(head, text="", bg=theme.SURFACE, fg=theme.MUTED,
                               font=theme.FONT_SMALL, anchor="w")
        self.detail.pack(side="left", padx=(theme.px(12), 0))

        self.rows = {}
        grid = tk.Frame(body, bg=theme.SURFACE)
        grid.pack(fill="x", pady=(theme.px(12), 0))
        grid.grid_columnconfigure(1, weight=1)
        for index, (key, label) in enumerate([
                ("target", "监控目标"), ("ip", "当前 IP"), ("source", "检测来源"),
                ("last_check", "最后检查"), ("next_check", "下次检查"),
                ("revision", "配置版本"), ("config_error", "热更新")]):
            tk.Label(grid, text=label, bg=theme.SURFACE, fg=theme.FAINT,
                     font=theme.FONT_SMALL, anchor="w", width=8).grid(
                row=index, column=0, sticky="w", pady=theme.px(2))
            value = tk.Label(grid, text="—", bg=theme.SURFACE, fg=theme.TEXT,
                             font=theme.FONT_SMALL, anchor="w", justify="left",
                             wraplength=theme.px(430))
            value.grid(row=index, column=1, sticky="w", pady=theme.px(2))
            self.rows[key] = value

        actions = tk.Frame(body, bg=theme.SURFACE)
        actions.pack(fill="x", pady=(theme.px(12), 0))
        self.check_btn = widgets.RoundedButton(actions, "立即检查一次",
                                               command=self._request_check,
                                               kind="soft", width=theme.px(124))
        self.check_btn.pack(side="left")
        widgets.RoundedButton(actions, "打开日志目录", command=self._open_logs,
                              kind="ghost", width=theme.px(116)).pack(
            side="left", padx=(theme.px(10), 0))
        self.action_hint = tk.Label(actions, text="", bg=theme.SURFACE, fg=theme.MUTED,
                                    font=theme.FONT_SMALL)
        self.action_hint.pack(side="left", padx=(theme.px(12), 0))

        card = self.card(pady=(0, 0))
        body = card.body
        widgets.SectionTitle(body, "最近日志").pack(fill="x")
        self.log_box = tk.Text(body, height=4, bd=0, relief="flat", bg=theme.FIELD_BG,
                               fg=theme.MUTED, font=theme.FONT_MONO, wrap="none",
                               padx=theme.px(12), pady=theme.px(10),
                               highlightthickness=1, highlightbackground=theme.BORDER)
        self.log_box.pack(fill="x", pady=(theme.px(10), 0))
        self.log_box.configure(state="disabled")

    def load(self, cfg):
        self.rows["target"].configure(text=config_mod.describe_target(cfg))
        self.refresh()

    def collect(self, draft):
        pass

    def refresh(self):
        data = runtime.read()
        state = runtime.summary(data)
        self.pill.set_state(state["state"], state["text"])
        self.detail.configure(text=state["detail"])

        if not data:
            for key in ("ip", "source", "last_check", "next_check", "revision"):
                self.rows[key].configure(text="—", fg=theme.FAINT)
            self.rows["config_error"].configure(text="—", fg=theme.FAINT)
            self.check_btn.set_enabled(False)
        else:
            self.check_btn.set_enabled(state["state"] == "running")
            self.rows["ip"].configure(text=data.get("last_ip") or "—")
            self.rows["source"].configure(text=(data.get("last_source") or "—")
                                          .replace("STUN:", "STUN · ")
                                          .replace("HTTP:", "HTTP · "))
            self.rows["last_check"].configure(text=data.get("last_check_at") or "—")
            self.rows["next_check"].configure(text=data.get("next_check_at") or "—")
            revision = data.get("config_revision")
            self.rows["revision"].configure(
                text="revision %s" % revision if revision is not None else "—")
            error = data.get("config_error")
            self.rows["config_error"].configure(
                text=("失败：" + error) if error else "正常",
                fg=theme.DANGER if error else theme.SUCCESS)
            if data.get("dryrun"):
                self.detail.configure(text=self.detail.cget("text") + " · DRYRUN 测试模式")

        lines = logging_setup.tail_lines(4)
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.insert("1.0", "\n".join(lines) if lines else "（暂无日志）")
        self.log_box.configure(state="disabled")

    def _request_check(self):
        if runtime.request_check():
            self.action_hint.configure(text="已通知监控进程，稍后执行", fg=theme.SUCCESS)
        else:
            self.action_hint.configure(text="无法写入请求文件，请检查目录权限", fg=theme.DANGER)
        self.app.root.after(3200, lambda: self.action_hint.configure(text=""))

    def _open_logs(self):
        folder = paths.log_dir()
        try:
            os.makedirs(folder, exist_ok=True)
            if os.name == "nt":
                os.startfile(folder)
            elif sys_platform() == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            self.app.toast("打不开日志目录：%s" % e, "error")


def sys_platform():
    import sys
    return sys.platform


PAGES = [ConnectionPage, DetectionPage, HealPage, StatusPage]
