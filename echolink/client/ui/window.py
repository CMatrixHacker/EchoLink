"""EchoLink 控制台主窗口。

无边框 + 极光渐变背景 + 自绘标题栏，关闭时收进系统托盘。
所有网络动作都经 ``run()`` 交给后台串行队列，界面线程不做阻塞调用。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRegion,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from echolink import __version__
from echolink.client.core import EchoLinkClient
from echolink.client.ui import theme
from echolink.client.ui.graphics import draw_icon, paint_aurora
from echolink.client.ui.pages import (
    ClipboardDialog,
    ConnectPage,
    ControlPage,
    FilesPage,
    LogPage,
    ScreenDialog,
)
from echolink.client.ui.widgets import (
    Caption,
    GlassCard,
    NavButton,
    NeonButton,
    Toast,
    TitleButton,
)
from echolink.client.ui.worker import TaskRunner
from echolink.platform import autostart as autostart_mod

SETTINGS_PATH = Path.home() / ".echolink" / "client.json"
INSET = 7  # 窗口四周留给阴影与拖拽的透明边

STYLE = f"""
QLineEdit {{
    background: rgba(9, 16, 30, 170);
    border: 1px solid rgba(120, 170, 255, 42);
    border-radius: 9px;
    padding: 8px 12px;
    color: {theme.TEXT.name()};
    selection-background-color: rgba(34, 229, 224, 90);
}}
QLineEdit:focus {{
    border: 1px solid rgba(94, 234, 255, 170);
    background: rgba(12, 22, 40, 200);
}}
QListWidget {{
    background: transparent;
    border: none;
    outline: none;
    color: {theme.TEXT_DIM.name()};
}}
QListWidget::item {{
    background: transparent;
    border: none;
}}
QListWidget::item:selected {{
    background: rgba(34, 229, 224, 40);
    border-radius: 8px;
}}
QTextEdit {{
    background: rgba(6, 11, 22, 190);
    border: 1px solid rgba(120, 170, 255, 36);
    border-radius: 10px;
    color: #B8D3F0;
    padding: 8px;
    selection-background-color: rgba(34, 229, 224, 90);
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: rgba(99, 102, 241, 120);
    border-radius: 4px;
    min-height: 26px;
}}
QScrollBar::handle:vertical:hover {{
    background: rgba(94, 234, 255, 160);
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0px;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}
QMenu {{
    background: #0B1322;
    border: 1px solid rgba(120, 170, 255, 60);
    border-radius: 10px;
    padding: 6px;
    color: {theme.TEXT.name()};
}}
QMenu::item {{
    padding: 7px 26px 7px 14px;
    border-radius: 7px;
}}
QMenu::item:selected {{
    background: rgba(34, 229, 224, 46);
}}
QMenu::indicator {{
    width: 12px;
    height: 12px;
    margin-left: 6px;
}}
QToolTip {{
    background: #0B1322;
    color: {theme.TEXT.name()};
    border: 1px solid rgba(120, 170, 255, 70);
    padding: 5px 8px;
}}
QInputDialog, QMessageBox {{
    background: #0A1120;
    color: {theme.TEXT.name()};
}}
"""


def make_icon(size: int = 256) -> QPixmap:
    """程序图标：圆角方块 + 青紫渐变 + 字标 E。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    rect = QRectF(size * 0.06, size * 0.06, size * 0.88, size * 0.88)
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0.0, theme.CYAN)
    grad.setColorAt(0.55, theme.INDIGO)
    grad.setColorAt(1.0, theme.VIOLET)
    path = QPainterPath()
    path.addRoundedRect(rect, size * 0.24, size * 0.24)
    painter.fillPath(path, grad)

    inner = rect.adjusted(size * 0.05, size * 0.05, -size * 0.05, -size * 0.05)
    painter.setPen(QPen(QColor(255, 255, 255, 110), size * 0.012))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)

    font = QFont(theme.FONT_UI, int(size * 0.40))
    font.setWeight(QFont.Weight.Black)
    painter.setFont(font)
    painter.setPen(QColor(4, 14, 24, 230))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "E")
    painter.setPen(QColor(255, 255, 255, 90))
    painter.drawText(rect.adjusted(-1, -1, -1, -1), Qt.AlignmentFlag.AlignCenter, "E")
    painter.end()
    return pixmap


