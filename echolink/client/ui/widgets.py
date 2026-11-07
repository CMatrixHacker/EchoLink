"""自绘控件集合：玻璃卡片、霓虹按钮、开关、滑块、状态环、导航项、浮层提示。

所有动态效果统一走 QVariantAnimation + theme 里的非线性缓动曲线，
不用固定时长线性插值，所以动作是"快进慢收"的，观感更接近原生动效。
"""

from __future__ import annotations

import math
from typing import Callable, Optional

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QGraphicsOpacityEffect,
    QLabel,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from echolink.client.ui import theme
from echolink.client.ui.graphics import draw_icon, icon_path, paint_aurora


# ---------------------------------------------------------------- 工具
def animate(
    owner: QWidget,
    start: float,
    end: float,
    setter: Callable[[float], None],
    duration: int = 220,
    curve: QEasingCurve.Type = theme.EASE_IN,
) -> QVariantAnimation:
    """对任意浮点量做缓动动画，结束后自动解引用，避免被 GC 回收。"""
    anim = QVariantAnimation(owner)
    anim.setStartValue(float(start))
    anim.setEndValue(float(end))
    anim.setDuration(duration)
    anim.setEasingCurve(QEasingCurve(curve))
    anim.valueChanged.connect(lambda v: setter(float(v)))
    anim.finished.connect(lambda: _forget(owner, anim))
    if not hasattr(owner, "_anims"):
        owner._anims = []
    owner._anims.append(anim)
    anim.start()
    return anim


def _forget(owner: QWidget, anim: QVariantAnimation) -> None:
    try:
        owner._anims.remove(anim)
    except (AttributeError, ValueError):
        pass


class AuroraCanvas(QWidget):
    """带极光背景的容器，子控件叠在上面。"""

    def __init__(self, parent: Optional[QWidget] = None, fps: int = 30) -> None:
        super().__init__(parent)
        self._t = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self._timer = QTimer(self)
        self._timer.setInterval(max(16, int(1000 / max(1, fps))))
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        self._t += 0.033
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        paint_aurora(painter, QRectF(self.rect()), self._t)


# ---------------------------------------------------------------- 卡片
class GlassCard(QWidget):
    """半透明玻璃面板：顶部微亮、底部转暗，1px 渐变描边。"""

    def __init__(self, parent: Optional[QWidget] = None, radius: float = 16.0, tint: Optional[QColor] = None) -> None:
        super().__init__(parent)
        self._radius = radius
        self._tint = tint
        self._hover = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def set_tint(self, color: Optional[QColor]) -> None:
        self._tint = color
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        animate(self, self._hover, 1.0, self._set_hover, 260, theme.EASE_SOFT)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        animate(self, self._hover, 0.0, self._set_hover, 300, theme.EASE_SOFT)
        super().leaveEvent(event)

    def _set_hover(self, value: float) -> None:
        self._hover = value
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        fill = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        top = theme.GLASS_TOP
        bottom = theme.GLASS_BOTTOM
        if self._tint is not None:
            top = theme.with_alpha(theme.mix(top, self._tint, 0.35), 26 + int(10 * self._hover))
            bottom = theme.with_alpha(theme.mix(bottom, self._tint, 0.25), 10)
        fill.setColorAt(0.0, top)
        fill.setColorAt(1.0, bottom)

        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        painter.fillPath(path, fill)

        border = theme.with_alpha(theme.BORDER_HI, 60 + int(70 * self._hover))
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, border)
        grad.setColorAt(0.55, theme.with_alpha(theme.BORDER, 30))
        grad.setColorAt(1.0, theme.with_alpha(theme.INDIGO, 55 + int(40 * self._hover)))
        pen = QPen(grad, 1.0)
        painter.setPen(pen)
        painter.drawPath(path)


