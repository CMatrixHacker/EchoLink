"""Android 端视觉主题 —— 与 Windows 控制台同一套配色语言。

用法:main.py 里 ``from echolink.client.android.theme import NeonButton as Button``
即可让全 app 的按钮换肤;其余控件通过本模块注册的全局 KV 规则统一着色。
"""

from __future__ import annotations

import os

from kivy.animation import Animation
from kivy.core.window import Window
from kivy.graphics import Color, Ellipse, Rectangle, RoundedRectangle
from kivy.metrics import dp
from kivy.properties import BooleanProperty, ListProperty, NumericProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.widget import Widget

# 与 echolink/client/ui/theme.py 保持一致
BG_TOP = (0.016, 0.024, 0.051, 1.0)       # #04060D
BG_MID = (0.024, 0.043, 0.098, 1.0)       # #060B19
ACCENT = (0.13, 0.90, 0.88, 1.0)          # 青 #22E5E0
ACCENT_DIM = (0.10, 0.72, 0.76, 1.0)
INDIGO = (0.39, 0.40, 0.95, 1.0)          # #6366F1
VIOLET = (0.66, 0.33, 0.97, 1.0)          # #A855F7
TEXT = (0.91, 0.95, 1.0, 1.0)
TEXT_DIM = (0.56, 0.64, 0.75, 1.0)
TEXT_FAINT = (0.35, 0.42, 0.52, 1.0)
MUTED = TEXT_DIM  # 兼容旧命名
DANGER = (0.98, 0.44, 0.52, 1.0)          # #FB7185
OK = (0.20, 0.83, 0.60, 1.0)              # #34D399
CARD = (0.055, 0.086, 0.15, 0.88)
CARD_BORDER = (0.47, 0.67, 1.0, 0.16)

GRADIENT_STOPS = [BG_TOP, BG_MID, (0.05, 0.06, 0.14, 1.0), BG_TOP]


def _gradient_bytes(stops, height: int = 256) -> bytes:
    """纵向渐变的 RGBA 字节流(GL 的第 0 行在底部,所以倒着生成)。"""
    rows = []
    count = len(stops) - 1
    for y in range(height):
        t = 1.0 - y / (height - 1)  # 顶部对应纹理最高行
        seg = min(int(t * count), count - 1)
        k = t * count - seg
        a = stops[seg]
        b = stops[seg + 1]
        rows.append(tuple(a[i] + (b[i] - a[i]) * k for i in range(4)))
    return bytes(int(c * 255) for rgba in rows for c in rgba)


def gradient_texture(stops=None):
    from kivy.graphics.texture import Texture

    data = _gradient_bytes(stops or GRADIENT_STOPS)
    texture = Texture.create(size=(1, 256))
    texture.blit_buffer(data, colorfmt="rgba", bufferfmt="ubyte")
    return texture


class GradientBackground(FloatLayout):
    """全屏极光底:纵向渐变 + 两团缓慢漂移的色晕。"""

    drift = NumericProperty(0.0)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        with self.canvas.before:
            self._tex = gradient_texture()
            Color(1, 1, 1, 1)
            self._bg = Rectangle(texture=self._tex, pos=self.pos, size=self.size)
            self._glow_a = Color(*VIOLET, 0.10)
            self._blob_a = Ellipse(pos=(-Window.width * 0.3, Window.height * 0.55),
                                   size=(Window.width * 1.1, Window.width * 1.1))
            self._glow_b = Color(*ACCENT, 0.08)
            self._blob_b = Ellipse(pos=(Window.width * 0.4, -Window.height * 0.25),
                                   size=(Window.width * 0.9, Window.width * 0.9))
        self.bind(pos=self._sync, size=self._sync)
        anim = Animation(drift=1.0, duration=14.0) + Animation(drift=0.0, duration=14.0)
        anim.repeat = True
        anim.start(self)
        self.bind(drift=self._breathe)

    def _sync(self, *_args) -> None:
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._breathe(self, self.drift)

    def _breathe(self, _widget, t: float) -> None:
        import math

        w, h = Window.width, Window.height
        ax = math.sin(t * 2 * math.pi) * w * 0.12
        ay = math.cos(t * 1.4 * math.pi) * h * 0.08
        self._blob_a.pos = (-w * 0.30 + ax, h * 0.55 + ay)
        self._blob_b.pos = (w * 0.40 - ax, -h * 0.25 - ay)


