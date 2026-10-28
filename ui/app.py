# -*- coding: utf-8 -*-
"""主窗口：侧栏导航 + 页面容器 + 保存流程。"""

import os
import tkinter as tk
from tkinter import messagebox

from ddns_core import APP_NAME, VERSION, config as config_mod
from ddns_core import paths, runtime

from . import pages, theme, widgets


class App(object):
    def __init__(self, root):
        self.root = root
        theme.setup(root)
        self.cfg_path = paths.config_path()
        self.load_error = None
        self.dirty = False
        self.cfg = self._load_initial()
        self.pages = []
        self.nav_items = []
        self.field_index = {}
        self.current_index = -1
        self._backed_up = False
        # 首次运行不占用顶部横幅，改在页脚提示，省下一整行高度
        self.first_run = not config_mod.exists(self.cfg_path)

        self._setup_window()
        self._build_sidebar()
        self._build_main()
        self._build_pages()
        self.show_page(0, animate=False)
        self.restore_state()
        self._poll()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Control-s>", lambda e: self.save())
        self.root.bind_all("<MouseWheel>", self._on_wheel)

    # -- 启动 -------------------------------------------------------------
    def _load_initial(self):
        # 文件不存在是首次运行，不算错误，不要走红色横幅
        if not config_mod.exists(self.cfg_path):
            return dict(config_mod.DEFAULT_CONFIG)
        try:
            return config_mod.load(self.cfg_path)
        except config_mod.ConfigError as e:
            self.load_error = e
            cfg = dict(config_mod.DEFAULT_CONFIG)
            # 尽量保留能读出来的字段，方便用户在原基础上修
            try:
                import json
                with open(self.cfg_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    cfg.update(raw)
            except Exception:
                pass
            return cfg

    def _setup_window(self):
        self.root.title("%s 配置 v%s" % (APP_NAME, VERSION))
        self.root.configure(bg=theme.BG)
        # 高 DPI 下逻辑尺寸会换算成很大的物理像素，按屏幕可用区域收敛
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(theme.px(950), int(screen_w * 0.92))
        height = min(theme.px(700), int(screen_h * 0.88))
        self.root.geometry("%dx%d" % (width, height))
        self.root.minsize(min(theme.px(830), int(screen_w * 0.76)),
                          min(theme.px(560), int(screen_h * 0.68)))
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(1, weight=1)
        self.toasts = widgets.ToastHost(self.root)

    def _build_sidebar(self):
        bar = tk.Frame(self.root, bg=theme.SIDEBAR_BG, width=theme.px(196))
        bar.grid(row=0, column=0, sticky="ns")
        bar.pack_propagate(False)

        brand = tk.Frame(bar, bg=theme.SIDEBAR_BG)
        brand.pack(fill="x", padx=theme.px(14), pady=(theme.px(16), theme.px(16)))
        logo = tk.Canvas(brand, width=theme.px(34), height=theme.px(34),
                         bg=theme.SIDEBAR_BG, highlightthickness=0, bd=0)
        logo.pack(side="left")
        widgets.round_rect(logo, 0, 0, theme.px(34), theme.px(34), theme.px(9),
                           fill=theme.ACCENT, outline="")
        logo.create_text(theme.px(17), theme.px(17), text="W", fill="#FFFFFF",
                         font=(theme.FONT_FAMILY, 13, "bold"))
        titles = tk.Frame(brand, bg=theme.SIDEBAR_BG)
        titles.pack(side="left", padx=(theme.px(10), 0))
        tk.Label(titles, text="DDNS-Watchdog", bg=theme.SIDEBAR_BG,
                 fg=theme.SIDEBAR_TEXT_ACTIVE, font=(theme.FONT_FAMILY, 10, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(titles, text="v%s" % VERSION, bg=theme.SIDEBAR_BG,
                 fg=theme.SIDEBAR_MUTED, font=theme.FONT_SMALL, anchor="w").pack(fill="x")

        for index, (icon, text) in enumerate([("●", "连接"), ("◎", "检测"),
                                              ("＋", "自愈"), ("▤", "状态")]):
            item = widgets.NavItem(bar, icon, text,
                                   command=lambda i=index: self.show_page(i))
            item.pack(padx=theme.px(12), pady=theme.px(2))
            self.nav_items.append(item)

        bottom = tk.Frame(bar, bg=theme.SIDEBAR_BG)
        bottom.pack(side="bottom", fill="x", padx=theme.px(14), pady=theme.px(14))
        self.side_hint = tk.Label(bottom, text="", bg=theme.SIDEBAR_BG,
                                  fg=theme.SIDEBAR_MUTED, font=theme.FONT_SMALL,
                                  anchor="w", justify="left", wraplength=theme.px(160))
        self.side_hint.pack(fill="x")
        tk.Label(bottom, text="配置保存后终端自动热更新", bg=theme.SIDEBAR_BG,
                 fg=theme.SIDEBAR_MUTED, font=theme.FONT_SMALL, anchor="w",
                 wraplength=theme.px(160)).pack(fill="x", pady=(theme.px(6), 0))

    def _build_main(self):
        main = tk.Frame(self.root, bg=theme.BG)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_rowconfigure(2, weight=1)
        main.grid_columnconfigure(0, weight=1)

        header = tk.Frame(main, bg=theme.BG)
        header.grid(row=0, column=0, sticky="ew", padx=theme.px(20),
                    pady=(theme.px(16), theme.px(2)))
        header.grid_columnconfigure(0, weight=1)
        self.title_label = tk.Label(header, text="", bg=theme.BG, fg=theme.TEXT,
                                    font=theme.FONT_TITLE, anchor="w")
        self.title_label.grid(row=0, column=0, sticky="w")
        self.subtitle_label = tk.Label(header, text="", bg=theme.BG, fg=theme.MUTED,
                                       font=theme.FONT_SMALL, anchor="w")
        self.subtitle_label.grid(row=1, column=0, sticky="w", pady=(theme.px(2), 0))
        self.pill = widgets.StatusPill(header, bg=theme.BG)
        self.pill.grid(row=0, column=1, rowspan=2, sticky="e")

        self.banner = widgets.Banner(main, bg=theme.BG)
        self.banner.grid(row=1, column=0, sticky="ew", padx=theme.px(20))

        self.container = tk.Frame(main, bg=theme.BG)
        self.container.grid(row=2, column=0, sticky="nsew", pady=(theme.px(8), 0))

        footer = tk.Frame(main, bg=theme.BG)
        footer.grid(row=3, column=0, sticky="ew", padx=theme.px(20),
                    pady=(theme.px(4), theme.px(12)))
        footer.grid_columnconfigure(0, weight=1)
        self.footer_hint = tk.Label(footer, text="", bg=theme.BG, fg=theme.FAINT,
                                    font=theme.FONT_SMALL, anchor="w")
        self.footer_hint.grid(row=0, column=0, sticky="w")
        self.reset_btn = widgets.RoundedButton(footer, "放弃修改", command=self.reload_config,
                                               kind="ghost", width=theme.px(92),
                                               height=theme.px(34), bg=theme.BG)
        self.reset_btn.grid(row=0, column=1, padx=(0, theme.px(8)))
        self.save_btn = widgets.RoundedButton(footer, "保存配置", command=self.save,
                                              kind="primary", width=theme.px(104),
                                              height=theme.px(34), bg=theme.BG)
        self.save_btn.grid(row=0, column=2)

    def _build_pages(self):
        for index, page_class in enumerate(pages.PAGES):
            page = page_class(self.container, self)
            self.pages.append(page)
            for field, widget in page.fields.items():
                self.field_index[field] = (index, widget)

        if self.load_error is not None:
            self.banner.show("配置文件有问题：%s  已按默认值载入，保存后会覆盖原文件。"
                             % self.load_error, "error")

    # -- 页面切换 ---------------------------------------------------------
    def show_page(self, index, animate=True):
        if index == self.current_index:
            return
        page = self.pages[index]
        if 0 <= self.current_index < len(self.pages):
            self.pages[self.current_index].place_forget()
        self.current_index = index
        offset = theme.px(14)
        page.place(x=0, y=offset if animate else 0, relwidth=1, relheight=1)

        self.title_label.configure(text=page.title)
        self.subtitle_label.configure(text=page.subtitle)

        if animate:
            # 内容上浮：位移略大、时长拉长，收尾用强缓出，观感更「沉」
            def rise(t):
                page.place_configure(y=offset * (1 - t))

            theme.tween(page, 260, rise)

            # 标题同步淡入，避免整页像被硬切
            def fade(t):
                ratio = 0.2 + 0.8 * t
                self.title_label.configure(fg=theme.mix(theme.BG, theme.TEXT, ratio))
                self.subtitle_label.configure(fg=theme.mix(theme.BG, theme.MUTED, ratio))

            theme.tween(self.title_label, 220, fade)

        for item_index, item in enumerate(self.nav_items):
            item.set_active(item_index == index)
        if page.key == "status":
            page.refresh()

    # -- 数据 -------------------------------------------------------------
    def restore_state(self):
        for page in self.pages:
            page.load(self.cfg)
        self.dirty = False
        self._sync_title()

    def reload_config(self):
        if self.dirty and not messagebox.askyesno(
                "放弃修改", "当前有未保存的修改，确定重新载入磁盘上的配置吗？"):
            return
        try:
            self.cfg = config_mod.load(self.cfg_path)
            self.load_error = None
            self.banner.hide()
            self.restore_state()
            for page in self.pages:
                page.clear_errors()
            self.toast("已重新载入配置", "info")
        except config_mod.ConfigError as e:
            self._present_issues(e.issues, fallback=str(e))

    def collect(self):
        draft = dict(self.cfg)
        for page in self.pages:
            page.collect(draft)
        return draft

    def mark_dirty(self):
        if self.dirty:
            return
        self.dirty = True
        self._sync_title()

    def _sync_title(self):
        suffix = " *" if self.dirty else ""
        self.root.title("%s 配置 v%s%s" % (APP_NAME, VERSION, suffix))
        if self.dirty:
            text = "有未保存的修改 · Ctrl+S 保存"
        elif self.first_run:
            text = "还没有配置文件，填好后点「保存配置」即可开始"
        else:
            text = "Ctrl+S 保存"
        self.footer_hint.configure(text=text)

    # -- 保存 -------------------------------------------------------------
    def save(self):
        if getattr(self, "_busy", False):
            return
        self.banner.hide()
        for page in self.pages:
            page.clear_errors()

        draft = self.collect()
        draft.pop("_missing", None)
        clean, issues = config_mod.validate(draft)
        if issues:
            self._present_issues(issues)
            return

        self._busy = True
        self.save_btn.set_loading(True, "保存中")
        self.root.after(40, lambda: self._write(clean))

    def _write(self, clean):
        try:
            if self.load_error is not None and not self._backed_up:
                self._backup_invalid()
            saved = config_mod.save(clean, self.cfg_path, note="图形配置界面")
        except config_mod.ConfigError as e:
            self._busy = False
            self.save_btn.set_loading(False, "保存配置")
            self._present_issues(e.issues, fallback=str(e))
            return
        except PermissionError as e:
            self._busy = False
            self.save_btn.set_loading(False, "保存配置")
            message = "没有写入权限：%s。请把程序放到可写目录，或用管理员身份运行。" % e
            self.banner.show(message, "error")
            self.toast("保存失败：权限不足", "error")
            return
        except OSError as e:
            self._busy = False
            self.save_btn.set_loading(False, "保存配置")
            self.banner.show("写入配置文件失败：%s" % e, "error")
            self.toast("保存失败", "error")
            return

        self._busy = False
        self.cfg = saved
        self.load_error = None
        self.first_run = False
        self.dirty = False
        self._sync_title()
        self.banner.hide()
        self.save_btn.set_loading(False, "保存配置")
        for page in self.pages:
            page.clear_errors()

        if runtime.is_alive():
            self.toast("已保存 · 终端将在 1 秒内热更新", "success")
            self.footer_hint.configure(text="已保存 · 终端已收到新配置")
        else:
            self.toast("已保存 · 终端未运行，下次启动生效", "info")
            self.footer_hint.configure(text="已保存 · 终端未运行")
        for page in self.pages:
            if page.key == "status":
                page.load(saved)

    def _backup_invalid(self):
        """把损坏的配置留一份，避免用户改了半天发现原件没了。"""
        try:
            if os.path.exists(self.cfg_path):
                backup = os.path.join(paths.base_dir(), "config.invalid.json")
                os.replace(self.cfg_path, backup)
                self._backed_up = True
                self.toast("原配置已备份为 config.invalid.json", "warning")
        except OSError:
            pass

    def _present_issues(self, issues, fallback=""):
        issues = list(issues or [])
        first_page = None
        unmapped = []
        for issue in issues:
            entry = self.field_index.get(issue.field)
            if entry is None:
                unmapped.append(issue)
                continue
            page_index, widget = entry
            if hasattr(widget, "show_error"):
                widget.show_error(issue.message)
            if first_page is None:
                first_page = page_index

        if not issues and fallback:
            self.banner.show(fallback, "error")
            self.toast("保存失败", "error")
            return

        head = issues[0].as_text() if issues else fallback
        extra = "（另有 %d 处）" % (len(issues) - 1) if len(issues) > 1 else ""
        if not issues:
            self.banner.show(fallback, "error")
            self.toast("保存失败", "error")
            return
        text = "配置有 %d 处需要修改：%s%s" % (len(issues), head, extra)
        if unmapped:
            text = "配置有 %d 处需要修改：%s" % (len(issues), issues[0].as_text())
        self.banner.show(text, "error")
        if first_page is not None:
            self.show_page(first_page)
        self.toast("有 %d 处需要修改" % len(issues), "error")

    # -- 状态轮询 ---------------------------------------------------------
    def _poll(self):
        try:
            state = runtime.summary()
            self.pill.set_state(state["state"], state["text"])
            if state["state"] == "running":
                self.side_hint.configure(text="监控运行中\n保存后自动生效")
                if not self.dirty:
                    self.footer_hint.configure(text="终端运行中 · 保存后 1 秒内热更新")
            else:
                self.side_hint.configure(text="监控未运行\n配置会保存到磁盘")
            if 0 <= self.current_index < len(self.pages):
                page = self.pages[self.current_index]
                if page.key == "status":
                    page.refresh()
        except Exception:
            pass
        self.root.after(1200, self._poll)

    def _on_wheel(self, event):
        """滚轮交给当前页面的滚动容器处理。"""
        if not 0 <= self.current_index < len(self.pages):
            return
        page = self.pages[self.current_index]
        if hasattr(page, "scroll"):
            page.scroll(event.delta)

    def toast(self, message, kind="info"):
        self.toasts.show(message, kind)

    # -- 退出 -------------------------------------------------------------
    def _on_close(self):
        if self.dirty:
            answer = messagebox.askyesnocancel(
                "未保存的修改", "还有未保存的修改，要保存后再退出吗？")
            if answer is None:
                return
            if answer:
                self.save()
                if self.dirty:
                    return
        self.root.destroy()
