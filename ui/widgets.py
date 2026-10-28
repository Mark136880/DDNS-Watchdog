# -*- coding: utf-8 -*-
"""自绘控件：圆角卡片、输入框、开关、分段选择、按钮、Toast 等。

全部基于 tk.Canvas 手绘，目的是在 tkinter 上得到统一的圆角与动效。
"""

import tkinter as tk
import tkinter.font as tkfont

from . import theme


def round_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
    """用平滑多边形近似圆角矩形。"""
    radius = max(0, min(radius, int((x2 - x1) / 2), int((y2 - y1) / 2)))
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1,
        x2, y1 + radius, x2, y2 - radius, x2, y2,
        x2 - radius, y2, x1 + radius, y2, x1, y2,
        x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


# ---------------------------------------------------------------------------
# 卡片
# ---------------------------------------------------------------------------

class Card(tk.Canvas):
    """白色圆角卡片，内容放进 self.body。"""

    def __init__(self, master, pad=22, radius=14, fill=theme.SURFACE,
                 parent_bg=theme.BG, border=theme.BORDER):
        tk.Canvas.__init__(self, master, bg=parent_bg, highlightthickness=0, bd=0)
        self.pad = pad
        self.radius = radius
        self.fill = fill
        self.border = border
        self.body = tk.Frame(self, bg=fill)
        self._window = self.create_window(pad, pad, anchor="nw", window=self.body)
        self.body.bind("<Configure>", self._on_body)
        self.bind("<Configure>", self._redraw)
        self.configure(height=pad * 2 + 20)

    def _on_body(self, _event=None):
        height = self.body.winfo_reqheight() + self.pad * 2
        if abs(int(self.cget("height")) - height) > 1:
            self.configure(height=height)
        width = self.winfo_width() - self.pad * 2
        if width > 10:
            self.itemconfigure(self._window, width=width)

    def _redraw(self, _event=None):
        width = self.winfo_width()
        height = self.winfo_height()
        if width < 4 or height < 4:
            return
        # 卡片宽度变化时同步内容宽度，否则子控件会一直按最初的窄宽度排版
        inner = width - self.pad * 2
        if inner > 10:
            self.itemconfigure(self._window, width=inner)
        self.delete("cardbg")
        round_rect(self, 1, 1, width - 1, height - 1, self.radius,
                   fill=self.fill, outline=self.border, width=1, tags="cardbg")
        self.tag_lower("cardbg")


class SectionTitle(tk.Frame):
    def __init__(self, master, title, desc="", bg=theme.SURFACE):
        tk.Frame.__init__(self, master, bg=bg)
        tk.Label(self, text=title, bg=bg, fg=theme.TEXT,
                 font=theme.FONT_CARD_TITLE, anchor="w").pack(fill="x")
        self._desc = None
        if desc:
            self._desc = tk.Label(self, text=desc, bg=bg, fg=theme.MUTED,
                                  font=theme.FONT_SMALL, anchor="w", justify="left",
                                  wraplength=theme.px(420))
            self._desc.pack(fill="x", pady=(4, 0))
            self.bind("<Configure>", self._rewrap)

    def _rewrap(self, event):
        target = max(theme.px(160), event.width - theme.px(2))
        if str(self._desc.cget("wraplength")) != str(target):
            self._desc.configure(wraplength=target)


def wrap_to(container, label, inset=0):
    """让 label 的换行宽度跟随容器宽度，避免文字被卡片裁掉。"""

    def _apply(event):
        target = max(theme.px(140), event.width - inset)
        if str(label.cget("wraplength")) != str(target):
            label.configure(wraplength=target)

    container.bind("<Configure>", _apply, add="+")


# ---------------------------------------------------------------------------
# 输入框
# ---------------------------------------------------------------------------