class Card(BoxLayout):
    """圆角玻璃卡片容器。"""

    tint = ListProperty(CARD)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        with self.canvas.before:
            Color(*self.tint)
            self._fill = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(14)])
            Color(*CARD_BORDER)
            self._border = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(14)])
        self.bind(pos=self._sync, size=self._sync, tint=self._sync)

    def _sync(self, *_args) -> None:
        self._fill.pos = self.pos
        self._fill.size = self.size
        self._border.pos = self.pos
        self._border.size = self.size


class NeonButton(Button):
    """霓虹按钮:圆角、按压回弹;默认低调配色,主行动按钮显式传 primary=True。"""

    primary = BooleanProperty(False)
    danger = BooleanProperty(False)
    glow = NumericProperty(0.0)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.background_normal = ""
        self.background_down = ""
        self.background_color = (0, 0, 0, 0)
        self.font_size = dp(14)
        self.bold = True
        self.markup = True
        with self.canvas.before:
            self._fill_color = Color(*ACCENT, 1.0)
            self._fill = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(12)])
            self._shine = Color(1, 1, 1, 0.0)
            self._shine_rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(12)])
        self.bind(pos=self._sync, size=self._sync, primary=self._sync, danger=self._sync,
                  glow=self._sync, state=self._sync)
        self._sync()

    def _fill_color_pair(self) -> tuple:
        if self.danger:
            base = DANGER
            dark = (0.73, 0.11, 0.29, 1.0)
            text = TEXT
        elif self.primary:
            base = ACCENT
            dark = INDIGO
            text = (0.02, 0.07, 0.10, 1.0)
        else:
            base = (0.11, 0.15, 0.25, 1.0)
            dark = (0.075, 0.11, 0.18, 1.0)
            text = TEXT
        return base, dark, text

    def _sync(self, *_args) -> None:
        base, dark, text = self._fill_color_pair()
        pressed = self.state == "down"
        lift = self.glow
        mixed = tuple(base[i] + (dark[i] - base[i]) * (1 - lift) for i in range(4))
        self._fill_color.rgba = tuple(
            min(1.0, mixed[i] + (0.10 if pressed else 0.0)) for i in range(4)
        )
        self._fill.pos = self.pos
        self._fill.size = self.size
        self._fill.radius = [dp(12)]
        self._shine.rgba = (1, 1, 1, 0.10 + 0.10 * lift)
        self._shine_rect.pos = self.pos
        self._shine_rect.size = (self.size[0], self.size[1] * 0.55)
        self._shine_rect.radius = [dp(12)]
        self.color = text

    def on_state(self, _widget, state) -> None:
        target = 1.0 if state == "down" else 0.0
        Animation(glow=target, d=0.28, t="out_back").start(self)
        Animation(font_size=dp(13.2) if state == "down" else dp(14), d=0.12, t="out_quint").start(self)


def stagger_in(widgets, interval: float = 0.035, duration: float = 0.3) -> None:
    """列表进场动画:逐个淡入 + 轻微上移,节奏感比一次性出现好。"""
    for index, widget in enumerate(widgets):
        widget.opacity = 0
        y0 = widget.y
        widget.y = y0 - dp(8)
        Animation(opacity=1.0, d=duration, t="out_quint").start(widget)
        Animation(y=y0, d=duration + 0.05, t="out_back").start(widget)
        _ = index, interval