# ---------------------------------------------------------------- 按钮
class NeonButton(QAbstractButton):
    """霓虹按钮：渐变填充 + 悬停发光 + 按下收缩。"""

    def __init__(
        self,
        text: str = "",
        parent: Optional[QWidget] = None,
        *,
        primary: bool = True,
        danger: bool = False,
        height: int = 38,
        radius: float = 10.0,
        icon: str = "",
    ) -> None:
        super().__init__(parent)
        self.setText(text)
        self._primary = primary
        self._danger = danger
        self._radius = radius
        self._icon = icon
        self._hover = 0.0
        self._press = 0.0
        self._sweep = 0.0
        self.setMinimumHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(theme.font(10, QFont.Weight.DemiBold))

    def sizeHint(self) -> QSize:  # noqa: N802
        # QAbstractButton 自身不实现 sizeHint,布局会把按钮压成 0 宽,必须自己给
        metrics = QFontMetrics(self.font())
        width = metrics.horizontalAdvance(self.text()) + 40
        if self._icon:
            width += 24
        return QSize(width, max(self.minimumHeight(), metrics.height() + 18))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

        self._sweep_timer = QTimer(self)
        self._sweep_timer.setInterval(33)
        self._sweep_timer.timeout.connect(self._advance_sweep)
        self._sweep_timer.start()

    def _advance_sweep(self) -> None:
        if self._hover > 0.01:
            self._sweep = (self._sweep + 0.045) % 1.0
            self.update()
        self._sweep_timer.setInterval(33 if self._hover > 0.01 else 120)

    def enterEvent(self, event) -> None:  # noqa: N802
        animate(self, self._hover, 1.0, self._set_hover, 220, theme.EASE_IN)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        animate(self, self._hover, 0.0, self._set_hover, 320, theme.EASE_SOFT)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        animate(self, self._press, 1.0, self._set_press, 90, theme.EASE_SOFT)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        animate(self, self._press, 0.0, self._set_press, 260, theme.EASE_ELASTIC)
        super().mouseReleaseEvent(event)

    def _set_hover(self, value: float) -> None:
        self._hover = value
        self.update()

    def _set_press(self, value: float) -> None:
        self._press = value
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        inset = 1.0 + 1.6 * self._press
        rect = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)

        if self._danger:
            c1, c2 = theme.DANGER, QColor("#B91C4B")
        elif self._primary:
            c1, c2 = theme.CYAN, theme.INDIGO
        else:
            c1, c2 = QColor("#1B2740"), QColor("#131B2E")

        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)

        enabled = self.isEnabled()
        glow = self._hover if enabled else 0.0

        if self._primary or self._danger:
            fill = theme.diagonal_gradient(rect, [theme.mix(c1, QColor("#FFFFFF"), 0.10 * glow), c1, c2])
            if not enabled:
                fill = theme.diagonal_gradient(rect, [theme.with_alpha(c1, 70), theme.with_alpha(c2, 70)])
            painter.fillPath(path, fill)
            # 顶部高光，制造亚克力质感
            hi = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            hi.setColorAt(0.0, QColor(255, 255, 255, int(46 + 30 * glow)))
            hi.setColorAt(0.5, QColor(255, 255, 255, 0))
            painter.fillPath(path, hi)
        else:
            fill = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            fill.setColorAt(0.0, theme.with_alpha(c1, 200))
            fill.setColorAt(1.0, theme.with_alpha(c2, 210))
            painter.fillPath(path, fill)
            pen = QPen(theme.with_alpha(theme.BORDER_HI, 40 + int(90 * glow)), 1.0)
            painter.setPen(pen)
            painter.drawPath(path)

        # 悬停时的斜向扫光
        if glow > 0.02:
            painter.save()
            painter.setClipPath(path)
            band_w = rect.width() * 0.36
            x = rect.left() - band_w + (rect.width() + band_w * 2) * self._sweep
            band = QLinearGradient(QPointF(x, rect.top()), QPointF(x + band_w, rect.bottom()))
            band.setColorAt(0.0, QColor(255, 255, 255, 0))
            band.setColorAt(0.5, QColor(255, 255, 255, int(26 * glow)))
            band.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.fillRect(rect, band)
            painter.restore()

        text_color = QColor("#04121A") if (self._primary and enabled and not self._danger) else theme.TEXT
        if not enabled:
            text_color = theme.with_alpha(theme.TEXT, 90)
        painter.setPen(text_color)
        painter.setFont(self.font())

        text_rect = rect
        if self._icon:
            icon_size = min(rect.height() * 0.5, 18.0)
            icon_rect = QRectF(rect.left() + 14, rect.center().y() - icon_size / 2, icon_size, icon_size)
            draw_icon(painter, self._icon, icon_rect, text_color, 1.5)
            text_rect = rect.adjusted(icon_size + 10, 0, 0, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self.text())


