"""自绘图形：极光渐变背景、网格、图标。

背景不是静态图，而是按时间函数实时绘制：三个色团在椭圆轨道上漂移，
相位互不相同，因此画面永远在缓慢变化但不会重复得很明显。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRadialGradient

from echolink.client.ui import theme


@dataclass
class Blob:
    color: QColor
    cx: float          # 基准位置（相对宽高的比例）
    cy: float
    radius: float      # 相对窗口短边的比例
    amp_x: float       # 轨道半径（相对宽）
    amp_y: float
    speed: float       # 角速度
    phase: float       # 初始相位
    alpha: int


AURORA_BLOBS = [
    Blob(theme.CYAN, 0.18, 0.12, 0.62, 0.06, 0.04, 0.11, 0.0, 78),
    Blob(theme.VIOLET, 0.84, 0.24, 0.55, 0.05, 0.06, -0.09, 1.7, 70),
    Blob(theme.INDIGO, 0.55, 0.95, 0.70, 0.08, 0.03, 0.07, 3.1, 64),
]


def paint_aurora(painter: QPainter, rect: QRectF, t: float) -> None:
    """在 rect 内绘制极光背景。t 为秒，用来驱动漂移。"""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    base = theme.diagonal_gradient(
        rect,
        [theme.BG_DEEP, theme.BG_MID, QColor("#0A1122"), theme.BG_DEEP],
        [0.0, 0.35, 0.72, 1.0],
    )
    painter.fillRect(rect, base)

    short = min(rect.width(), rect.height())
    for blob in AURORA_BLOBS:
        # 两个不同频率的正弦叠加，轨迹不是规整的圆，看起来更自然
        ax = blob.cx + blob.amp_x * math.sin(t * blob.speed * 2 * math.pi + blob.phase)
        ay = blob.cy + blob.amp_y * math.sin(t * blob.speed * 1.37 * math.pi + blob.phase * 1.4)
        center = QPointF(rect.left() + ax * rect.width(), rect.top() + ay * rect.height())
        radius = blob.radius * short
        grad = QRadialGradient(center, radius)
        # 呼吸式透明度，避免色团总是同样亮
        breathe = 0.82 + 0.18 * math.sin(t * 0.7 + blob.phase)
        grad.setColorAt(0.0, theme.with_alpha(blob.color, int(blob.alpha * breathe)))
        grad.setColorAt(0.42, theme.with_alpha(blob.color, int(blob.alpha * 0.38 * breathe)))
        grad.setColorAt(1.0, theme.with_alpha(blob.color, 0))
        painter.fillRect(rect, grad)

    paint_grid(painter, rect)
    paint_vignette(painter, rect)


def paint_grid(painter: QPainter, rect: QRectF, step: float = 44.0) -> None:
    painter.save()
    pen = QPen(theme.with_alpha(theme.CYAN, 12))
    pen.setWidthF(1.0)
    painter.setPen(pen)
    x = rect.left() + step
    while x < rect.right():
        painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        x += step
    y = rect.top() + step
    while y < rect.bottom():
        painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        y += step
    painter.restore()


def paint_vignette(painter: QPainter, rect: QRectF) -> None:
    """四角压暗，让中间内容更聚光。"""
    radius = max(rect.width(), rect.height()) * 0.78
    center = rect.center()
    grad = QRadialGradient(center, radius)
    grad.setColorAt(0.0, QColor(0, 0, 0, 0))
    grad.setColorAt(0.62, QColor(0, 0, 0, 0))
    grad.setColorAt(1.0, QColor(0, 0, 0, 150))
    painter.fillRect(rect, grad)


# ---------------------------------------------------------------- 图标
def icon_path(name: str, rect: QRectF) -> QPainterPath:
    """用几何路径画图标，避免依赖字体或图片资源。"""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    p = QPainterPath()

    if name == "link":
        # 两个交叠的圆环 = 链路
        r = w * 0.30
        p.addEllipse(QPointF(x + w * 0.34, y + h * 0.5), r, r)
        p.addEllipse(QPointF(x + w * 0.66, y + h * 0.5), r, r)
    elif name == "files":
        p.addRoundedRect(QRectF(x + w * 0.16, y + h * 0.18, w * 0.56, h * 0.44), 2.0, 2.0)
        p.addRoundedRect(QRectF(x + w * 0.28, y + h * 0.38, w * 0.56, h * 0.44), 2.0, 2.0)
    elif name == "control":
        for i, cy in enumerate((0.28, 0.5, 0.72)):
            cy_px = y + h * cy
            p.addEllipse(QPointF(x + w * (0.32 + 0.16 * i), cy_px), w * 0.075, w * 0.075)
            p.moveTo(x + w * 0.12, cy_px)
            p.lineTo(x + w * (0.28 + 0.16 * i), cy_px)
            p.moveTo(x + w * (0.36 + 0.16 * i), cy_px)
            p.lineTo(x + w * 0.88, cy_px)
    elif name == "log":
        for i, cy in enumerate((0.26, 0.5, 0.74)):
            p.moveTo(x + w * 0.16, y + h * cy)
            p.lineTo(x + w * (0.88 - 0.18 * (i == 2)), y + h * cy)
    elif name == "power":
        p.arcMoveTo(QRectF(x + w * 0.16, y + h * 0.18, w * 0.68, h * 0.68), 60)
        p.arcTo(QRectF(x + w * 0.16, y + h * 0.18, w * 0.68, h * 0.68), 60, 300)
        p.moveTo(x + w * 0.5, y + h * 0.08)
        p.lineTo(x + w * 0.5, y + h * 0.46)
    elif name == "screen":
        p.addRoundedRect(QRectF(x + w * 0.10, y + h * 0.16, w * 0.80, h * 0.56), 2.0, 2.0)
        p.moveTo(x + w * 0.32, y + h * 0.86)
        p.lineTo(x + w * 0.68, y + h * 0.86)
        p.moveTo(x + w * 0.50, y + h * 0.72)
        p.lineTo(x + w * 0.50, y + h * 0.86)
    elif name == "clip":
        p.addRoundedRect(QRectF(x + w * 0.22, y + h * 0.10, w * 0.56, h * 0.80), 2.0, 2.0)
        p.addRoundedRect(QRectF(x + w * 0.34, y + h * 0.04, w * 0.32, h * 0.14), 1.5, 1.5)
    elif name == "cpu":
        p.addRoundedRect(QRectF(x + w * 0.24, y + h * 0.24, w * 0.52, h * 0.52), 2.0, 2.0)
        for i in range(3):
            off = 0.34 + i * 0.16
            p.moveTo(x + w * off, y + h * 0.08)
            p.lineTo(x + w * off, y + h * 0.24)
            p.moveTo(x + w * off, y + h * 0.76)
            p.lineTo(x + w * off, y + h * 0.92)
            p.moveTo(x + w * 0.08, y + h * off)
            p.lineTo(x + w * 0.24, y + h * off)
            p.moveTo(x + w * 0.76, y + h * off)
            p.lineTo(x + w * 0.92, y + h * off)
    elif name == "min":
        p.moveTo(x + w * 0.25, y + h * 0.55)
        p.lineTo(x + w * 0.75, y + h * 0.55)
    elif name == "close":
        p.moveTo(x + w * 0.30, y + h * 0.30)
        p.lineTo(x + w * 0.70, y + h * 0.70)
        p.moveTo(x + w * 0.70, y + h * 0.30)
        p.lineTo(x + w * 0.30, y + h * 0.70)
    elif name == "max":
        p.addRoundedRect(QRectF(x + w * 0.28, y + h * 0.28, w * 0.44, h * 0.44), 2.0, 2.0)
    return p


def draw_icon(painter: QPainter, name: str, rect: QRectF, color: QColor, width: float = 1.6) -> None:
    pen = QPen(color)
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.save()
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(icon_path(name, rect))
    painter.restore()
