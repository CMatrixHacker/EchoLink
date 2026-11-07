"""EchoLink 控制台 —— 视觉主题。

集中定义配色、渐变、缓动曲线与字体，界面其它模块只从这里取色，
避免颜色散落在各处导致风格走偏。
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QRadialGradient

# ---------------------------------------------------------------- 配色
# 深空底色：几乎全黑，带一点靛蓝，让霓虹更"跳"
BG_DEEP = QColor("#04060D")
BG_MID = QColor("#080D1A")
BG_RAISED = QColor("#0C1424")

# 霓虹主色：青 → 靛 → 紫，构成整条渐变轴
CYAN = QColor("#22E5E0")
CYAN_SOFT = QColor("#7DF7F2")
INDIGO = QColor("#6366F1")
VIOLET = QColor("#A855F7")
MAGENTA = QColor("#F472B6")

# 语义色
OK = QColor("#34D399")
WARN = QColor("#FBBF24")
DANGER = QColor("#FB7185")

# 文字
TEXT = QColor("#E8F3FF")
TEXT_DIM = QColor("#8FA3BF")
TEXT_FAINT = QColor("#5A6B85")

# 结构
BORDER = QColor(120, 170, 255, 38)
BORDER_HI = QColor(94, 234, 255, 110)
GLASS_TOP = QColor(255, 255, 255, 16)
GLASS_BOTTOM = QColor(120, 170, 255, 6)

FONT_UI = "Microsoft YaHei UI"
FONT_MONO = "Cascadia Mono"


def font(size: float = 10, weight: QFont.Weight = QFont.Weight.Normal, mono: bool = False) -> QFont:
    """字号支持小数（Qt 的 setPointSizeF 才是精确的）。"""
    f = QFont(FONT_MONO if mono else FONT_UI)
    f.setPointSizeF(max(1.0, float(size)))
    f.setWeight(weight)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return f


# ---------------------------------------------------------------- 缓动
# 统一使用"非线性"曲线：进入快、收尾慢，机械感更弱
EASE_IN = QEasingCurve.Type.OutQuint
EASE_SOFT = QEasingCurve.Type.OutCubic
EASE_BACK = QEasingCurve.Type.OutBack
EASE_BOTH = QEasingCurve.Type.InOutCubic
EASE_ELASTIC = QEasingCurve.Type.OutElastic


def with_alpha(color: QColor, alpha: int) -> QColor:
    c = QColor(color)
    c.setAlpha(max(0, min(255, int(alpha))))
    return c


def mix(a: QColor, b: QColor, t: float) -> QColor:
    """线性插值两个颜色，t 会被夹到 [0,1]。"""
    t = max(0.0, min(1.0, float(t)))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


def horizontal_gradient(rect: QRectF, colors: list[QColor], stops: list[float] | None = None) -> QLinearGradient:
    grad = QLinearGradient(rect.topLeft(), rect.topRight())
    _apply_stops(grad, colors, stops)
    return grad


def diagonal_gradient(rect: QRectF, colors: list[QColor], stops: list[float] | None = None) -> QLinearGradient:
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    _apply_stops(grad, colors, stops)
    return grad


def radial_glow(center: QPointF, radius: float, color: QColor, alpha: int = 120) -> QRadialGradient:
    grad = QRadialGradient(center, radius)
    grad.setColorAt(0.0, with_alpha(color, alpha))
    grad.setColorAt(0.45, with_alpha(color, int(alpha * 0.45)))
    grad.setColorAt(1.0, with_alpha(color, 0))
    return grad


def _apply_stops(grad: QLinearGradient, colors: list[QColor], stops: list[float] | None) -> None:
    if stops is None:
        count = max(1, len(colors) - 1)
        stops = [i / count for i in range(len(colors))]
    for stop, color in zip(stops, colors):
        grad.setColorAt(stop, color)


# 三个主色首尾相接，形成"青—靛—紫"的完整光谱
SPECTRUM = [CYAN, INDIGO, VIOLET]