class _Mark(QWidget):
    """标题栏左侧的应用标记（渐变圆角 + E 字标）。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(30, 30)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, theme.CYAN)
        grad.setColorAt(1.0, theme.VIOLET)
        path = QPainterPath()
        path.addRoundedRect(rect, 9, 9)
        painter.fillPath(path, grad)
        painter.setPen(QPen(QColor(255, 255, 255, 120), 1.0))
        painter.drawPath(path)
        painter.setPen(QColor(4, 14, 24))
        painter.setFont(theme.font(12, QFont.Weight.Black))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "E")


class TitleBar(QWidget):
    """自绘标题栏，负责拖动与按钮。"""

    def __init__(self, window: "ConsoleWindow") -> None:
        super().__init__(window)
        self.host = window
        self.setFixedHeight(58)
        self._drag_offset: Optional[QPoint] = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 8, 10, 4)
        layout.setSpacing(10)

        layout.addWidget(_Mark(self))

        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(0)
        title = QLabel("ECHOLINK")
        title.setFont(theme.font(11, QFont.Weight.Black))
        title.setStyleSheet(f"color: {theme.TEXT.name()}; background: transparent; letter-spacing: 2px;")
        subtitle = QLabel("REMOTE CONTROL CONSOLE")
        subtitle.setFont(theme.font(7))
        subtitle.setStyleSheet(f"color: {theme.TEXT_FAINT.name()}; background: transparent; letter-spacing: 1px;")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        layout.addLayout(title_box)

        layout.addStretch(1)

        self.status = QLabel("未连接")
        self.status.setFont(theme.font(8.5, mono=True))
        self.status.setStyleSheet(
            f"color: {theme.TEXT_DIM.name()}; background: rgba(10, 18, 34, 150);"
            f"border: 1px solid rgba(120, 170, 255, 40); border-radius: 9px; padding: 4px 12px;"
        )
        layout.addWidget(self.status)

        self.tray_btn = TitleButton("min")
        self.tray_btn.setToolTip("收进托盘")
        self.tray_btn.clicked.connect(window.hide_to_tray)
        close = TitleButton("close")
        close.setToolTip("收进托盘（托盘菜单可退出）")
        close.clicked.connect(window.hide_to_tray)
        layout.addWidget(self.tray_btn)
        layout.addWidget(close)

    # 拖动
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.host.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if self.host.isMaximized():
                self.host.showNormal()
            self.host.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.host.toggle_max()
        super().mouseDoubleClickEvent(event)

    def set_status(self, text: str, color: QColor) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(
            f"color: {color.name()}; background: rgba(10, 18, 34, 150);"
            f"border: 1px solid {color.name()}; border-radius: 9px; padding: 4px 12px;"
        )


class ConsoleWindow(QWidget):
    def __init__(self, start_hidden: bool = False, autoconnect: Optional[bool] = None) -> None:
        super().__init__()
        self.setWindowTitle("EchoLink 控制台")
        self.setWindowIcon(QIcon(make_icon()))
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(QSize(980, 660))
        self.resize(1180, 760)
        self.setStyleSheet(STYLE)

        self.client: Optional[EchoLinkClient] = None
        self.runner = TaskRunner(self)
        self._t = 0.0
        self._connecting = False
        self._quitting = False
        self._tray_tip_shown = False
        self._resize_edge = 0
        self._resize_origin: Optional[tuple[QPoint, QRect]] = None

        self.settings = self._load_settings()

        # 背景动画（15fps 足够表现缓慢漂移，也不烧 CPU）
        self._anim = QTimer(self)
        self._anim.setInterval(66)
        self._anim.timeout.connect(self._tick)
        self._anim.start()

        root = QVBoxLayout(self)
        root.setContentsMargins(INSET, INSET, INSET, INSET)
        root.setSpacing(0)

        self.titlebar = TitleBar(self)
        root.addWidget(self.titlebar)

        body = QHBoxLayout()
        body.setContentsMargins(14, 4, 14, 12)
        body.setSpacing(14)
        root.addLayout(body, 1)

        body.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.stack.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        body.addWidget(self.stack, 1)

        self.connect_page = ConnectPage(self)
        self.files_page = FilesPage(self)
        self.control_page = ControlPage(self)
        self.log_page = LogPage(self)
        for page in (self.connect_page, self.files_page, self.control_page, self.log_page):
            self.stack.addWidget(page)

        self.toast = Toast(self)
        self._build_tray()
        self._restore_settings()
        self.log(f"EchoLink 控制台 v{__version__} 启动")

        if start_hidden:
            QTimer.singleShot(160, self._start_hidden_now)
        if autoconnect is None:
            autoconnect = bool(self.settings.get("autoconnect", True))
        if autoconnect:
            QTimer.singleShot(420, self.toggle_connection)

    # ------------------------------------------------------------ 构建
    def _build_sidebar(self) -> QWidget:
        panel = GlassCard(radius=16)
        panel.setFixedWidth(208)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 16, 10, 14)
        layout.setSpacing(4)

        self.nav_buttons: List[NavButton] = []
        for index, (icon, text) in enumerate(
            (("link", "连接"), ("files", "文件"), ("control", "控制"), ("log", "日志"))
        ):
            button = NavButton(icon, text)
            button.clicked.connect(lambda _=False, i=index: self.switch_page(i))
            layout.addWidget(button)
            self.nav_buttons.append(button)

        layout.addStretch(1)

        tip = Caption("关闭窗口不退出\n托盘图标可随时唤回")
        tip.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(tip)

        version = QLabel(f"v{__version__}")
        version.setFont(theme.font(7.5, mono=True))
        version.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        version.setStyleSheet(f"color: {theme.TEXT_FAINT.name()}; background: transparent;")
        layout.addWidget(version)
        return panel

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(QIcon(make_icon(64)), self)
        self.tray.setToolTip("EchoLink 控制台")
        menu = QMenu()

        show = QAction("显示控制台", self)
        show.triggered.connect(self.show_console)
        menu.addAction(show)

        self.reconnect_action = QAction("重新连接", self)
        self.reconnect_action.triggered.connect(self.toggle_connection)
        menu.addAction(self.reconnect_action)

        menu.addSeparator()

        self.autostart_action = QAction("开机自启", self)
        self.autostart_action.setCheckable(True)
        self.autostart_action.triggered.connect(lambda checked: self.set_autostart(checked))
        menu.addAction(self.autostart_action)

        menu.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _restore_settings(self) -> None:
        target = str(self.settings.get("target") or "127.0.0.1:8765")
        secret = str(self.settings.get("secret") or "")
        self.connect_page.target_edit.setText(target)
        self.connect_page.secret_edit.setText(secret)

        enabled = bool(autostart_mod.status().get("enabled"))
        self.autostart_action.setChecked(enabled)
        self.connect_page.autostart_switch.blockSignals(True)
        self.connect_page.autostart_switch.setChecked(enabled)
        self.connect_page.autostart_switch._pos = 1.0 if enabled else 0.0
        self.connect_page.autostart_switch.blockSignals(False)
        # 不默认写注册表：自启是显式选择，由界面开关或 `echolink autostart on` 触发

    # ------------------------------------------------------------ 基础行为
    def _tick(self) -> None:
        self._t += 0.066
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        radius = 0.0 if self.isMaximized() else 18.0
        panel = QRectF(self.rect()).adjusted(INSET, INSET, -INSET, -INSET)

        path = QPainterPath()
        path.addRoundedRect(panel, radius, radius)
        painter.save()
        painter.setClipPath(path)
        paint_aurora(painter, panel, self._t)
        painter.restore()

        border = QLinearGradient(panel.topLeft(), panel.bottomRight())
        border.setColorAt(0.0, theme.with_alpha(theme.CYAN, 150))
        border.setColorAt(0.45, theme.with_alpha(theme.BORDER, 60))
        border.setColorAt(1.0, theme.with_alpha(theme.VIOLET, 130))
        painter.setPen(QPen(border, 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "toast") and self.toast.isVisible():
            self.toast._relocate()

    def toggle_max(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self.update()

    # 无边框窗口的边缘拖拽
    def _edge_at(self, pos: QPoint) -> int:
        if self.isMaximized():
            return 0
        edge = 0
        if pos.x() <= INSET + 3:
            edge |= 1
        elif pos.x() >= self.width() - INSET - 3:
            edge |= 2
        if pos.y() <= INSET + 3:
            edge |= 4
        elif pos.y() >= self.height() - INSET - 3:
            edge |= 8
        return edge

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            edge = self._edge_at(event.position().toPoint())
            if edge:
                self._resize_edge = edge
                self._resize_origin = (event.globalPosition().toPoint(), self.geometry())
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._resize_edge and self._resize_origin is not None:
            start, geometry = self._resize_origin
            delta = event.globalPosition().toPoint() - start
            rect = QRect(geometry)
            if self._resize_edge & 1:
                rect.setLeft(min(rect.left() + delta.x(), rect.right() - 720))
            if self._resize_edge & 2:
                rect.setRight(max(rect.right() + delta.x(), rect.left() + 720))
            if self._resize_edge & 4:
                rect.setTop(min(rect.top() + delta.y(), rect.bottom() - 520))
            if self._resize_edge & 8:
                rect.setBottom(max(rect.bottom() + delta.y(), rect.top() + 520))
            self.setGeometry(rect)
            return
        edge = self._edge_at(event.position().toPoint())
        cursors = {
            0: Qt.CursorShape.ArrowCursor,
            1: Qt.CursorShape.SizeHorCursor,
            2: Qt.CursorShape.SizeHorCursor,
            4: Qt.CursorShape.SizeVerCursor,
            8: Qt.CursorShape.SizeVerCursor,
            5: Qt.CursorShape.SizeFDiagCursor,
            10: Qt.CursorShape.SizeFDiagCursor,
            6: Qt.CursorShape.SizeBDiagCursor,
            9: Qt.CursorShape.SizeBDiagCursor,
        }
        self.setCursor(cursors.get(edge, Qt.CursorShape.ArrowCursor))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._resize_edge = 0
        self._resize_origin = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._quitting or not self.tray.isVisible():
            self._shutdown()
            event.accept()
            return
        event.ignore()
        self.hide_to_tray()

    def hide_to_tray(self) -> None:
        self.hide()
        if not self._tray_tip_shown and self.tray.isVisible() and QSystemTrayIcon.supportsMessages():
            self.tray.showMessage("EchoLink 仍在运行", "已在后台常驻，点击托盘图标可重新打开。", QIcon(make_icon(64)), 2600)
            self._tray_tip_shown = True

    def show_console(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _start_hidden_now(self) -> None:
        if self.tray.isVisible():
            self.hide()
            self._tray_tip_shown = True

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            if self.isVisible() and not self.isMinimized():
                self.hide_to_tray()
            else:
                self.show_console()

    def quit_app(self) -> None:
        self._quitting = True
        self._shutdown()
        QApplication.quit()

    def _shutdown(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None
        self.runner.stop()

    # ------------------------------------------------------------ 页面
    def switch_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)
        page = self.stack.currentWidget()
        if page is not None:
            from PySide6.QtWidgets import QGraphicsOpacityEffect

            effect = QGraphicsOpacityEffect(page)
            page.setGraphicsEffect(effect)
            effect.setOpacity(0.0)
            self._animate_opacity(effect, lambda: page.setGraphicsEffect(None))

        if index == 1 and self.client is not None and not self.files_page.entries:
            self.files_page.refresh()
        if index == 2 and self.client is not None:
            self._load_audio()
            self.control_page.load_processes()

    def _animate_opacity(self, effect: Any, on_done: Callable[[], None]) -> None:
        from PySide6.QtCore import QEasingCurve, QVariantAnimation

        animation = QVariantAnimation(self)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setDuration(240)
        animation.setEasingCurve(QEasingCurve(theme.EASE_SOFT))
        animation.valueChanged.connect(lambda v: effect.setOpacity(float(v)))
        animation.finished.connect(on_done)
        animation.start()
        if not hasattr(self, "_page_anims"):
            self._page_anims = []
        self._page_anims.append(animation)

    # ------------------------------------------------------------ 连接
    def toggle_connection(self) -> None:
        if self._connecting:
            return
        if self.client is not None:
            self.disconnect()
            return
        self.connect()

    def connect(self) -> None:
        target = self.connect_page.target_edit.text().strip() or "127.0.0.1:8765"
        secret = self.connect_page.secret_edit.text().strip()
        if not secret:
            self.toast.show_message("请先填写配对密钥", theme.WARN)
            self.switch_page(0)
            return

        self._connecting = True
        self.connect_page.connect_btn.setEnabled(False)
        self.connect_page.connect_btn.setText("握手中…")
        self.connect_page.orb.set_state("connecting", "连接中", target)
        self.titlebar.set_status("连接中…", theme.CYAN)
        self.log(f"连接 {target}")

        def task() -> Any:
            return EchoLinkClient.connect(target, secret, timeout=15.0, app="echolink-console")

        self.runner.submit(task, self._on_connected, self._on_connect_error)

    def _on_connected(self, client: EchoLinkClient) -> None:
        self._connecting = False
        self.client = client
        self.settings["target"] = self.connect_page.target_edit.text().strip()
        self.settings["secret"] = self.connect_page.secret_edit.text().strip()
        self._save_settings()

        server = client.server or {}
        name = server.get("name") or server.get("hostname") or "电脑"
        platform = server.get("platform") or ""
        subtitle = str(server.get("hostname") or name)
        self.connect_page.orb.set_state("online", "已连接", subtitle)
        self.titlebar.set_status(f"● {name}", theme.OK)
        self.connect_page.connect_btn.setEnabled(True)
        self.connect_page.connect_btn.setText("断开链路")
        self.reconnect_action.setText("断开连接")
        self.log(f"握手成功: {name} ({platform}),命令 {len(client.commands)} 条")
        self.toast.show_message(f"已接入 {name}", theme.OK)

        self.runner.submit(client.info, self.connect_page.apply_info, lambda e: None)
        self.files_page.refresh()
        self._load_audio()
        self.control_page.load_processes()

    def _on_connect_error(self, message: str) -> None:
        self._connecting = False
        self.connect_page.connect_btn.setEnabled(True)
        self.connect_page.connect_btn.setText("建立链路")
        self.connect_page.orb.set_state("error", "连接失败", message[:22])
        self.titlebar.set_status("未连接", theme.DANGER)
        self.log(f"连接失败: {message}")
        self.toast.show_message(f"连接失败：{message}", theme.DANGER)

    def disconnect(self) -> None:
        client, self.client = self.client, None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        self.connect_page.orb.set_state("offline", "未连接", "等待接入")
        self.connect_page.reset()
        self.titlebar.set_status("未连接", theme.TEXT_DIM)
        self.connect_page.connect_btn.setText("建立链路")
        self.reconnect_action.setText("重新连接")
        self.files_page.list.clear()
        self.files_page.set_path("")
        self.control_page.proc_list.clear()
        self.log("已断开连接")
        self.toast.show_message("已断开", theme.WARN)

    # ------------------------------------------------------------ 任务入口
    def run(
        self,
        fn: Callable[[], Any],
        on_done: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        label: str = "请求",
    ) -> None:
        if self.client is None:
            self.toast.show_message("尚未连接远端", theme.WARN)
            return

        def ok(result: Any) -> None:
            if on_done is not None:
                on_done(result)

        def fail(message: str) -> None:
            self.log(f"{label} 失败: {message}")
            if on_error is not None:
                on_error(message)
            else:
                self.toast.show_message(f"{label}失败：{message}", theme.DANGER)

        self.runner.submit(fn, ok, fail)

    # ------------------------------------------------------------ 功能
    def screenshot(self) -> None:
        if self.client is None:
            self.toast.show_message("尚未连接远端", theme.WARN)
            return
        folder = Path(tempfile.gettempdir()) / "echolink-shots"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / time.strftime("shot-%Y%m%d-%H%M%S.png")

        def done(result: Dict[str, Any]) -> None:
            data = result.get("data") or b""
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            self.log(f"截屏 {pixmap.width()}×{pixmap.height()} · {len(data) / 1024:.0f} KB")
            self.toast.show_message("截屏已取回", theme.OK)
            dialog = ScreenDialog(self, pixmap, str(target))
            dialog.exec()

        self.run(lambda: self.client.screenshot(str(target)), done, label="截屏")

    def clipboard_show(self) -> None:
        if self.client is None:
            self.toast.show_message("尚未连接远端", theme.WARN)
            return
        ClipboardDialog(self, self).exec()

    def _load_audio(self) -> None:
        if self.client is None:
            return
        self.run(
            lambda: self.client.call("system.audio"),
            self.control_page.apply_audio,
            lambda e: None,
            "读取音量",
        )

    def set_autostart(self, checked: bool) -> None:
        try:
            result = autostart_mod.set_enabled(bool(checked))
        except Exception as exc:  # noqa: BLE001
            self.toast.show_message(f"自启设置失败：{exc}", theme.DANGER)
            return
        enabled = bool(result.get("enabled"))
        self.settings["autostart"] = enabled
        self._save_settings()
        self.autostart_action.setChecked(enabled)
        self.connect_page.autostart_switch.blockSignals(True)
        self.connect_page.autostart_switch.setChecked(enabled)
        self.connect_page.autostart_switch._pos = 1.0 if enabled else 0.0
        self.connect_page.autostart_switch.blockSignals(False)
        self.toast.show_message("已开启开机自启" if enabled else "已关闭开机自启", theme.OK if enabled else theme.WARN)
        self.log(f"开机自启 -> {enabled}")

    # ------------------------------------------------------------ 设置
    def _load_settings(self) -> Dict[str, Any]:
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_settings(self) -> None:
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(SETTINGS_PATH, "w", encoding="utf-8") as handle:
                json.dump(self.settings, handle, ensure_ascii=False, indent=2)
        except OSError:
            pass

    # ------------------------------------------------------------ 日志
    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_page.append(f"[{stamp}] {message}")

    def toast_message(self, message: str, accent: Optional[QColor] = None) -> None:
        self.toast.show_message(message, accent)

    def toast(self, message: str, accent: Optional[QColor] = None) -> None:
        self.toast.show_message(message, accent)


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv if argv is None else argv)
    start_hidden = "--tray" in args or "--minimized" in args

    app = QApplication.instance() or QApplication(args[:1])
    app.setApplicationName("EchoLink")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(make_icon()))

    window = ConsoleWindow(start_hidden=start_hidden)
    if not start_hidden:
        window.show()
    else:
        window.show()
        QTimer.singleShot(60, window.hide)

    return app.exec()
