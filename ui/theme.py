# -*- coding: utf-8 -*-
"""配色、字体与轻量动画工具。"""

import time
import tkinter.font as tkfont

# 深色侧栏 + 浅色内容区
SIDEBAR_BG = "#0F172A"
SIDEBAR_HOVER = "#1E293B"
SIDEBAR_ACTIVE = "#1E293B"
SIDEBAR_TEXT = "#94A3B8"
SIDEBAR_TEXT_ACTIVE = "#F8FAFC"
SIDEBAR_MUTED = "#64748B"
SIDEBAR_HINT = "#6366F1"

BG = "#F6F7F9"
SURFACE = "#FFFFFF"
FIELD_BG = "#F9FAFB"
BORDER = "#E5E7EB"
BORDER_HOVER = "#D1D5DB"
TEXT = "#0F172A"
MUTED = "#6B7280"
FAINT = "#9CA3AF"

ACCENT = "#4F46E5"
ACCENT_HOVER = "#4338CA"
ACCENT_SOFT = "#EEF2FF"

SUCCESS = "#059669"
SUCCESS_SOFT = "#ECFDF5"
DANGER = "#DC2626"
DANGER_SOFT = "#FEF2F2"
WARNING = "#D97706"
WARNING_SOFT = "#FFFBEB"

FONT_FAMILY = "Microsoft YaHei UI"
FONT_BODY = None
FONT_SMALL = None
FONT_LABEL = None
FONT_TITLE = None
FONT_H2 = None
FONT_CARD_TITLE = None
FONT_MONO = None
FONT_NAV = None
FONT_BUTTON = None

SCALE = 1.0


def setup(root):
    """按系统 DPI 选字体与缩放。"""
    global FONT_FAMILY, FONT_BODY, FONT_SMALL, FONT_LABEL, FONT_TITLE, FONT_H2
    global FONT_CARD_TITLE, FONT_MONO, FONT_NAV, FONT_BUTTON, SCALE

    try:
        families = set(tkfont.families(root))
    except Exception:
        families = set()
    for candidate in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI",
                      "PingFang SC", "Noto Sans CJK SC", "Arial"):
        if candidate in families:
            FONT_FAMILY = candidate
            break

    try:
        dpi = root.winfo_fpixels("1i")
        root.tk.call("tk", "scaling", dpi / 72.0)
        SCALE = max(1.0, dpi / 96.0)
    except Exception:
        SCALE = 1.0

    FONT_BODY = (FONT_FAMILY, 10)
    FONT_SMALL = (FONT_FAMILY, 9)
    FONT_LABEL = (FONT_FAMILY, 9)
    FONT_TITLE = (FONT_FAMILY, 16, "bold")
    FONT_H2 = (FONT_FAMILY, 11, "bold")
    FONT_CARD_TITLE = (FONT_FAMILY, 11, "bold")
    FONT_NAV = (FONT_FAMILY, 10)
    FONT_BUTTON = (FONT_FAMILY, 10)
    FONT_MONO = ("Consolas" if "Consolas" in families else "Courier New", 9)


def px(value):
    """把 96 DPI 下设计的像素换算到当前缩放。"""
    return int(round(value * SCALE))


def _to_rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def mix(color_a, color_b, ratio):
    """线性混合两个颜色，用于 hover / 焦点动画。"""
    ratio = max(0.0, min(1.0, ratio))
    a = _to_rgb(color_a)
    b = _to_rgb(color_b)
    return "#%02x%02x%02x" % tuple(
        int(round(a[i] + (b[i] - a[i]) * ratio)) for i in range(3))


def ease_out_cubic(t):
    return 1 - pow(1 - t, 3)


def ease_out_quart(t):
    return 1 - pow(1 - t, 4)


def ease_out_quint(t):
    """收尾更长、起步更快，界面位移类动画用它最接近「自然减速」。"""
    return 1 - pow(1 - t, 5)


def ease_out_expo(t):
    return 1.0 if t >= 1.0 else 1 - pow(2, -10 * t)


def ease_out_back(t, overshoot=1.32):
    """轻微回弹，只用在开关拇指这类小元素上，幅度大了会显得廉价。"""
    return 1 + (overshoot + 1) * pow(t - 1, 3) + overshoot * pow(t - 1, 2)


def ease_in_out(t):
    return 2 * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 2) / 2


def ease_in_out_cubic(t):
    if t < 0.5:
        return 4 * t * t * t
    return 1 - pow(-2 * t + 2, 3) / 2


class Tween(object):
    """对某个 widget 跑一段按帧推进的动画。

    只负责算进度，具体改什么由 on_frame 决定；调用方应当只更新已有画布
    元素的属性，不要每帧重建，否则会出现闪烁和掉帧。
    """

    FRAME_MS = 16

    def __init__(self, widget, duration_ms, on_frame, on_done=None, easing=ease_out_quint):
        self.widget = widget
        self.duration = max(1, int(duration_ms))
        self.on_frame = on_frame
        self.on_done = on_done
        self.easing = easing
        self._start = None
        self._cancelled = False

    def start(self):
        self._tick()
        return self

    def cancel(self):
        self._cancelled = True

    def _tick(self):
        if self._cancelled:
            return
        try:
            if not self.widget.winfo_exists():
                return
        except Exception:
            return
        now = time.time()
        if self._start is None:
            self._start = now
        progress = min(1.0, (now - self._start) * 1000.0 / self.duration)
        try:
            self.on_frame(self.easing(progress))
        except Exception:
            return
        if progress < 1.0:
            self.widget.after(self.FRAME_MS, self._tick)
        elif self.on_done is not None:
            try:
                self.on_done()
            except Exception:
                pass


def tween(widget, duration_ms, on_frame, on_done=None, easing=ease_out_quint):
    """同一控件上的新动画会取消上一个，避免 hover 抖动。"""
    previous = getattr(widget, "_theme_tween", None)
    if previous is not None:
        previous.cancel()
    animation = Tween(widget, duration_ms, on_frame, on_done, easing).start()
    widget._theme_tween = animation
    return animation
