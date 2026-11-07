"""控制台四个功能页：连接、文件、控制、日志。

页面只通过 ``host`` 暴露的几个方法跟窗口交互（提交后台任务、打日志、弹提示），
不直接碰传输层，也不自己在后台线程里跑东西——这样线程模型只有一个入口。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from echolink.client.ui import theme
from echolink.client.ui.graphics import draw_icon
from echolink.client.ui.widgets import (
    Caption,
    Divider,
    GlassCard,
    NeonButton,
    NeonSlider,
    StatTile,
    StatusOrb,
    ToggleSwitch,
    animate,
    vspace,
)


# ---------------------------------------------------------------- 小工具
def human_size(value: Optional[int]) -> str:
    if value is None:
        return "—"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def human_time(value: Optional[float]) -> str:
    if not value:
        return "—"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(value)))
    except (OverflowError, OSError, ValueError):
        return "—"


def human_uptime(seconds: Optional[float]) -> str:
    if not seconds:
        return "—"
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}天 {hours}小时"
    if hours:
        return f"{hours}小时 {minutes}分"
    return f"{minutes}分"


def card(title: str, subtitle: str = "") -> tuple[GlassCard, QVBoxLayout]:
    """创建一张带标题的玻璃卡片，返回 (卡片, 内容布局)。"""
    box = GlassCard()
    outer = QVBoxLayout(box)
    outer.setContentsMargins(18, 16, 18, 16)
    outer.setSpacing(10)
    head = QLabel(title)
    head.setFont(theme.font(10.5, QFont.Weight.DemiBold))
    head.setStyleSheet(f"color: {theme.TEXT.name()}; background: transparent;")
    outer.addWidget(head)
    if subtitle:
        sub = QLabel(subtitle)
        sub.setFont(theme.font(8))
        sub.setStyleSheet(f"color: {theme.TEXT_FAINT.name()}; background: transparent;")
        outer.addWidget(sub)
    return box, outer


def field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setFont(theme.font(8))
    label.setStyleSheet(f"color: {theme.TEXT_DIM.name()}; background: transparent;")
    return label


# ---------------------------------------------------------------- 文件行
class FileRow(QWidget):
    """文件列表的一行：图标 + 名称 + 大小 + 时间，悬停有渐变底。"""

    def __init__(self, entry: Dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.entry = entry
        self._hover = 0.0
        self.setFixedHeight(38)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

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
        from PySide6.QtGui import QLinearGradient, QPainter, QPainterPath

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(2, 1, -2, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        if self._hover > 0.01:
            grad = QLinearGradient(rect.topLeft(), rect.topRight())
            grad.setColorAt(0.0, theme.with_alpha(theme.CYAN, int(30 * self._hover)))
            grad.setColorAt(1.0, theme.with_alpha(theme.INDIGO, int(8 * self._hover)))
            painter.fillPath(path, grad)

        is_dir = self.entry.get("type") == "dir"
        color = theme.CYAN_SOFT if is_dir else theme.TEXT_DIM
        icon_rect = QRectF(rect.left() + 10, rect.center().y() - 8, 16, 16)
        draw_icon(painter, "files" if is_dir else "log", icon_rect, color, 1.4)

        name_rect = QRectF(icon_rect.right() + 10, rect.top(), rect.width() * 0.52, rect.height())
        painter.setPen(theme.TEXT if is_dir else theme.TEXT_DIM)
        painter.setFont(theme.font(9.5, QFont.Weight.Medium if is_dir else QFont.Weight.Normal))
        metrics = QFontMetrics(painter.font())
        name = self.entry.get("name", "")
        painter.drawText(
            name_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            metrics.elidedText(name, Qt.TextElideMode.ElideMiddle, int(name_rect.width())),
        )

        painter.setPen(theme.TEXT_FAINT)
        painter.setFont(theme.font(8.5, mono=True))
        size_text = "目录" if is_dir else human_size(self.entry.get("size"))
        painter.drawText(
            QRectF(rect.right() - 300, rect.top(), 130, rect.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            size_text,
        )
        painter.drawText(
            QRectF(rect.right() - 160, rect.top(), 150, rect.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            human_time(self.entry.get("mtime")),
        )


# ---------------------------------------------------------------- 通用提示
class ConfirmDialog(QDialog):
    """危险操作二次确认，样式与主界面一致。"""

    def __init__(self, parent: QWidget, title: str, message: str, confirm_text: str = "确认执行", danger: bool = True) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self._result = False

        panel = GlassCard(self, radius=16)
        panel.setGeometry(0, 0, 420, 190)
        self.resize(420, 190)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)

        head = QLabel(title)
        head.setFont(theme.font(12, QFont.Weight.Bold))
        head.setStyleSheet(f"color: {theme.TEXT.name()}; background: transparent;")
        layout.addWidget(head)

        body = QLabel(message)
        body.setWordWrap(True)
        body.setFont(theme.font(9))
        body.setStyleSheet(f"color: {theme.TEXT_DIM.name()}; background: transparent;")
        layout.addWidget(body)
        layout.addStretch(1)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        cancel = NeonButton("取消", primary=False, height=34)
        cancel.clicked.connect(self.reject)
        ok = NeonButton(confirm_text, primary=not danger, danger=danger, height=34)
        ok.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        layout.addLayout(row)

    def _accept(self) -> None:
        self._result = True
        self.accept()

    @staticmethod
    def ask(parent: QWidget, title: str, message: str, confirm_text: str = "确认执行") -> bool:
        dialog = ConfirmDialog(parent, title, message, confirm_text)
        dialog.exec()
        return dialog._result


# ---------------------------------------------------------------- 连接页
class ConnectPage(QWidget):
    def __init__(self, host: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.host = host
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        # ---- 左：链路状态 ----
        left, left_box = card("链路状态", "挑战应答认证 · 端到端加密密钥核验")
        left.setMinimumWidth(330)
        left.setMaximumWidth(380)

        self.orb = StatusOrb()
        orb_wrap = QWidget()
        orb_layout = QVBoxLayout(orb_wrap)
        orb_layout.setContentsMargins(0, 4, 0, 4)
        orb_layout.addWidget(self.orb, alignment=Qt.AlignmentFlag.AlignCenter)
        left_box.addWidget(orb_wrap)

        left_box.addWidget(field_label("目标地址"))
        self.target_edit = QLineEdit()
        self.target_edit.setPlaceholderText("192.168.1.8:8765 或 bt:AA:BB:CC:DD:EE:FF")
        self.target_edit.setFont(theme.font(9.5, mono=True))
        left_box.addWidget(self.target_edit)

        left_box.addWidget(field_label("配对密钥"))
        self.secret_edit = QLineEdit()
        self.secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret_edit.setPlaceholderText("64 位十六进制密钥")
        self.secret_edit.setFont(theme.font(9.5, mono=True))
        left_box.addWidget(self.secret_edit)

        self.connect_btn = NeonButton("建立链路", primary=True, height=40, icon="link")
        self.connect_btn.clicked.connect(self.host.toggle_connection)
        left_box.addWidget(self.connect_btn)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.autostart_switch = ToggleSwitch(checked=False)
        self.autostart_switch.toggled.connect(self.host.set_autostart)
        row.addWidget(field_label("开机自启"))
        row.addWidget(self.autostart_switch)
        row.addStretch(1)
        self.tray_hint = Caption("关闭窗口即收进托盘")
        row.addWidget(self.tray_hint)
        left_box.addLayout(row)
        left_box.addStretch(1)
        root.addWidget(left)

        # ---- 右：远端信息 + 快速操作 ----
        right = QVBoxLayout()
        right.setSpacing(16)

        info_card, info_box = card("远端主机", "握手后由服务端上报")
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(2)
        self.tiles: Dict[str, StatTile] = {}
        specs = [
            ("host", "主机名"),
            ("os", "操作系统"),
            ("cpu", "处理器"),
            ("memory", "内存占用"),
            ("disk", "数据盘"),
            ("uptime", "已运行"),
        ]
        for index, (key, label) in enumerate(specs):
            tile = StatTile(label)
            self.tiles[key] = tile
            grid.addWidget(tile, index // 3, index % 3)
        info_box.addLayout(grid)
        info_box.addStretch(1)
        right.addWidget(info_card, 3)

        quick_card, quick_box = card("快速操作", "无需进入控制页")
        quick_row = QHBoxLayout()
        quick_row.setSpacing(10)
        for text, icon, handler in (
            ("延迟探测", "link", self._ping),
            ("静音切换", "control", self._mute),
            ("截屏取回", "screen", self._shot),
            ("读剪贴板", "clip", self._clipboard),
        ):
            button = NeonButton(text, primary=False, height=38, icon=icon)
            button.clicked.connect(handler)
            quick_row.addWidget(button)
        quick_box.addLayout(quick_row)
        quick_box.addStretch(1)
        right.addWidget(quick_card, 2)

        root.addLayout(right, 1)

    # ---- 内部动作 ----
    def _ping(self) -> None:
        started = time.time()

        def done(_: Any) -> None:
            self.host.toast(f"往返延迟 {int((time.time() - started) * 1000)} ms", theme.OK)

        self.host.run(lambda: self.host.client.ping(), done, label="延迟探测")

    def _mute(self) -> None:
        self.host.run(lambda: self.host.client.mute("toggle"), lambda r: self.host.toast("已切换静音状态"), label="静音")

    def _shot(self) -> None:
        self.host.screenshot()

    def _clipboard(self) -> None:
        self.host.clipboard_show()

    # ---- 数据填充 ----
    def apply_info(self, info: Dict[str, Any]) -> None:
        self.tiles["host"].set_value(str(info.get("hostname") or "—"))
        self.tiles["os"].set_value(str(info.get("os") or "—"))
        cores = info.get("cpu_count")
        self.tiles["cpu"].set_value(f"{cores} 核" if isinstance(cores, int) else str(cores or "—"))
        resources = info.get("resources") or {}
        mem = resources.get("memory_percent")
        self.tiles["memory"].set_value(f"{mem:.0f}%" if isinstance(mem, (int, float)) else "—")
        disk_total = resources.get("disk_total")
        disk_free = resources.get("disk_free")
        if isinstance(disk_total, (int, float)) and isinstance(disk_free, (int, float)) and disk_total > 0:
            used_pct = (1 - disk_free / disk_total) * 100
            self.tiles["disk"].set_value(f"剩 {human_size(int(disk_free))}")
            _ = used_pct
        else:
            self.tiles["disk"].set_value("—")
        self.tiles["uptime"].set_value(human_uptime(info.get("uptime_seconds")))

    def reset(self) -> None:
        for tile in self.tiles.values():
            tile.set_value("—")


# ---------------------------------------------------------------- 文件页
class FilesPage(QWidget):
    def __init__(self, host: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.host = host
        self.path = ""
        self.entries: List[Dict[str, Any]] = []
        self.selected: Optional[Dict[str, Any]] = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.up_btn = NeonButton("上级", primary=False, height=34)
        self.up_btn.clicked.connect(self.go_up)
        self.refresh_btn = NeonButton("刷新", primary=False, height=34)
        self.refresh_btn.clicked.connect(self.refresh)
        bar.addWidget(self.up_btn)
        bar.addWidget(self.refresh_btn)

        self.path_label = QLabel("未连接")
        self.path_label.setFont(theme.font(9.5, mono=True))
        self.path_label.setStyleSheet(
            f"color: {theme.TEXT_DIM.name()}; background: rgba(10,18,34,140);"
            f"border: 1px solid rgba(120,170,255,40); border-radius: 8px; padding: 6px 12px;"
        )
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bar.addWidget(self.path_label, 1)

        self.new_btn = NeonButton("新建", primary=False, height=34)
        self.new_btn.clicked.connect(self.create_folder)
        self.upload_btn = NeonButton("上传", primary=False, height=34)
        self.upload_btn.clicked.connect(self.upload)
        self.download_btn = NeonButton("下载", primary=True, height=34)
        self.download_btn.clicked.connect(self.download)
        self.delete_btn = NeonButton("删除", primary=False, danger=True, height=34)
        self.delete_btn.clicked.connect(self.delete)
        for button in (self.new_btn, self.upload_btn, self.download_btn, self.delete_btn):
            bar.addWidget(button)
        root.addLayout(bar)

        head, head_box = card("目录", "双击文件夹进入,双击文件下载")
        head_box.setContentsMargins(6, 6, 6, 6)
        self.list = QListWidget()
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setSpacing(1)
        self.list.itemSelectionChanged.connect(self._on_select)
        self.list.itemDoubleClicked.connect(self._on_open)
        head_box.addWidget(self.list)
        root.addWidget(head, 1)

        self.empty_hint = Caption("尚未连接。先在「连接」页建立链路。")
        root.addWidget(self.empty_hint)

    # ---- 数据 ----
    def set_path(self, path: str) -> None:
        self.path = path or ""
        self.path_label.setText(self.path or "/")

    def render(self, data: Dict[str, Any]) -> None:
        self.list.clear()
        self.selected = None
        self.entries = list(data.get("entries") or [])
        self.set_path(str(data.get("path") or ""))

        total = data.get("count", 0)
        self.empty_hint.setText(f"{total} 项 · {data.get('dirs', 0)} 目录 / {data.get('files', 0)} 文件")

        for index, entry in enumerate(self.entries):
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 40))
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.list.addItem(item)
            row = FileRow(entry)
            self.list.setItemWidget(item, row)
            if index < 22:
                from PySide6.QtWidgets import QGraphicsOpacityEffect

                effect = QGraphicsOpacityEffect(row)
                row.setGraphicsEffect(effect)
                effect.setOpacity(0.0)
                QTimer.singleShot(18 * index, lambda r=row, e=effect: self._fade_in(r, e))

    @staticmethod
    def _fade_in(row: QWidget, effect: Any) -> None:
        anim = animate(row, 0.0, 1.0, lambda v: effect.setOpacity(v), 260, theme.EASE_SOFT)
        _ = anim

    def _on_select(self) -> None:
        items = self.list.selectedItems()
        self.selected = items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def _on_open(self, item: QListWidgetItem) -> None:
        entry = item.data(Qt.ItemDataRole.UserRole) or {}
        if entry.get("type") == "dir":
            self.open_path(entry.get("path", ""))
        else:
            self.selected = entry
            self.download()

    # ---- 动作 ----
    def open_path(self, path: str) -> None:
        def done(data: Dict[str, Any]) -> None:
            self.render(data)

        self.host.run(lambda: self.host.client.list_dir(path), done, label="列目录")

    def refresh(self) -> None:
        self.open_path(self.path)

    def go_up(self) -> None:
        if not self.path:
            return
        parent = os.path.dirname(self.path.rstrip("/"))
        self.open_path(parent)

    def create_folder(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "新建文件夹", "名称")
        if not ok or not name.strip():
            return
        target = f"{self.path.rstrip('/')}/{name.strip()}" if self.path else name.strip()

        def done(_: Any) -> None:
            self.host.toast(f"已创建 {name}", theme.OK)
            self.refresh()

        self.host.run(lambda: self.host.client.mkdir(target), done, label="新建文件夹")

    def upload(self) -> None:
        local, _ = QFileDialog.getOpenFileName(self, "选择要上传的文件")
        if not local:
            return
        name = Path(local).name
        remote = f"{self.path.rstrip('/')}/{name}" if self.path else name

        def done(result: Dict[str, Any]) -> None:
            self.host.toast(f"已上传 {name} · {human_size(result.get('size'))}", theme.OK)
            self.refresh()

        self.host.run(lambda: self.host.client.upload(local, remote), done, label=f"上传 {name}")

    def download(self) -> None:
        entry = self.selected
        if not entry or entry.get("type") == "dir":
            self.host.toast("请先选中一个文件", theme.WARN)
            return
        name = entry.get("name", "file")
        local, _ = QFileDialog.getSaveFileName(self, "另存为", str(Path.home() / name))
        if not local:
            return

        def done(result: Dict[str, Any]) -> None:
            self.host.toast(f"已保存到 {local} · {human_size(result.get('size'))}", theme.OK)

        self.host.run(lambda: self.host.client.download(entry["path"], local), done, label=f"下载 {name}")

    def delete(self) -> None:
        entry = self.selected
        if not entry:
            self.host.toast("请先选中要删除的项", theme.WARN)
            return
        recursive = entry.get("type") == "dir"
        if not ConfirmDialog.ask(
            self,
            "删除确认",
            f"将{'递归' if recursive else ''}删除 {entry.get('path')}，此操作不可撤销。",
            "删除",
        ):
            return

        def done(_: Any) -> None:
            self.host.toast("已删除", theme.OK)
            self.refresh()

        self.host.run(
            lambda: self.host.client.delete(entry["path"], recursive=recursive),
            done,
            label="删除",
        )


# ---------------------------------------------------------------- 控制页
class ControlPage(QWidget):
    def __init__(self, host: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.host = host
        self._volume_dirty = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(16)

        audio_card, audio_box = card("声音与媒体", "写入远端默认输出设备")
        row = QHBoxLayout()
        self.volume_label = QLabel("音量 —")
        self.volume_label.setFont(theme.font(13, QFont.Weight.Bold))
        self.volume_label.setStyleSheet(f"color: {theme.TEXT.name()}; background: transparent;")
        row.addWidget(self.volume_label)
        row.addStretch(1)
        self.mute_switch = ToggleSwitch()
        self.mute_switch.toggled.connect(self._mute_changed)
        row.addWidget(field_label("静音"))
        row.addWidget(self.mute_switch)
        audio_box.addLayout(row)

        self.slider = NeonSlider()
        self.slider.setValue(0)
        self.slider.sliderReleased.connect(self._push_volume)
        self.slider.valueChanged.connect(self._on_slider)
        audio_box.addWidget(self.slider)

        media_row = QHBoxLayout()
        media_row.setSpacing(8)
        for text, action in (("上一首", "prev"), ("播放/暂停", "play_pause"), ("下一首", "next")):
            button = NeonButton(text, primary=False, height=34)
            button.clicked.connect(lambda _=False, a=action: self.host.run(
                lambda: self.host.client.media(a), lambda r: self.host.toast(f"媒体: {a}"), label="媒体控制"
            ))
            media_row.addWidget(button)
        audio_box.addLayout(media_row)
        audio_box.addStretch(1)
        left.addWidget(audio_card, 2)

        power_card, power_box = card("电源", "全部需要二次确认")
        power_grid = QGridLayout()
        power_grid.setSpacing(8)
        powers = [
            ("锁屏", "lock", lambda: self.host.client.lock()),
            ("睡眠", "moon", lambda: self.host.client.sleep()),
            ("注销", "power", lambda: self.host.client.logoff()),
            ("关机", "power", lambda: self.host.client.shutdown()),
            ("重启", "power", lambda: self.host.client.restart()),
        ]
        for index, (text, icon, fn) in enumerate(powers):
            button = NeonButton(text, primary=False, danger=text in ("关机", "重启", "注销"), height=36)
            button.clicked.connect(lambda _=False, t=text, f=fn: self._power(t, f))
            power_grid.addWidget(button, index // 3, index % 3)
        power_box.addLayout(power_grid)
        power_box.addStretch(1)
        left.addWidget(power_card, 1)
        root.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(16)

        desk_card, desk_box = card("桌面交互", "截屏 / 剪贴板 / 通知")
        desk_row = QHBoxLayout()
        desk_row.setSpacing(8)
        shot = NeonButton("截取屏幕", primary=True, height=36, icon="screen")
        shot.clicked.connect(self.host.screenshot)
        clip = NeonButton("剪贴板", primary=False, height=36, icon="clip")
        clip.clicked.connect(self.host.clipboard_show)
        notify = NeonButton("发通知", primary=False, height=36)
        notify.clicked.connect(self._notify)
        for button in (shot, clip, notify):
            desk_row.addWidget(button)
        desk_box.addLayout(desk_row)
        desk_box.addStretch(1)
        right.addWidget(desk_card, 1)

        proc_card, proc_box = card("进程", "按内存占用排序,可强制结束")
        self.proc_list = QListWidget()
        self.proc_list.setFrameShape(QFrame.Shape.NoFrame)
        self.proc_list.setStyleSheet("QListWidget { background: transparent; }")
        self.proc_list.setFont(theme.font(9, mono=True))
        proc_box.addWidget(self.proc_list, 1)
        proc_row = QHBoxLayout()
        refresh = NeonButton("刷新", primary=False, height=32)
        refresh.clicked.connect(self.load_processes)
        kill = NeonButton("结束进程", primary=False, danger=True, height=32)
        kill.clicked.connect(self._kill)
        proc_row.addStretch(1)
        proc_row.addWidget(refresh)
        proc_row.addWidget(kill)
        proc_box.addLayout(proc_row)
        right.addWidget(proc_card, 2)

        root.addLayout(right, 1)

    # ---- 音量 ----
    def _on_slider(self, value: int) -> None:
        self.volume_label.setText(f"音量 {value}")
        self._volume_dirty = True

    def _push_volume(self) -> None:
        if not self._volume_dirty:
            return
        value = self.slider.value()
        self._volume_dirty = False

        def done(result: Dict[str, Any]) -> None:
            level = result.get("level")
            self.volume_label.setText(f"音量 {level}")
            self.host.toast(f"音量已设为 {level}%", theme.OK)

        self.host.run(lambda: self.host.client.volume(value), done, label="设置音量")

    def _mute_changed(self, checked: bool) -> None:
        state = "on" if checked else "off"

        def done(result: Dict[str, Any]) -> None:
            self.host.toast("已静音" if result.get("muted") else "已取消静音", theme.OK)

        self.host.run(lambda: self.host.client.mute(state), done, label="静音")

    def apply_audio(self, data: Dict[str, Any]) -> None:
        level = data.get("level")
        muted = bool(data.get("muted"))
        if isinstance(level, (int, float)):
            self.slider.blockSignals(True)
            self.slider.setValue(int(level))
            self.slider.blockSignals(False)
            self.volume_label.setText(f"音量 {int(level)}")
        else:
            self.volume_label.setText("音量 不可读")
        self.mute_switch.blockSignals(True)
        self.mute_switch.setChecked(muted)
        self.mute_switch._pos = 1.0 if muted else 0.0
        self.mute_switch.blockSignals(False)
        self._volume_dirty = False

    # ---- 电源 ----
    def _power(self, title: str, fn: Callable[[], Any]) -> None:
        if not ConfirmDialog.ask(self, f"{title}确认", f"确定要让远端电脑执行「{title}」吗?", title):
            return
        self.host.run(fn, lambda r: self.host.toast(f"{title}指令已下发", theme.OK), label=title)

    def _notify(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        text, ok = QInputDialog.getText(self, "发送通知", "通知内容")
        if not ok or not text.strip():
            return
        self.host.run(
            lambda: self.host.client.notify("EchoLink", text.strip()),
            lambda r: self.host.toast("通知已发送", theme.OK),
            label="通知",
        )

    # ---- 进程 ----
    def load_processes(self) -> None:
        def done(data: Dict[str, Any]) -> None:
            self.proc_list.clear()
            processes = data.get("processes") or data.get("items") or []
            for proc in processes:
                text = f"{proc.get('pid'):>6}  {human_size(proc.get('memory')) :>10}  {proc.get('name', '')}"
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, proc.get("pid"))
                self.proc_list.addItem(item)

        self.host.run(lambda: self.host.client.processes(40), done, label="进程列表")

    def _kill(self) -> None:
        item = self.proc_list.currentItem()
        if item is None:
            self.host.toast("请先选中一个进程", theme.WARN)
            return
        pid = item.data(Qt.ItemDataRole.UserRole)
        if not ConfirmDialog.ask(self, "结束进程", f"强制结束 PID {pid}?", "结束"):
            return
        self.host.run(
            lambda: self.host.client.kill(int(pid)),
            lambda r: (self.host.toast(f"已结束 PID {pid}", theme.OK), self.load_processes()),
            label="结束进程",
        )


# ---------------------------------------------------------------- 日志页
class LogPage(QWidget):
    def __init__(self, host: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.host = host
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        bar = QHBoxLayout()
        bar.addStretch(1)
        clear = NeonButton("清空", primary=False, height=32)
        clear.clicked.connect(lambda: self.view.clear())
        copy = NeonButton("复制全部", primary=False, height=32)
        copy.clicked.connect(self._copy)
        bar.addWidget(clear)
        bar.addWidget(copy)
        root.addLayout(bar)

        panel, panel_box = card("会话日志", "所有请求与错误的本地记录")
        panel_box.setContentsMargins(10, 10, 10, 10)
        self.view = QTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(theme.font(9, mono=True))
        panel_box.addWidget(self.view)
        root.addWidget(panel, 1)

    def _copy(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self.view.toPlainText())
        self.host.toast("日志已复制", theme.OK)

    def append(self, text: str) -> None:
        self.view.append(text)
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())


# ---------------------------------------------------------------- 截屏预览
class ScreenDialog(QDialog):
    def __init__(self, parent: QWidget, pixmap: QPixmap, saved_path: str) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self._pixmap = pixmap
        self._path = saved_path

        width = min(pixmap.width() + 40, 1180)
        height = min(pixmap.height() + 96, 820)
        self.resize(max(420, width), max(280, height))

        panel = GlassCard(self, radius=16)
        panel.setGeometry(0, 0, self.width(), self.height())
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        head = QLabel(f"远端屏幕 · {pixmap.width()}×{pixmap.height()} · 已存 {saved_path}")
        head.setFont(theme.font(9))
        head.setStyleSheet(f"color: {theme.TEXT_DIM.name()}; background: transparent;")
        layout.addWidget(head)

        view = QLabel()
        view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        view.setStyleSheet("background: transparent;")
        scaled = pixmap.scaled(
            self.width() - 40,
            self.height() - 96,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        view.setPixmap(scaled)
        layout.addWidget(view, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        close = NeonButton("关闭", primary=False, height=32)
        close.clicked.connect(self.accept)
        open_dir = NeonButton("打开所在文件夹", primary=True, height=32)
        open_dir.clicked.connect(self._open_folder)
        row.addWidget(open_dir)
        row.addWidget(close)
        layout.addLayout(row)

    def _open_folder(self) -> None:
        try:
            os.startfile(str(Path(self._path).parent))  # type: ignore[attr-defined]
        except Exception:
            pass


class ClipboardDialog(QDialog):
    def __init__(self, parent: QWidget, host: Any) -> None:
        super().__init__(parent)
        self.host = host
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.resize(620, 420)

        panel = GlassCard(self, radius=16)
        panel.setGeometry(0, 0, 620, 420)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        head = QLabel("远端剪贴板")
        head.setFont(theme.font(11, QFont.Weight.DemiBold))
        head.setStyleSheet(f"color: {theme.TEXT.name()}; background: transparent;")
        layout.addWidget(head)

        self.edit = QTextEdit()
        self.edit.setFont(theme.font(9.5))
        self.edit.setPlaceholderText("读取中…")
        layout.addWidget(self.edit, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        reload_btn = NeonButton("重新读取", primary=False, height=34)
        reload_btn.clicked.connect(self.reload)
        write_btn = NeonButton("写入远端", primary=True, height=34)
        write_btn.clicked.connect(self._write)
        close = NeonButton("关闭", primary=False, height=34)
        close.clicked.connect(self.accept)
        row.addWidget(reload_btn)
        row.addStretch(1)
        row.addWidget(close)
        row.addWidget(write_btn)
        layout.addLayout(row)
        self.reload()

    def reload(self) -> None:
        def done(text: Any) -> None:
            self.edit.setPlainText(str(text or ""))

        self.host.run(lambda: self.host.client.clipboard_get(), done, label="读剪贴板")

    def _write(self) -> None:
        text = self.edit.toPlainText()

        def done(_: Any) -> None:
            self.host.toast("已写入远端剪贴板", theme.OK)

        self.host.run(lambda: self.host.client.clipboard_set(text), done, label="写剪贴板")