KV_RULES = """
<TextInput>:
    background_normal: ''
    background_active: ''
    background_color: 0, 0, 0, 0
    foreground_color: {TEXT}
    cursor_color: {ACCENT}
    font_size: dp(14)
    padding: dp(12), dp(12), dp(12), dp(12)
    canvas.before:
        Color:
            rgba: (0.05, 0.09, 0.16, 0.92)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(10)]
        Color:
            rgba: (0.37, 0.92, 1.0, 0.55) if self.focus else (0.47, 0.67, 1.0, 0.18)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(10)]

<Label>:
    color: {TEXT}
    font_size: dp(14)

<Slider>:
    canvas:
        Color:
            rgba: (0.09, 0.13, 0.21, 1)
        RoundedRectangle:
            pos: self.x, self.center_y - dp(3)
            size: self.width, dp(6)
            radius: [dp(3)]
        Color:
            rgba: (0.13, 0.90, 0.88, 1)
        RoundedRectangle:
            pos: self.x, self.center_y - dp(3)
            size: max(dp(6), self.width * self.value_normalized), dp(6)
            radius: [dp(3)]
        Color:
            rgba: (0.95, 1.0, 1.0, 1)
        Ellipse:
            pos: self.x + self.width * self.value_normalized - dp(9), self.center_y - dp(9)
            size: dp(18), dp(18)

<ProgressBar>:
    background_color: 0, 0, 0, 0
    canvas:
        Color:
            rgba: (0.09, 0.13, 0.21, 1)
        RoundedRectangle:
            pos: self.x, self.center_y - dp(3)
            size: self.width, dp(6)
            radius: [dp(3)]
        Color:
            rgba: (0.13, 0.90, 0.88, 1)
        RoundedRectangle:
            pos: self.x, self.center_y - dp(3)
            size: self.width * self.value_normalized, dp(6)
            radius: [dp(3)]

<Popup>:
    background_color: (0.043, 0.071, 0.133, 0.99)
    separator_color: (0.13, 0.90, 0.88, 0.55)
    title_color: {TEXT}
    title_size: dp(15)
    title_align: left

<TabbedPanel>:
    background_image: ''
    strip_image: ''
    background_color: (0.043, 0.071, 0.133, 1)

<TabbedPanelItem>:
    background_normal: ''
    background_down: ''
    background_disabled_normal: ''
    background_disabled_down: ''
    color: {TEXT_DIM}
    bold: True
    canvas.after:
        Color:
            rgba: (0.10, 0.16, 0.27, 0.98) if self.state == 'down' else (0.06, 0.10, 0.18, 0.92)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(9), dp(9), 0, 0]
        Color:
            rgba: (0.13, 0.90, 0.88, 0.9) if self.state == 'down' else (0, 0, 0, 0)
        Line:
            rounded_rectangle: (self.x + dp(6), self.y, self.width - dp(12), dp(2), dp(1))
"""


def _register_cjk_font() -> None:
    """把默认字体 'Roboto' 重定向到含中文字形的系统字体。

    Kivy 自带的 Roboto 没有汉字,安卓真机/桌面预览都会显示方块。
    直接以 'Roboto' 之名注册 CJK 字体,所有控件零改动即可正常显示中文。
    """
    from kivy.core.text import LabelBase

    candidates = [
        "/system/fonts/NotoSansCJK-Regular.ttc",      # Android
        "/system/fonts/NotoSansSC-Regular.otf",       # Android
        "/system/fonts/NotoSansSC-Regular.ttf",
        "C:/Windows/Fonts/msyh.ttc",                  # Windows 预览
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Linux
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                LabelBase.register(name="Roboto", fn_regular=path, fn_bold=path)
                return
            except Exception:
                pass


def apply_theme() -> None:
    """注册全局 KV 规则与字体(幂等,重复调用无害)。"""
    _register_cjk_font()
    from kivy.lang import Builder

    Builder.load_string(KV_RULES.format(TEXT=TEXT, TEXT_DIM=TEXT_DIM, ACCENT=ACCENT))


def spacer(height: float = 0, width: float = 0) -> Widget:
    widget = Widget(size_hint_y=None, height=dp(height)) if height else Widget()
    if width:
        widget.size_hint_x = None
        widget.width = dp(width)
    return widget