class TitleButton(QAbstractButton):
    """标题栏按钮：最小化 / 关闭，悬停时按语义变色。"""

    def __init__(self, kind: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._kind = kind
        self._hover = 0.0
        self.setFixedSize(38, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, event) -> None:  # noqa: N802
        animate(self, self._hover, 1.0, self._set_hover, 180, theme.EASE_IN)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        animate(self, self._hover, 0.0, self._set_hover, 240, theme.EASE_SOFT)
        super().leaveEvent(event)

    def _set_hover(self, value: float) -> None:
        self._hover = value
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(4, 2, -4, -2)
        path = QPainterPath()
        path.addRoundedRect(rect, 7, 7)
        base = theme.DANGER if self._kind == "close" else theme.CYAN
        painter.fillPath(path, theme.with_alpha(base, int(38 * self._hover)))
        color = theme.mix(theme.TEXT_DIM, base, self._hover)
        size = 12.0
        icon_rect = QRectF(rect.center().x() - size / 2, rect.center().y() - size / 2, size, size)
        draw_icon(painter, self._kind, icon_rect, color, 1.5)


# ---------------------------------------------------------------- 开关
class ToggleSwitch(QAbstractButton):
    """滑动开关：开态走渐变轨道，滑块用 OutBack 回弹。"""

    def __init__(self, parent: Optional[QWidget] = None, checked: bool = False) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(52, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = 1.0 if checked else 0.0
        self.toggled.connect(self._on_toggled)

    def _on_toggled(self, checked: bool) -> None:
        animate(self, self._pos, 1.0 if checked else 0.0, self._set_pos, 300, theme.EASE_BACK)

    def _set_pos(self, value: float) -> None:
        self._pos = max(0.0, min(1.0, value))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 3, -1, -3)
        path = QPainterPath()
        path.addRoundedRect(rect, rect.height() / 2, rect.height() / 2)

        off = QLinearGradient(rect.topLeft(), rect.topRight())
        off.setColorAt(0.0, QColor(28, 38, 58))
        off.setColorAt(1.0, QColor(20, 28, 44))
        on = QLinearGradient(rect.topLeft(), rect.topRight())
        on.setColorAt(0.0, theme.CYAN)
        on.setColorAt(1.0, theme.VIOLET)

        painter.fillPath(path, off)
        if self._pos > 0.01:
            painter.save()
            painter.setClipPath(path)
            overlay = QRectF(rect.left(), rect.top(), rect.width() * self._pos, rect.height())
            painter.fillRect(overlay, on)
            painter.restore()

        painter.setPen(QPen(theme.with_alpha(theme.BORDER_HI, 40 + int(80 * self._pos)), 1.0))
        painter.drawPath(path)

        knob_r = rect.height() / 2 - 3.0
        left = rect.left() + 3.0 + knob_r
        right = rect.right() - 3.0 - knob_r
        cx = left + (right - left) * self._pos
        center = QPointF(cx, rect.center().y())

        glow = QRadialGradient(center, knob_r * 2.6)
        glow.setColorAt(0.0, theme.with_alpha(theme.CYAN_SOFT, 60 + int(40 * self._pos)))
        glow.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(center, knob_r * 2.6, knob_r * 2.6)

        painter.setBrush(QColor("#F2FBFF"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center, knob_r, knob_r)


# ---------------------------------------------------------------- 滑块
class NeonSlider(QSlider):
    """横向音量条：已填充部分走渐变，手柄带光晕。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setRange(0, 100)
        self.setFixedHeight(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._glow = 0.0

    def enterEvent(self, event) -> None:  # noqa: N802
        animate(self, self._glow, 1.0, self._set_glow, 220, theme.EASE_IN)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        animate(self, self._glow, 0.0, self._set_glow, 300, theme.EASE_SOFT)
        super().leaveEvent(event)

    def _set_glow(self, value: float) -> None:
        self._glow = value
        self.update()

    def _ratio(self) -> float:
        span = self.maximum() - self.minimum()
        return 0.0 if span <= 0 else (self.value() - self.minimum()) / span

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        h = 6.0
        track = QRectF(2, (self.height() - h) / 2, self.width() - 4, h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(24, 33, 50))
        painter.drawRoundedRect(track, h / 2, h / 2)

        ratio = self._ratio()
        if ratio > 0:
            fill = QRectF(track.left(), track.top(), max(h, track.width() * ratio), h)
            grad = QLinearGradient(fill.topLeft(), fill.topRight())
            grad.setColorAt(0.0, theme.CYAN)
            grad.setColorAt(0.6, theme.INDIGO)
            grad.setColorAt(1.0, theme.VIOLET)
            painter.setBrush(grad)
            painter.drawRoundedRect(fill, h / 2, h / 2)

        cx = track.left() + track.width() * ratio
        cy = track.center().y()
        glow_r = 13.0 + 4.0 * self._glow
        glow = QRadialGradient(QPointF(cx, cy), glow_r)
        glow.setColorAt(0.0, theme.with_alpha(theme.CYAN_SOFT, 110 + int(60 * self._glow)))
        glow.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(glow)
        painter.drawEllipse(QPointF(cx, cy), glow_r, glow_r)

        painter.setBrush(QColor("#EAFBFF"))
        painter.drawEllipse(QPointF(cx, cy), 5.5, 5.5)


# ---------------------------------------------------------------- 状态环
class StatusOrb(QWidget):
    """连接状态指示：外圈旋转虚线 + 呼吸光晕 + 中心文案。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(168, 168)
        self._angle = 0.0
        self._pulse = 0.0
        self._state = "offline"
        self._title = "未连接"
        self._subtitle = "等待接入"
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_state(self, state: str, title: str, subtitle: str = "") -> None:
        self._state = state
        self._title = title
        self._subtitle = subtitle
        self.update()

    def _tick(self) -> None:
        # 旋转角速度与状态相关：连接中快、在线慢，符合"呼吸"直觉
        speed = {"online": 0.55, "connecting": 2.4, "error": 0.9}.get(self._state, 0.35)
        self._angle = (self._angle + speed) % 360.0
        self._pulse += 0.05
        self.update()

    def _color(self) -> QColor:
        return {"online": theme.OK, "connecting": theme.CYAN, "error": theme.DANGER}.get(self._state, theme.TEXT_FAINT)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        side = min(self.width(), self.height())
        center = QPointF(self.width() / 2, self.height() / 2)
        color = self._color()

        breathe = 0.5 + 0.5 * math.sin(self._pulse)
        halo_r = side * 0.46
        halo = QRadialGradient(center, halo_r)
        halo.setColorAt(0.0, theme.with_alpha(color, int(30 + 30 * breathe)))
        halo.setColorAt(0.75, theme.with_alpha(color, int(10 + 12 * breathe)))
        halo.setColorAt(1.0, theme.with_alpha(color, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(center, halo_r, halo_r)

        ring_r = side * 0.38
        painter.setBrush(Qt.BrushStyle.NoBrush)

        painter.setPen(QPen(theme.with_alpha(color, 40), 1.4))
        painter.drawEllipse(center, ring_r, ring_r)

        # 旋转弧：用渐变笔刷让首尾淡出
        painter.save()
        painter.translate(center)
        painter.rotate(self._angle)
        arc_rect = QRectF(-ring_r, -ring_r, ring_r * 2, ring_r * 2)
        for i in range(3):
            start = i * 120
            pen = QPen(theme.with_alpha(color, 90 + i * 40))
            pen.setWidthF(2.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(arc_rect, int(start * 16), int(58 * 16))
        painter.restore()

        inner_r = side * 0.26
        inner = QRadialGradient(center, inner_r)
        inner.setColorAt(0.0, theme.with_alpha(color, int(60 + 50 * breathe)))
        inner.setColorAt(0.7, theme.with_alpha(color, 24))
        inner.setColorAt(1.0, theme.with_alpha(color, 6))
        painter.setBrush(inner)
        painter.setPen(QPen(theme.with_alpha(color, 120), 1.2))
        painter.drawEllipse(center, inner_r, inner_r)

        painter.setPen(theme.TEXT)
        painter.setFont(theme.font(13, QFont.Weight.Bold))
        metrics = QFontMetrics(painter.font())
        title = metrics.elidedText(self._title, Qt.TextElideMode.ElideRight, int(ring_r * 1.7))
        text_rect = QRectF(center.x() - ring_r, center.y() - 22, ring_r * 2, 26)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, title)

        painter.setPen(theme.with_alpha(color, 220))
        painter.setFont(theme.font(8))
        sub_metrics = QFontMetrics(painter.font())
        subtitle = sub_metrics.elidedText(self._subtitle, Qt.TextElideMode.ElideMiddle, int(ring_r * 1.8))
        sub_rect = QRectF(center.x() - ring_r, center.y() + 4, ring_r * 2, 20)
        painter.drawText(sub_rect, Qt.AlignmentFlag.AlignCenter, subtitle)


# ---------------------------------------------------------------- 导航
class NavButton(QAbstractButton):
    """左侧导航项：图标 + 文字，选中时左侧亮条滑出。"""

    def __init__(self, icon: str, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._icon = icon
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(46)
        self._hover = 0.0
        self._indicator = 0.0
        self.setFont(theme.font(10, QFont.Weight.Medium))

    def sizeHint(self) -> QSize:  # noqa: N802
        metrics = QFontMetrics(self.font())
        return QSize(metrics.horizontalAdvance(self.text()) + 74, 46)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        super().setChecked(checked)
        animate(self, self._indicator, 1.0 if checked else 0.0, self._set_indicator, 320, theme.EASE_IN)
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        animate(self, self._hover, 1.0, self._set_hover, 200, theme.EASE_IN)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        animate(self, self._hover, 0.0, self._set_hover, 280, theme.EASE_SOFT)
        super().leaveEvent(event)

    def _set_hover(self, value: float) -> None:
        self._hover = value
        self.update()

    def _set_indicator(self, value: float) -> None:
        self._indicator = value
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(6, 3, -6, -3)
        path = QPainterPath()
        path.addRoundedRect(rect, 11, 11)

        active = max(self._indicator, self._hover * 0.55)
        if active > 0.01:
            fill = QLinearGradient(rect.topLeft(), rect.topRight())
            fill.setColorAt(0.0, theme.with_alpha(theme.CYAN, int(34 * active)))
            fill.setColorAt(1.0, theme.with_alpha(theme.INDIGO, int(10 * active)))
            painter.fillPath(path, fill)
            painter.setPen(QPen(theme.with_alpha(theme.CYAN, int(70 * active)), 1.0))
            painter.drawPath(path)

        if self._indicator > 0.01:
            bar_h = rect.height() * 0.52 * self._indicator
            bar = QRectF(rect.left() + 1.5, rect.center().y() - bar_h / 2, 2.6, bar_h)
            grad = QLinearGradient(bar.topLeft(), bar.bottomLeft())
            grad.setColorAt(0.0, theme.CYAN)
            grad.setColorAt(1.0, theme.VIOLET)
            painter.fillRect(bar, grad)

        color = theme.mix(theme.TEXT_DIM, theme.TEXT, max(self._indicator, self._hover))
        icon_rect = QRectF(rect.left() + 16, rect.center().y() - 9, 18, 18)
        draw_icon(painter, self._icon, icon_rect, color, 1.5)

        painter.setPen(color)
        painter.setFont(self.font())
        painter.drawText(
            QRectF(icon_rect.right() + 12, rect.top(), rect.width() - 46, rect.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.text(),
        )


# ---------------------------------------------------------------- 文本
class Caption(QLabel):
    """小号说明文字。"""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None, *, mono: bool = False) -> None:
        super().__init__(text, parent)
        self.setFont(theme.font(8, mono=mono))
        self.setStyleSheet(f"color: {theme.TEXT_FAINT.name()};")


class StatTile(QWidget):
    """指标块：上方小标题，下方大数值。"""

    def __init__(self, label: str, value: str = "—", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(58)
        self._label = label
        self._value = value
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, value: str) -> None:
        if value != self._value:
            self._value = value
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        painter.setPen(theme.TEXT_FAINT)
        painter.setFont(theme.font(8))
        painter.drawText(
            rect.adjusted(2, 6, -2, 0),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
            self._label,
        )
        painter.setPen(theme.TEXT)
        painter.setFont(theme.font(12, QFont.Weight.DemiBold))
        metrics = QFontMetrics(painter.font())
        value = metrics.elidedText(self._value, Qt.TextElideMode.ElideRight, int(rect.width() - 6))
        painter.drawText(
            rect.adjusted(2, 0, -2, -8),
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
            value,
        )


# ---------------------------------------------------------------- 浮层
class Toast(QWidget):
    """右下角浮层提示，滑入 + 自动淡出。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._text = ""
        self._accent = theme.CYAN
        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)

    def show_message(self, text: str, accent: Optional[QColor] = None, duration: int = 2600) -> None:
        self._text = text
        self._accent = accent or theme.CYAN
        metrics = QFontMetrics(theme.font(10))
        width = min(max(220, metrics.horizontalAdvance(text) + 46), max(260, self.parent().width() - 80))
        self.setFixedSize(width, 46)
        self._relocate()
        self.show()
        self.raise_()

        start = self.parent().width()
        end = self.parent().width() - self.width() - 24
        animate(self, start, end, self._move_x, 420, theme.EASE_IN)
        animate(self, 0.0, 1.0, self._set_opacity, 320, theme.EASE_SOFT)
        self._timer.start(duration)

    def _move_x(self, x: float) -> None:
        self.move(int(x), self.y())

    def _relocate(self) -> None:
        parent = self.parent()
        self.move(parent.width() - self.width() - 24, parent.height() - self.height() - 24)

    def _set_opacity(self, value: float) -> None:
        self._opacity.setOpacity(value)

    def _fade_out(self) -> None:
        animate(self, 1.0, 0.0, self._set_opacity, 380, theme.EASE_SOFT).finished.connect(self.hide)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 12, 12)

        fill = QLinearGradient(rect.topLeft(), rect.topRight())
        fill.setColorAt(0.0, theme.with_alpha(theme.BG_RAISED, 245))
        fill.setColorAt(1.0, theme.with_alpha(theme.mix(theme.BG_RAISED, self._accent, 0.25), 245))
        painter.fillPath(path, fill)
        painter.setPen(QPen(theme.with_alpha(self._accent, 170), 1.0))
        painter.drawPath(path)

        painter.fillRect(QRectF(rect.left(), rect.top() + 10, 3, rect.height() - 20), self._accent)

        painter.setPen(theme.TEXT)
        painter.setFont(theme.font(9.5))
        painter.drawText(rect.adjusted(18, 0, -12, 0), Qt.AlignmentFlag.AlignVCenter, self._text)


class Divider(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(1)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        grad = QLinearGradient(QRectF(self.rect()).topLeft(), QRectF(self.rect()).topRight())
        grad.setColorAt(0.0, QColor(255, 255, 255, 0))
        grad.setColorAt(0.5, theme.with_alpha(theme.BORDER_HI, 60))
        grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(self.rect(), grad)


def hairline() -> Divider:
    return Divider()


def vspace(parent: Optional[QWidget], height: int) -> QWidget:
    box = QWidget(parent)
    box.setFixedHeight(height)
    return box
