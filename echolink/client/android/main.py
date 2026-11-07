"""Android App(Kivy):连接电脑后浏览文件、下载上传、远程控制。

连接方式:
* 蓝牙 —— 手机与电脑配对后,用 ``bt:MAC`` 直连(推荐,不需要同一 Wi-Fi);
* 局域网 —— ``192.168.1.8:8765``(需要同一网络,速度更快)。

所有网络操作在后台线程执行,通过 ``Clock.schedule_once`` 回主线程刷新界面。
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.uix.screenmanager import Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.slider import Slider
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.uix.textinput import TextInput

from echolink.client.core import EchoLinkClient, join_remote
from echolink.common.auth import read_secret_file
from echolink.common.errors import RemoteError

ACCENT = (0.15, 0.55, 0.85, 1)
DANGER = (0.85, 0.28, 0.28, 1)
MUTED = (0.55, 0.55, 0.58, 1)


def human_size(value: Optional[int]) -> str:
    if value is None:
        return "-"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def request_android_permissions() -> None:  # pragma: no cover - 仅安卓
    """申请存储与蓝牙权限(Android 6+ 需要运行时授权)。"""
    try:
        from android.permissions import Permission, request_permissions  # type: ignore
    except Exception:
        return
    wanted = []
    for name in (
        "READ_EXTERNAL_STORAGE",
        "WRITE_EXTERNAL_STORAGE",
        "BLUETOOTH",
        "BLUETOOTH_CONNECT",
        "BLUETOOTH_SCAN",
    ):
        perm = getattr(Permission, name, None)
        if perm:
            wanted.append(perm)
    if wanted:
        try:
            request_permissions(wanted)
        except Exception:
            pass


class EchoLinkApp(App):
    """Android 应用主体。"""

    title = "EchoLink"

    # ==================== 生命周期 ====================
    def build(self):
        request_android_permissions()
        self.settings = self._load_settings()
        self.client: Optional[EchoLinkClient] = None
        self.cwd = ""
        self._last_volume_sent = 0.0

        self.manager = ScreenManager()
        self.manager.add_widget(self._build_connect_screen())
        self.manager.add_widget(self._build_main_screen())
        return self.manager

    def on_stop(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass

    # ==================== 设置持久化 ====================
    @property
    def settings_path(self) -> str:
        return os.path.join(self.user_data_dir, "config.json")

    def _load_settings(self) -> Dict[str, str]:
        try:
            with open(self.settings_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_settings(self) -> None:
        try:
            os.makedirs(self.user_data_dir, exist_ok=True)
            with open(self.settings_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {"target": self.target_input.text.strip(), "secret": self.secret_input.text.strip()},
                    handle,
                )
        except OSError:
            pass

    @property
    def download_dir(self) -> str:
        for candidate in ("/storage/emulated/0/Download", "/sdcard/Download"):
            if os.path.isdir(candidate) and os.access(candidate, os.W_OK):
                return candidate
        return self.user_data_dir

    # ==================== 连接界面 ====================
    def _build_connect_screen(self) -> Screen:
        screen = Screen(name="connect")
        layout = BoxLayout(orientation="vertical", padding=dp(18), spacing=dp(10))

        layout.add_widget(Label(text="[b]EchoLink[/b]", markup=True, font_size=dp(26), size_hint_y=None, height=dp(56)))
        layout.add_widget(
            Label(
                text="以电脑为中心:手机可浏览电脑文件、上传下载、控制音量与开关机。",
                font_size=dp(13),
                color=MUTED,
                text_size=(dp(320), None),
                size_hint_y=None,
                height=dp(56),
            )
        )

        layout.add_widget(Label(text="连接地址", size_hint_y=None, height=dp(22), font_size=dp(13)))
        self.target_input = TextInput(
            text=self.settings.get("target", "192.168.1.100:8765"),
            multiline=False,
            size_hint_y=None,
            height=dp(44),
            font_size=dp(15),
        )
        layout.add_widget(self.target_input)

        layout.add_widget(Label(text="配对密钥", size_hint_y=None, height=dp(22), font_size=dp(13)))
        self.secret_input = TextInput(
            text=self.settings.get("secret", ""),
            multiline=False,
            size_hint_y=None,
            height=dp(44),
            password=True,
            font_size=dp(15),
        )
        layout.add_widget(self.secret_input)

        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        row.add_widget(Button(text="蓝牙配对设备", on_release=lambda *_: self.show_paired_devices()))
        row.add_widget(Button(text="从文件导入密钥", on_release=lambda *_: self.import_secret()))
        layout.add_widget(row)

        self.connect_button = Button(text="连接电脑", size_hint_y=None, height=dp(52), background_color=ACCENT)
        self.connect_button.bind(on_release=lambda *_: self.do_connect())
        layout.add_widget(self.connect_button)

        self.connect_status = Label(text="未连接", size_hint_y=None, height=dp(28), color=MUTED)
        layout.add_widget(self.connect_status)

        layout.add_widget(WidgetSpacer())
        screen.add_widget(layout)
        return screen

    def show_paired_devices(self) -> None:
        """列出手机已配对的蓝牙设备,选中即填 bt:MAC。"""
        try:
            from echolink.transport.android_bt import list_paired_devices

            devices = list_paired_devices()
        except Exception as exc:  # pragma: no cover - 非安卓
            self.toast(f"蓝牙不可用: {exc}")
            return
        if not devices:
            self.toast("没有已配对的设备,请先在系统设置里配对电脑")
            return

        content = BoxLayout(orientation="vertical", spacing=dp(4))
        grid = GridLayout(cols=1, size_hint_y=None, spacing=dp(4))
        grid.bind(minimum_height=grid.setter("height"))
        for device in devices:
            label = f"{device.get('name') or '未命名'}  ({device.get('address')})"
            button = Button(text=label, size_hint_y=None, height=dp(46), font_size=dp(13))
            button.bind(
                on_release=lambda _btn, addr=device.get("address"): self._pick_bluetooth(addr)
            )
            grid.add_widget(button)
        scroll = ScrollView()
        scroll.add_widget(grid)
        content.add_widget(scroll)
        content.add_widget(Button(text="关闭", size_hint_y=None, height=dp(42), on_release=lambda *_: popup.dismiss()))
        popup = Popup(title="选择电脑", content=content, size_hint=(0.92, 0.75))
        popup.open()

    def _pick_bluetooth(self, address: Optional[str]) -> None:
        if not address:
            return
        self.target_input.text = f"bt:{address}"
        self.toast("已选择蓝牙设备,点击连接")

    def import_secret(self) -> None:
        """从手机存储里选一个密钥文件导入(需 Kivy 文件选择器)。"""
        try:
            from kivy.uix.filechooser import FileChooserListView
        except Exception:  # pragma: no cover
            self.toast("当前环境不支持文件选择")
            return
        chooser = FileChooserListView(path=self.download_dir, filters=["*.txt", "*.secret", "*"])
        content = BoxLayout(orientation="vertical")
        content.add_widget(chooser)
        buttons = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        popup = Popup(title="选择密钥文件", content=content, size_hint=(0.95, 0.85))

        def confirm(*_args) -> None:
            if not chooser.selection:
                return
            try:
                self.secret_input.text = read_secret_file(chooser.selection[0])
                self.toast("密钥已导入")
            except Exception as exc:  # noqa: BLE001
                self.toast(f"读取失败: {exc}")
            popup.dismiss()

        buttons.add_widget(Button(text="取消", on_release=lambda *_: popup.dismiss()))
        buttons.add_widget(Button(text="导入", on_release=confirm))
        content.add_widget(buttons)
        popup.open()

    # ==================== 主界面 ====================
    def _build_main_screen(self) -> Screen:
        screen = Screen(name="main")
        layout = BoxLayout(orientation="vertical")

        top = BoxLayout(size_hint_y=None, height=dp(48), padding=(dp(8), dp(4)), spacing=dp(6))
        self.server_label = Label(text="未连接", halign="left", valign="middle", font_size=dp(14))
        self.server_label.bind(size=lambda widget, value: setattr(widget, "text_size", value))
        top.add_widget(self.server_label)
        top.add_widget(Button(text="断开", size_hint_x=None, width=dp(76), on_release=lambda *_: self.disconnect()))
        layout.add_widget(top)

        self.tabs = TabbedPanel(do_default_tab=False, tab_width=dp(110))
        files_item = self._build_files_tab()
        self.tabs.add_widget(files_item)
        self.tabs.add_widget(self._build_controls_tab())
        self.tabs.add_widget(self._build_log_tab())
        layout.add_widget(self.tabs)
        self.tabs.switch_to(files_item)

        screen.add_widget(layout)
        return screen

    def _build_files_tab(self) -> TabbedPanelItem:
        item = TabbedPanelItem(text="文件")
        layout = BoxLayout(orientation="vertical", padding=dp(6), spacing=dp(6))

        bar = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(6))
        bar.add_widget(Button(text="↑ 上级", size_hint_x=None, width=dp(84), on_release=lambda *_: self.go_parent()))
        bar.add_widget(Button(text="刷新", size_hint_x=None, width=dp(64), on_release=lambda *_: self.refresh_listing()))
        bar.add_widget(Button(text="上传", size_hint_x=None, width=dp(64), on_release=lambda *_: self.upload_here()))
        layout.add_widget(bar)

        self.path_label = Label(text="/", size_hint_y=None, height=dp(24), font_size=dp(12), color=MUTED,
                                halign="left", valign="middle", shorten=True, shorten_from="left")
        self.path_label.bind(size=lambda widget, value: setattr(widget, "text_size", value))
        layout.add_widget(self.path_label)

        self.file_grid = GridLayout(cols=1, size_hint_y=None, spacing=dp(4))
        self.file_grid.bind(minimum_height=self.file_grid.setter("height"))
        scroll = ScrollView()
        scroll.add_widget(self.file_grid)
        layout.add_widget(scroll)

        item.add_widget(layout)
        return item

    def _build_controls_tab(self) -> TabbedPanelItem:
        item = TabbedPanelItem(text="控制")
        layout = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(8))

        volume_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        volume_row.add_widget(Label(text="音量", size_hint_x=None, width=dp(48)))
        self.volume_slider = Slider(min=0, max=100, value=50, step=1)
        self.volume_slider.bind(on_touch_up=self._on_volume_released)
        volume_row.add_widget(self.volume_slider)
        layout.add_widget(volume_row)

        grid = GridLayout(cols=2, spacing=dp(6), size_hint_y=None)
        grid.bind(minimum_height=grid.setter("height"))
        actions = [
            ("切换静音", lambda: self.simple_call("静音", lambda c: c.mute("toggle"))),
            ("锁屏", lambda: self.confirm("锁定电脑屏幕?", lambda c: c.lock())),
            ("睡眠", lambda: self.confirm("让电脑进入睡眠?", lambda c: c.sleep())),
            ("关机", lambda: self.confirm("关闭电脑?远程连接会中断。", lambda c: c.shutdown(), danger=True)),
            ("重启", lambda: self.confirm("重启电脑?远程连接会中断。", lambda c: c.restart(), danger=True)),
            ("截屏", lambda: self.take_screenshot()),
            ("读剪贴板", lambda: self.read_clipboard()),
            ("写剪贴板", lambda: self.write_clipboard()),
            ("发通知", lambda: self.send_notification()),
            ("进程列表", lambda: self.show_processes()),
            ("电脑概况", lambda: self.show_info()),
            ("播放/暂停", lambda: self.simple_call("媒体", lambda c: c.media("play_pause"))),
        ]
        for label, handler in actions:
            button = Button(text=label, size_hint_y=None, height=dp(46), font_size=dp(13))
            button.bind(on_release=lambda _btn, fn=handler: fn())
            grid.add_widget(button)
        layout.add_widget(grid)

        hint = Label(
            text="提示:关机/重启/结束进程属于危险操作,界面会二次确认;所有动作都会记录在电脑的审计日志里。",
            font_size=dp(11),
            color=MUTED,
            size_hint_y=None,
            height=dp(54),
            text_size=(dp(300), None),
        )
        layout.add_widget(hint)
        layout.add_widget(WidgetSpacer())
        item.add_widget(layout)
        return item

    def _build_log_tab(self) -> TabbedPanelItem:
        item = TabbedPanelItem(text="日志")
        layout = BoxLayout(orientation="vertical", padding=dp(6))
        self.log_view = TextInput(text="", readonly=True, font_size=dp(11), size_hint=(1, 1))
        layout.add_widget(self.log_view)
        item.add_widget(layout)
        return item

    # ==================== 连接逻辑 ====================
    def do_connect(self) -> None:
        if self.client is not None and self.client.connected:
            self.manager.current = "main"
            return
        target = self.target_input.text.strip()
        secret = self.secret_input.text.strip()
        if not target or not secret:
            self.toast("请填写连接地址与配对密钥")
            return
        self.connect_button.disabled = True
        self.connect_status.text = "连接中…"

        def worker() -> None:
            try:
                client = EchoLinkClient.connect(target, secret, timeout=20.0, app="echolink-android")
            except Exception as exc:  # noqa: BLE001
                Clock.schedule_once(lambda dt, e=exc: self._connect_failed(e), 0)
            else:
                Clock.schedule_once(lambda dt, c=client: self._connect_done(c), 0)

        threading.Thread(target=worker, daemon=True, name="echolink-connect").start()

    def _connect_done(self, client: EchoLinkClient) -> None:
        self.client = client
        self.connect_button.disabled = False
        server = client.server or {}
        self.connect_status.text = f"已连接 {server.get('name', '')}"
        self.server_label.text = f"{server.get('name', '电脑')} · {server.get('platform', '')}"
        self._save_settings()
        self.log(f"连接成功: {server.get('name')} ({server.get('platform')}),命令 {len(client.commands)} 条")
        self.manager.current = "main"
        self.refresh_listing()

    def _connect_failed(self, exc: Exception) -> None:
        self.connect_button.disabled = False
        self.connect_status.text = "连接失败"
        self.toast(f"连接失败: {exc}")

    def disconnect(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None
        self.manager.current = "connect"
        self.connect_status.text = "未连接"
        self.log("已断开")

    # ==================== 通用任务 ====================
    def run_task(
        self,
        label: str,
        func: Callable[[], Any],
        on_success: Optional[Callable[[Any], None]] = None,
    ) -> None:
        def worker() -> None:
            try:
                result = func()
            except RemoteError as exc:
                Clock.schedule_once(lambda dt, e=exc: self._task_failed(label, str(e)), 0)
            except Exception as exc:  # noqa: BLE001
                Clock.schedule_once(lambda dt, e=exc: self._task_failed(label, f"{type(e).__name__}: {e}"), 0)
            else:
                if on_success is not None:
                    Clock.schedule_once(lambda dt, r=result: on_success(r), 0)
                else:
                    Clock.schedule_once(lambda dt: self.log(f"{label} 完成"), 0)

        threading.Thread(target=worker, daemon=True, name=f"echolink-{label}").start()

    def _task_failed(self, label: str, message: str) -> None:
        self.log(f"{label} 失败: {message}")
        self.toast(message)

    def require_client(self) -> Optional[EchoLinkClient]:
        if self.client is None or not self.client.connected:
            self.toast("尚未连接电脑")
            return None
        return self.client

    def simple_call(self, label: str, func: Callable[[EchoLinkClient], Any]) -> None:
        client = self.require_client()
        if client is None:
            return
        self.run_task(label, lambda: func(client), lambda result: self.log(f"{label}: {result}"))

    def confirm(
        self,
        question: str,
        func: Callable[[EchoLinkClient], Any],
        danger: bool = False,
    ) -> None:
        content = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))
        content.add_widget(Label(text=question, text_size=(dp(260), None), halign="center"))
        buttons = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        popup = Popup(title="请确认", content=content, size_hint=(0.86, 0.4))

        def proceed(*_args) -> None:
            popup.dismiss()
            self.simple_call("操作", func)

        buttons.add_widget(Button(text="取消", on_release=lambda *_: popup.dismiss()))
        yes = Button(text="确定", background_color=DANGER if danger else ACCENT, on_release=proceed)
        buttons.add_widget(yes)
        content.add_widget(buttons)
        popup.open()

    def toast(self, message: str) -> None:
        self.log(message)
        try:  # 安卓原生 Toast
            from android import mActivity  # type: ignore
            from jnius import autoclass  # type: ignore

            Toast = autoclass("android.widget.Toast")
            Toast.makeText(mActivity, message, Toast.LENGTH_SHORT).show()
        except Exception:
            pass

    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {message}\n"
        view = getattr(self, "log_view", None)
        if view is not None:
            view.text += line
        print(line.strip())

    # ==================== 文件浏览 ====================
    def refresh_listing(self) -> None:
        client = self.client
        if client is None:
            return
        path = self.cwd

        def done(data: Dict[str, Any]) -> None:
            self.cwd = data.get("path") or ""
            self.path_label.text = data.get("absolute") or "/"
            self.file_grid.clear_widgets()
            entries = data.get("entries") or []
            if not entries:
                self.file_grid.add_widget(
                    Label(text="(空目录)", size_hint_y=None, height=dp(40), color=MUTED)
                )
            for entry in entries:
                self.file_grid.add_widget(self._entry_button(entry))
            self.log(f"共 {len(entries)} 项")

        self.run_task("列出目录", lambda: client.list_dir(path), done)

    def _entry_button(self, entry: Dict[str, Any]) -> Button:
        is_dir = entry.get("type") == "dir"
        icon = "📁" if is_dir else "📄"
        size = "" if is_dir else f"  {human_size(entry.get('size'))}"
        button = Button(
            text=f"{icon}  {entry['name']}{size}",
            size_hint_y=None,
            height=dp(46),
            font_size=dp(13),
            halign="left",
            valign="middle",
        )
        button.bind(size=lambda widget, value: setattr(widget, "text_size", (value[0] - dp(16), value[1])))
        button.bind(on_release=lambda _btn, e=entry: self._on_entry_tap(e))
        return button

    def _on_entry_tap(self, entry: Dict[str, Any]) -> None:
        if entry.get("type") == "dir":
            self.cwd = entry["path"]
            self.refresh_listing()
            return
        self._file_actions(entry)

    def _file_actions(self, entry: Dict[str, Any]) -> None:
        content = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))
        content.add_widget(
            Label(text=f"{entry['name']}\n{human_size(entry.get('size'))}", size_hint_y=None, height=dp(48))
        )
        popup = Popup(title="文件操作", content=content, size_hint=(0.86, 0.6))

        def add(label: str, handler: Callable[[], None], danger: bool = False) -> None:
            button = Button(
                text=label,
                size_hint_y=None,
                height=dp(44),
                background_color=DANGER if danger else (0.25, 0.45, 0.65, 1),
            )
            button.bind(on_release=lambda *_: (popup.dismiss(), handler()))
            content.add_widget(button)

        add("下载到手机", lambda: self.download_entry(entry))
        add("预览文本", lambda: self.preview_entry(entry))
        add("删除", lambda: self.delete_entry(entry), danger=True)
        add("取消", lambda: None)
        popup.open()

    def go_parent(self) -> None:
        if not self.cwd:
            return
        head = self.cwd.rstrip("/").rsplit("/", 1)
        self.cwd = "" if len(head) < 2 else head[0]
        self.refresh_listing()

    def download_entry(self, entry: Dict[str, Any]) -> None:
        client = self.require_client()
        if client is None:
            return
        target = os.path.join(self.download_dir, entry["name"])
        total = int(entry.get("size") or 0)
        bar = ProgressBar(max=100, value=0)
        content = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))
        content.add_widget(Label(text=f"下载 {entry['name']}", size_hint_y=None, height=dp(28)))
        content.add_widget(bar)
        popup = Popup(title="正在下载", content=content, size_hint=(0.85, 0.35), auto_dismiss=False)
        popup.open()

        last = {"t": 0.0}

        def progress(done: int, size: int) -> None:
            now = time.monotonic()
            if now - last["t"] < 0.15 and done < size:
                return
            last["t"] = now
            percent = int(done * 100 / size) if size else 100
            Clock.schedule_once(lambda dt: setattr(bar, "value", percent), 0)

        def finished(result: Dict[str, Any]) -> None:
            popup.dismiss()
            self.log(f"已保存到 {result['path']} ({human_size(result['size'])})")
            self.toast(f"下载完成: {result['path']}")

        def failed_fix(exception: Exception) -> None:
            popup.dismiss()
            self.toast(str(exception))

        def worker() -> None:
            try:
                result = client.download(entry["path"], target, progress=progress)
            except Exception as exc:  # noqa: BLE001
                Clock.schedule_once(lambda dt, e=exc: failed_fix(e), 0)
            else:
                Clock.schedule_once(lambda dt, r=result: finished(r), 0)

        threading.Thread(target=worker, daemon=True, name="echolink-download").start()

    def upload_here(self) -> None:
        client = self.require_client()
        if client is None:
            return
        try:
            from kivy.uix.filechooser import FileChooserListView
        except Exception:  # pragma: no cover
            self.toast("当前环境不支持文件选择")
            return
        chooser = FileChooserListView(path=self.download_dir, dirselect=False)
        content = BoxLayout(orientation="vertical")
        content.add_widget(chooser)
        popup = Popup(title="选择要上传的文件", content=content, size_hint=(0.95, 0.85))
        buttons = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        content.add_widget(buttons)

        def confirm(*_args) -> None:
            if not chooser.selection:
                return
            local = chooser.selection[0]
            popup.dismiss()
            remote = join_remote(self.cwd, os.path.basename(local))
            self.run_task(
                "上传",
                lambda: client.upload(local, remote, overwrite=False),
                lambda result: (self.log(f"已上传 {result['path']}"), self.refresh_listing()),
            )

        buttons.add_widget(Button(text="取消", on_release=lambda *_: popup.dismiss()))
        buttons.add_widget(Button(text="上传", background_color=ACCENT, on_release=confirm))
        popup.open()

    def preview_entry(self, entry: Dict[str, Any]) -> None:
        client = self.require_client()
        if client is None:
            return

        def done(data: Dict[str, Any]) -> None:
            content = BoxLayout(orientation="vertical", padding=dp(6))
            if data.get("binary"):
                content.add_widget(Label(text="这是二进制文件,无法预览"))
            else:
                content.add_widget(
                    TextInput(text=data.get("text", ""), readonly=True, font_size=dp(11), size_hint=(1, 1))
                )
            content.add_widget(Button(text="关闭", size_hint_y=None, height=dp(42), on_release=lambda *_: popup.dismiss()))
            popup.content = content
            popup.open()

        popup = Popup(title=entry["name"], content=BoxLayout(), size_hint=(0.95, 0.85))
        self.run_task("预览", lambda: client.preview(entry["path"]), done)

    def delete_entry(self, entry: Dict[str, Any]) -> None:
        client = self.require_client()
        if client is None:
            return
        recursive = entry.get("type") == "dir"

        def do_delete() -> None:
            self.run_task(
                "删除",
                lambda: client.delete(entry["path"], recursive=recursive),
                lambda _result: self.refresh_listing(),
            )

        self.confirm(f"确认删除 {entry['name']} ?", lambda _client: do_delete(), danger=True)

    # ==================== 控制 ====================
    def _on_volume_released(self, _slider, _touch) -> None:
        soon = time.monotonic() + 0.25
        if soon < self._last_volume_sent:
            return
        self._last_volume_sent = soon
        level = int(self.volume_slider.value)
        self.simple_call("音量", lambda c: c.volume(level))

    def take_screenshot(self) -> None:
        client = self.require_client()
        if client is None:
            return
        target = os.path.join(self.download_dir, time.strftime("shot-%Y%m%d-%H%M%S.png"))

        popup = Popup(title="电脑屏幕", content=BoxLayout(), size_hint=(0.95, 0.85))

        def done(result: Dict[str, Any]) -> None:
            content = BoxLayout(orientation="vertical", padding=dp(6))
            try:
                content.add_widget(Image(source=result["path"]))
            except Exception:
                content.add_widget(Label(text=f"已保存: {result['path']}"))
            content.add_widget(Button(text="关闭", size_hint_y=None, height=dp(42), on_release=lambda *_: popup.dismiss()))
            popup.content = content
            popup.open()
            self.log(f"截图已保存 {result['path']}")

        self.run_task("截图", lambda: client.screenshot(target), done)

    def read_clipboard(self) -> None:
        client = self.require_client()
        if client is None:
            return

        def done(text: str) -> None:
            content = BoxLayout(orientation="vertical", padding=dp(6))
            content.add_widget(TextInput(text=text, readonly=True, font_size=dp(12)))
            content.add_widget(Button(text="关闭", size_hint_y=None, height=dp(42), on_release=lambda *_: popup.dismiss()))
            popup.content = content
            popup.open()

        popup = Popup(title="电脑剪贴板", content=BoxLayout(), size_hint=(0.92, 0.7))
        self.run_task("读剪贴板", client.clipboard_get, done)

    def write_clipboard(self) -> None:
        client = self.require_client()
        if client is None:
            return
        content = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))
        box = TextInput(multiline=True, font_size=dp(12))
        content.add_widget(box)
        popup = Popup(title="写入电脑剪贴板", content=content, size_hint=(0.92, 0.7))

        def submit(*_args) -> None:
            text = box.text
            popup.dismiss()
            self.run_task("写剪贴板", lambda: client.clipboard_set(text), lambda _r: self.log("剪贴板已更新"))

        content.add_widget(Button(text="写入", size_hint_y=None, height=dp(44), background_color=ACCENT, on_release=submit))
        popup.open()

    def send_notification(self) -> None:
        client = self.require_client()
        if client is None:
            return
        content = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))
        box = TextInput(multiline=False, hint_text="要显示在电脑上的提示")
        content.add_widget(box)
        popup = Popup(title="桌面通知", content=content, size_hint=(0.92, 0.4))

        def submit(*_args) -> None:
            message = box.text.strip()
            popup.dismiss()
            if message:
                self.run_task("通知", lambda: client.notify("EchoLink", message), lambda _r: self.log("已发送通知"))

        content.add_widget(Button(text="发送", size_hint_y=None, height=dp(44), background_color=ACCENT, on_release=submit))
        popup.open()

    def show_processes(self) -> None:
        client = self.require_client()
        if client is None:
            return

        def done(data: Dict[str, Any]) -> None:
            content = BoxLayout(orientation="vertical", padding=dp(6), spacing=dp(4))
            grid = GridLayout(cols=2, size_hint_y=None, spacing=dp(2))
            grid.bind(minimum_height=grid.setter("height"))
            grid.add_widget(Label(text="PID", size_hint_y=None, height=dp(24), bold=True))
            grid.add_widget(Label(text="进程", size_hint_y=None, height=dp(24), bold=True))
            for item in data.get("processes") or []:
                grid.add_widget(Label(text=str(item.get("pid")), size_hint_y=None, height=dp(22), font_size=dp(11)))
                grid.add_widget(Label(text=str(item.get("name")), size_hint_y=None, height=dp(22), font_size=dp(11)))
            scroll = ScrollView()
            scroll.add_widget(grid)
            content.add_widget(scroll)
            content.add_widget(Button(text="关闭", size_hint_y=None, height=dp(42), on_release=lambda *_: popup.dismiss()))
            popup.content = content
            popup.open()

        popup = Popup(title="电脑进程", content=BoxLayout(), size_hint=(0.92, 0.85))
        self.run_task("进程列表", lambda: client.processes(200), done)

    def show_info(self) -> None:
        client = self.require_client()
        if client is None:
            return

        def done(data: Dict[str, Any]) -> None:
            resources = data.get("resources") or {}
            lines = [
                f"名称: {data.get('server')}",
                f"主机: {data.get('hostname')}",
                f"系统: {data.get('os')}",
                f"平台: {data.get('platform')} / {data.get('backend')}",
                f"运行: {int(data.get('uptime_seconds') or 0)} 秒",
                f"内存: {human_size(resources.get('memory_total'))} / 已用 {resources.get('memory_percent')}%",
                f"磁盘剩余: {human_size(resources.get('disk_free'))}",
            ]
            audio = data.get("audio") or {}
            if audio.get("level") is not None:
                lines.append(f"音量: {audio.get('level')}%{' (静音)' if audio.get('muted') else ''}")
            lines.append("可访问目录:")
            for root in data.get("roots") or []:
                lines.append(f"  · {root.get('path')}")
            content = BoxLayout(orientation="vertical", padding=dp(8))
            content.add_widget(TextInput(text="\n".join(lines), readonly=True, font_size=dp(12)))
            content.add_widget(Button(text="关闭", size_hint_y=None, height=dp(42), on_release=lambda *_: popup.dismiss()))
            popup.content = content
            popup.open()

        popup = Popup(title="电脑概况", content=BoxLayout(), size_hint=(0.92, 0.8))
        self.run_task("概况", client.info, done)


class WidgetSpacer(BoxLayout):
    """占位,把上方内容顶上去。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


def run() -> None:  # pragma: no cover - 需要 Kivy 运行环境
    EchoLinkApp().run()


if __name__ == "__main__":  # pragma: no cover
    run()