class TextField(tk.Frame):
    """带浮动焦点环、错误提示与掩码切换的输入框。"""

    def __init__(self, master, label, value="", hint="", secret=False,
                 placeholder="", bg=theme.SURFACE, on_change=None):
        tk.Frame.__init__(self, master, bg=bg)
        self.bg = bg
        self.secret = secret
        self.placeholder = placeholder
        self._error = ""
        self._focused = False
        self._on_change = on_change
        self._show_plain = not secret

        head = tk.Frame(self, bg=bg)
        head.pack(fill="x")
        tk.Label(head, text=label, bg=bg, fg=theme.MUTED, font=theme.FONT_LABEL,
                 anchor="w").pack(side="left")
        if hint:
            tk.Label(head, text=hint, bg=bg, fg=theme.FAINT, font=theme.FONT_LABEL,
                     anchor="e").pack(side="right")

        height = theme.px(42)
        self.canvas = tk.Canvas(self, bg=bg, height=height, highlightthickness=0, bd=0)
        self.canvas.pack(fill="x", pady=(6, 0))

        self.var = tk.StringVar(value=value)
        self.entry = tk.Entry(
            self.canvas, textvariable=self.var, bd=0, relief="flat",
            bg=theme.FIELD_BG, fg=theme.TEXT, insertbackground=theme.ACCENT,
            font=theme.FONT_BODY, highlightthickness=0,
            show="" if self._show_plain else "•",
            selectbackground=theme.ACCENT_SOFT, selectforeground=theme.TEXT)
        self._entry_window = self.canvas.create_window(
            theme.px(14), height // 2, anchor="w", window=self.entry)
        self.canvas.bind("<Configure>", self._layout)
        self.canvas.bind("<Button-1>", lambda e: self.entry.focus_set())
        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)
        self.var.trace_add("write", self._changed)

        self._suffix_id = None
        if secret:
            self._suffix_id = self.canvas.create_text(
                0, height // 2, anchor="e", text="显示", fill=theme.FAINT,
                font=theme.FONT_SMALL, tags="suffix")
            self.canvas.tag_bind("suffix", "<Button-1>", self._toggle_secret)
            self.canvas.tag_bind("suffix", "<Enter>",
                                 lambda e: self.canvas.itemconfigure("suffix", fill=theme.ACCENT))
            self.canvas.tag_bind("suffix", "<Leave>",
                                 lambda e: self.canvas.itemconfigure("suffix", fill=theme.FAINT))

        self.error_label = tk.Label(self, text="", bg=bg, fg=theme.DANGER,
                                    font=theme.FONT_SMALL, anchor="w", justify="left")
        self._error_visible = False
        self._border = theme.BORDER
        self._border_ratio = 0.0
        self.canvas.after(10, self._paint)

    # -- 布局与绘制 -------------------------------------------------------
    def _layout(self, event=None):
        width = self.canvas.winfo_width()
        reserve = theme.px(58) if self.secret else theme.px(28)
        self.canvas.coords(self._entry_window, theme.px(14), self.canvas.winfo_height() // 2)
        self.canvas.itemconfigure(self._entry_window, width=max(20, width - reserve))
        if self._suffix_id is not None:
            self.canvas.coords(self._suffix_id, width - theme.px(14),
                               self.canvas.winfo_height() // 2)
        self._paint()

    def _paint(self, _ratio=None):
        if not self.canvas.winfo_exists():
            return
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width < 4:
            # 还没被布局到屏幕上；<Configure> 触发后会重画，这里不轮询
            return
        self.canvas.delete("frame")
        round_rect(self.canvas, 1, 1, width - 1, height - 1, theme.px(9),
                   fill=theme.FIELD_BG, outline=self._border, width=1, tags="frame")
        self.canvas.tag_lower("frame")

    def _animate_border(self, target, ratio_from=0.0):
        start = self._border

        def step(t):
            color = theme.mix(start, target, t)
            self._border = color
            width = self.canvas.winfo_width()
            height = self.canvas.winfo_height()
            if width < 4:
                return
            self.canvas.delete("frame")
            round_rect(self.canvas, 1, 1, width - 1, height - 1, theme.px(9),
                       fill=theme.FIELD_BG, outline=color, width=1, tags="frame")
            self.canvas.tag_lower("frame")

        theme.tween(self.canvas, 140, step)

    def _on_focus_in(self, _event=None):
        self._focused = True
        self._animate_border(theme.ACCENT if not self._error else theme.DANGER)

    def _on_focus_out(self, _event=None):
        self._focused = False
        self._animate_border(theme.DANGER if self._error else theme.BORDER)

    def _changed(self, *_args):
        if self._error:
            self.clear_error()
        if self._on_change:
            self._on_change(self.var.get())

    def _toggle_secret(self, _event=None):
        self._show_plain = not self._show_plain
        self.entry.configure(show="" if self._show_plain else "•")
        self.canvas.itemconfigure("suffix", text="隐藏" if self._show_plain else "显示")
        self.entry.icursor("end")

    # -- 数据 -------------------------------------------------------------
    def get(self):
        return self.var.get().strip()

    def set(self, value):
        self.var.set(value or "")
        if self._error:
            self.clear_error()

    def show_error(self, message):
        self._error = message
        self.error_label.configure(text="  " + message)
        if not self._error_visible:
            self.error_label.pack(fill="x", pady=(5, 0))
            self._error_visible = True
        self._animate_border(theme.DANGER)
        self._flash()

    def clear_error(self):
        self._error = ""
        if self._error_visible:
            self.error_label.pack_forget()
            self._error_visible = False
        self._animate_border(theme.ACCENT if self._focused else theme.BORDER)

    def _flash(self):
        """错误时的两次红色呼吸，替代抖动（tk 里移动控件容易破坏布局）。"""
        frames = [theme.DANGER, "#FCA5A5", theme.DANGER]

        def step(index=0):
            if index >= len(frames) or not self.canvas.winfo_exists():
                self._animate_border(theme.DANGER)
                return
            color = frames[index]
            width = self.canvas.winfo_width()
            height = self.canvas.winfo_height()
            if width > 4:
                self.canvas.delete("frame")
                round_rect(self.canvas, 1, 1, width - 1, height - 1, theme.px(9),
                           fill=theme.FIELD_BG, outline=color, width=1, tags="frame")
                self.canvas.tag_lower("frame")
            self.canvas.after(110, lambda: step(index + 1))

        step()


class Toggle(tk.Canvas):
    """带滑块动画的开关。"""

    def __init__(self, master, value=True, command=None, bg=theme.SURFACE):
        self.width = theme.px(46)
        self.height = theme.px(26)
        tk.Canvas.__init__(self, master, width=self.width, height=self.height,
                           bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.command = command
        self.value = bool(value)
        self._pos = 1.0 if self.value else 0.0
        self.bind("<Button-1>", self._click)
        self.after(10, self._paint)

    def _paint(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        track = theme.mix(theme.BORDER_HOVER, theme.ACCENT, self._pos)
        round_rect(self, 1, 1, self.width - 1, self.height - 1,
                   (self.height - 2) // 2, fill=track, outline="")
        radius = (self.height - theme.px(8)) // 2
        center_x = theme.px(4) + radius + self._pos * (self.width - theme.px(8) - 2 * radius)
        center_y = self.height // 2
        self.create_oval(center_x - radius, center_y - radius,
                         center_x + radius, center_y + radius,
                         fill=theme.SURFACE, outline="")

    def _click(self, _event=None):
        self.set(not self.value, animate=True)
        if self.command:
            self.command(self.value)

    def set(self, value, animate=True):
        self.value = bool(value)
        target = 1.0 if self.value else 0.0
        if not animate:
            self._pos = target
            self._paint()
            return
        start = self._pos

        def step(t):
            self._pos = start + (target - start) * t
            self._paint()

        theme.tween(self, 180, step)


class SegmentedControl(tk.Canvas):
    """分段选择器，选中态用滑块动画过渡。"""

    def __init__(self, master, options, value=None, command=None,
                 bg=theme.SURFACE, width=theme.px(300)):
        self.height = theme.px(40)
        tk.Canvas.__init__(self, master, height=self.height, width=width,
                           bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.options = list(options)
        self.value = value if value is not None else self.options[0][0]
        self.command = command
        self._index = self._index_of(self.value)
        self._pos = float(self._index)
        self.bind("<Button-1>", self._click)
        self.bind("<Configure>", lambda e: self._paint())
        self.after(10, self._paint)

    def _index_of(self, value):
        for index, (key, _label) in enumerate(self.options):
            if key == value:
                return index
        return 0

    def _segment_width(self):
        return max(1, (self.winfo_width() - theme.px(6)) / float(len(self.options)))

    def _paint(self, _anim=None):
        if not self.winfo_exists():
            return
        width = self.winfo_width()
        height = self.height
        if width < 10:
            return
        self.delete("all")
        round_rect(self, 0, 0, width, height, theme.px(10),
                   fill=theme.FIELD_BG, outline=theme.BORDER, width=1)
        seg_w = self._segment_width()
        pill_x = theme.px(3) + self._pos * seg_w
        round_rect(self, pill_x + 2, theme.px(3), pill_x + seg_w - 2, height - theme.px(3),
                   theme.px(8), fill=theme.SURFACE, outline=theme.BORDER, width=1)
        for index, (_key, label) in enumerate(self.options):
            center_x = theme.px(3) + seg_w * index + seg_w / 2
            active = abs(self._pos - index) < 0.5
            self.create_text(center_x, height / 2, text=label,
                             fill=theme.TEXT if active else theme.MUTED,
                             font=theme.FONT_BUTTON if active else theme.FONT_BODY)

    def _click(self, event):
        seg_w = self._segment_width()
        index = int(max(0, min(len(self.options) - 1, (event.x - theme.px(3)) // seg_w)))
        key = self.options[index][0]
        if key == self.value:
            return
        self.value = key
        start = self._pos
        target = float(index)
        self._index = index

        def step(t):
            self._pos = start + (target - start) * t
            self._paint()

        theme.tween(self, 200, step, easing=theme.ease_in_out)
        if self.command:
            self.command(key)

    def set(self, value, animate=True):
        self.value = value
        index = self._index_of(value)
        if not animate:
            self._pos = float(index)
            self._paint()
            return
        start = self._pos

        def step(t):
            self._pos = start + (index - start) * t
            self._paint()

        theme.tween(self, 200, step, easing=theme.ease_in_out)


class RoundedButton(tk.Canvas):
    """带 hover / press / loading 状态的圆角按钮。"""

    KINDS = {
        "primary": (theme.ACCENT, theme.ACCENT_HOVER, "#FFFFFF", ""),
        "ghost": (theme.SURFACE, theme.FIELD_BG, theme.TEXT, theme.BORDER),
        "danger": (theme.DANGER_SOFT, "#FEE2E2", theme.DANGER, ""),
        "soft": (theme.ACCENT_SOFT, "#E0E7FF", theme.ACCENT, ""),
    }

    def __init__(self, master, text, command=None, kind="primary", width=None,
                 height=None, bg=theme.SURFACE, font=None):
        self.kind = kind
        base, hover, fg, border = self.KINDS[kind]
        self.base, self.hover = base, hover
        self.fg = fg
        self.border = border or base
        self.text = text
        self.command = command
        self._enabled = True
        self._loading = False
        self._hover_ratio = 0.0
        font = font or theme.FONT_BUTTON
        measure = tkfont.Font(font=font)
        width = width or (measure.measure(text) + theme.px(38))
        height = height or theme.px(36)
        self._cw, self._ch = width, height
        tk.Canvas.__init__(self, master, width=width, height=height, bg=bg,
                           highlightthickness=0, bd=0, cursor="hand2")
        self._font = font
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.after(10, self._paint)

    def _paint(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        fill = theme.mix(self.base, self.hover, self._hover_ratio)
        outline = theme.mix(self.border, self.hover, self._hover_ratio) if self.border else ""
        if not self._enabled:
            fill = theme.mix(self.base, theme.BORDER, 0.55)
            outline = theme.BORDER
        round_rect(self, 1, 1, self._cw - 1, self._ch - 1, theme.px(9),
                   fill=fill, outline=outline, width=1)
        fg = self.fg if self._enabled else theme.FAINT
        self.create_text(self._cw / 2, self._ch / 2, text=self.text, fill=fg, font=self._font)

    def _on_enter(self, _event=None):
        if not self._enabled or self._loading:
            return
        start = self._hover_ratio

        def step(t):
            self._hover_ratio = start + (1 - start) * t
            self._paint()

        theme.tween(self, 130, step)

    def _on_leave(self, _event=None):
        start = self._hover_ratio

        def step(t):
            self._hover_ratio = start * (1 - t)
            self._paint()

        theme.tween(self, 160, step)

    def _on_press(self, _event=None):
        if not self._enabled or self._loading:
            return
        self.configure(cursor="arrow")

    def _on_release(self, _event=None):
        if not self._enabled or self._loading:
            return
        self.configure(cursor="hand2")
        if self.command:
            self.command()

    def set_text(self, text):
        self.text = text
        self._paint()

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if enabled else "arrow")
        self._paint()

    def set_loading(self, loading, text="处理中"):
        """加载态：文字后跟三个跳动的点。"""
        self._loading = bool(loading)
        if not loading:
            self.set_text(text)
            return
        self.set_enabled(True)
        dots = [0]

        def tick():
            if not self._loading or not self.winfo_exists():
                return
            dots[0] = (dots[0] + 1) % 4
            self.text = text + "·" * dots[0]
            self._paint()
            self.after(320, tick)

        tick()


class Chip(tk.Canvas):
    """小尺寸快捷选项（如预设间隔）。"""

    def __init__(self, master, text, command=None, bg=theme.SURFACE,
                 selected=False, color=theme.ACCENT):
        font = theme.FONT_SMALL
        measure = tkfont.Font(font=font)
        self._cw = measure.measure(text) + theme.px(22)
        self._ch = theme.px(26)
        tk.Canvas.__init__(self, master, width=self._cw, height=self._ch, bg=bg,
                           highlightthickness=0, bd=0, cursor="hand2")
        self.text = text
        self.command = command
        self.selected = selected
        self.color = color
        self._ratio = 1.0 if selected else 0.0
        self.bind("<Button-1>", lambda e: self.command and self.command(text))
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.after(10, self._paint)

    def _hover(self, entering):
        target = 1.0 if entering else (1.0 if self.selected else 0.0)

        def step(t):
            self._ratio = self._ratio + (target - self._ratio) * t
            self._paint()

        theme.tween(self, 120, step)

    def _paint(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        fill = theme.mix(theme.SURFACE, theme.ACCENT_SOFT, self._ratio)
        outline = theme.mix(theme.BORDER, self.color, self._ratio)
        round_rect(self, 1, 1, self._cw - 1, self._ch - 1, self._ch // 2,
                   fill=fill, outline=outline, width=1)
        self.create_text(self._cw / 2, self._ch / 2, text=self.text,
                         fill=theme.mix(theme.MUTED, self.color, self._ratio),
                         font=theme.FONT_SMALL)

    def set_selected(self, selected):
        self.selected = selected
        self._hover(False)
        self._ratio = 1.0 if selected else 0.0
        self._paint()


class NavItem(tk.Canvas):
    """侧栏导航项：图标 + 文字，激活时有滑块高亮。"""

    def __init__(self, master, icon, text, command=None):
        self._cw = theme.px(184)
        self._ch = theme.px(42)
        tk.Canvas.__init__(self, master, width=self._cw, height=self._ch,
                           bg=theme.SIDEBAR_BG, highlightthickness=0, bd=0,
                           cursor="hand2")
        self.icon = icon
        self.text = text
        self.command = command
        self.active = False
        self._hover = 0.0
        self.bind("<Button-1>", lambda e: self.command and self.command())
        self.bind("<Enter>", lambda e: self._animate(1.0))
        self.bind("<Leave>", lambda e: self._animate(0.0))
        self.after(10, self._paint)

    def _animate(self, target):
        start = self._hover

        def step(t):
            self._hover = start + (target - start) * t
            self._paint()

        theme.tween(self, 140, step)

    def set_active(self, active):
        self.active = bool(active)
        self._paint()

    def _paint(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        if self.active or self._hover > 0.01:
            fill = theme.SIDEBAR_ACTIVE if self.active else theme.mix(
                theme.SIDEBAR_BG, theme.SIDEBAR_HOVER, self._hover)
            round_rect(self, 1, 1, self._cw - 1, self._ch - 1, theme.px(9),
                       fill=fill, outline="")
        if self.active:
            round_rect(self, 0, self._ch / 2 - theme.px(9), theme.px(3),
                       self._ch / 2 + theme.px(9), theme.px(2),
                       fill=theme.SIDEBAR_HINT, outline="")
        color = theme.SIDEBAR_TEXT_ACTIVE if self.active else theme.mix(
            theme.SIDEBAR_TEXT, theme.SIDEBAR_TEXT_ACTIVE, self._hover * 0.7)
        self.create_text(theme.px(20), self._ch / 2, text=self.icon,
                         fill=color, font=(theme.FONT_FAMILY, 11))
        self.create_text(theme.px(42), self._ch / 2, text=self.text, anchor="w",
                         fill=color, font=theme.FONT_NAV)


class ToastHost(object):
    """右上角堆叠提示。"""

    def __init__(self, root):
        self.root = root
        self.items = []

    def show(self, message, kind="info", duration=2800):
        colors = {
            "info": theme.ACCENT,
            "success": theme.SUCCESS,
            "error": theme.DANGER,
            "warning": theme.WARNING,
        }
        accent = colors.get(kind, theme.ACCENT)
        font = theme.FONT_BODY
        measure = tkfont.Font(font=font)
        width = min(theme.px(420), measure.measure(message) + theme.px(58))
        height = theme.px(44)

        canvas = tk.Canvas(self.root, width=width, height=height, bg=theme.BG,
                           highlightthickness=0, bd=0)
        round_rect(canvas, 1, 1, width - 1, height - 1, theme.px(10),
                   fill="#111827", outline="")
        canvas.create_oval(theme.px(16), height / 2 - theme.px(3),
                           theme.px(22), height / 2 + theme.px(3),
                           fill=accent, outline="")
        canvas.create_text(theme.px(34), height / 2, text=message, anchor="w",
                           fill="#F9FAFB", font=font, width=width - theme.px(48))

        index = len(self.items)
        target_y = self.root.winfo_height() - theme.px(24) - height - index * (height + 10)
        canvas.place(relx=1.0, x=-theme.px(24), y=target_y + theme.px(16), anchor="ne")
        self.items.append(canvas)

        def slide(t):
            canvas.place_configure(y=target_y + (1 - t) * theme.px(16))
            canvas.place_configure(x=-theme.px(24))

        theme.tween(canvas, 220, slide)
        canvas.after(duration, lambda: self._dismiss(canvas))

    def _dismiss(self, canvas):
        if canvas not in self.items:
            return
        self.items.remove(canvas)
        try:
            canvas.destroy()
        except Exception:
            pass


class Banner(tk.Canvas):
    """页面顶部的提示条，用于校验失败等需要解释的场景。"""

    def __init__(self, master, bg=theme.BG):
        tk.Canvas.__init__(self, master, bg=bg, height=0, highlightthickness=0, bd=0)
        self._message = ""
        self._kind = "error"
        self._visible = False
        self._retry = 0

    def show(self, message, kind="error"):
        self._message = message
        self._kind = kind
        self._visible = True
        self._retry = 0
        self.after(10, self._paint)

    def hide(self):
        self._visible = False
        self.configure(height=0)
        self.delete("all")

    def _paint(self):
        if not self._visible or not self.winfo_exists():
            return
        width = self.winfo_width()
        if width < 20:
            if self._retry < 20:
                self._retry += 1
                self.after(30, self._paint)
            return
        palette = {
            "error": (theme.DANGER_SOFT, theme.DANGER),
            "success": (theme.SUCCESS_SOFT, theme.SUCCESS),
            "warning": (theme.WARNING_SOFT, theme.WARNING),
            "info": (theme.ACCENT_SOFT, theme.ACCENT),
        }
        fill, accent = palette.get(self._kind, palette["info"])
        font = tkfont.Font(font=theme.FONT_SMALL)
        # 粗略按字符宽度换行，避免提示条过高
        per_line = max(20, int((width - theme.px(48)) / max(1, font.measure("测"))))
        text = self._message
        lines = [text[i:i + per_line] for i in range(0, len(text), per_line)] or [""]
        height = theme.px(20) + len(lines) * theme.px(18)
        self.configure(height=height)
        self.delete("all")
        round_rect(self, 0, 0, width, height, theme.px(10), fill=fill, outline="")
        round_rect(self, 0, theme.px(8), theme.px(3), height - theme.px(8),
                   theme.px(2), fill=accent, outline="")
        for index, line in enumerate(lines):
            self.create_text(theme.px(16), theme.px(12) + index * theme.px(18),
                             text=line, anchor="nw", fill=accent, font=theme.FONT_SMALL)


class StatusPill(tk.Canvas):
    """状态胶囊：色点 + 文本，运行中有呼吸动画。"""

    def __init__(self, master, bg=theme.BG):
        self._cw = theme.px(104)
        self._ch = theme.px(28)
        tk.Canvas.__init__(self, master, width=self._cw, height=self._ch, bg=bg,
                           highlightthickness=0, bd=0)
        self.state = "stopped"
        self.text = "未运行"
        self._pulse = 0.0
        self._pulsing = False
        self.after(10, self._paint)

    def set_state(self, state, text):
        self.state = state
        self.text = text
        if state == "running" and not self._pulsing:
            self._pulsing = True
            self._start_pulse()
        if state != "running":
            self._pulsing = False
        self._paint()

    def _start_pulse(self):
        def tick():
            if not self._pulsing or not self.winfo_exists():
                return
            import math
            self._pulse = (math.sin(self._pulse * 3.0) + 1) / 2
            self._paint()
            self.after(120, tick)

        tick()

    def _paint(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        palette = {
            "running": (theme.SUCCESS_SOFT, theme.SUCCESS),
            "stale": (theme.WARNING_SOFT, theme.WARNING),
            "stopped": (theme.FIELD_BG, theme.FAINT),
        }
        fill, accent = palette.get(self.state, palette["stopped"])
        round_rect(self, 1, 1, self._cw - 1, self._ch - 1, self._ch // 2,
                   fill=fill, outline="")
        radius = theme.px(3) + (theme.px(1) * self._pulse if self._pulsing else 0)
        cx = theme.px(14)
        cy = self._ch / 2
        if self._pulsing:
            self.create_oval(cx - radius - theme.px(2), cy - radius - theme.px(2),
                             cx + radius + theme.px(2), cy + radius + theme.px(2),
                             fill="", outline=accent, width=1)
        self.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                         fill=accent, outline="")
        self.create_text(theme.px(26), cy, text=self.text, anchor="w",
                         fill=accent, font=theme.FONT_SMALL)


class ScrollFrame(tk.Frame):
    """纵向滚动容器（不显示滚动条，保持极简）。"""

    def __init__(self, master, bg=theme.BG):
        tk.Frame.__init__(self, master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window(0, 0, anchor="nw", window=self.inner)
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)

    def _on_inner(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)

    def scroll(self, delta):
        if self.canvas.bbox("all") is None:
            return
        _x1, y1, _x2, y2 = self.canvas.bbox("all")
        if y2 <= self.canvas.winfo_height():
            return
        self.canvas.yview_scroll(int(-delta / 3), "units")
