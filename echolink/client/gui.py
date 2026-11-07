"""Windows / 桌面图形客户端(Tkinter,标准库,零额外依赖)。

左侧浏览电脑文件,右侧一键控制(静音、音量、锁屏、关机、截屏、剪贴板、进程)。
所有网络操作用后台线程,UI 只在主线程更新,避免卡死。
"""

from __future__ import annotations

import json
import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

from echolink.client.core import EchoLinkClient, join_remote
from echolink.common.auth import read_secret_file
from echolink.common.errors import RemoteError

SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".echolink", "client.json")


def human_size(value: Optional[int]) -> str:
    if value is None:
        return "-"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


class EchoLinkWindow(tk.Tk):
    """主窗口。"""

    def __init__(self, target: str = "127.0.0.1:8765", secret: str = ""):
        super().__init__()
        self.title("EchoLink 控制台")
        self.geometry("1080x680")
        self.minsize(900, 560)

        self.client: Optional[EchoLinkClient] = None
        self.current_path = ""
        self._entry_map: Dict[str, Dict[str, Any]] = {}
        self._busy = False

        self._build_ui()
        self.target_var.set(target)
        self.secret_var.set(secret or self._load_saved_secret())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.log("提示:先在电脑端运行 `echolink server`,再在这里填入地址与配对密钥。")

    # ==================== UI 构建 ====================
    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill=tk.X)

        ttk.Label(top, text="连接目标").pack(side=tk.LEFT)
        self.target_var = tk.StringVar(value="127.0.0.1:8765")
        ttk.Entry(top, textvariable=self.target_var, width=24).pack(side=tk.LEFT, padx=(4, 10))

        ttk.Label(top, text="配对密钥").pack(side=tk.LEFT)
        self.secret_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.secret_var, width=46, show="*").pack(side=tk.LEFT, padx=(4, 10))

        self.connect_button = ttk.Button(top, text="连接", command=self.toggle_connection)
        self.connect_button.pack(side=tk.LEFT)

        self.status_var = tk.StringVar(value="未连接")
        ttk.Label(top, textvariable=self.status_var, foreground="#0a6").pack(side=tk.LEFT, padx=12)

        body = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)

        body.add(self._build_files_panel(body), weight=3)
        body.add(self._build_control_panel(body), weight=2)

        log_frame = ttk.LabelFrame(self, text="运行日志", padding=6)
        log_frame.pack(fill=tk.BOTH, expand=False, padx=10, pady=(0, 8))
        self.log_text = tk.Text(log_frame, height=7, wrap=tk.WORD, state=tk.DISABLED)
        scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_files_panel(self, parent) -> ttk.Frame:
        frame = ttk.LabelFrame(parent, text="电脑文件", padding=6)

        bar = ttk.Frame(frame)
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="↑ 上级", width=8, command=self.go_parent).pack(side=tk.LEFT)
        ttk.Button(bar, text="刷新", width=6, command=self.refresh_listing).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="下载", width=6, command=self.download_selected).pack(side=tk.LEFT)
        ttk.Button(bar, text="上传", width=6, command=self.upload_file).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="新建目录", width=8, command=self.create_folder).pack(side=tk.LEFT)
        ttk.Button(bar, text="删除", width=6, command=self.delete_selected).pack(side=tk.LEFT, padx=4)

        self.path_var = tk.StringVar(value="/")
        ttk.Label(frame, textvariable=self.path_var, foreground="#555").pack(fill=tk.X, pady=(6, 2))

        columns = ("name", "size", "time")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("name", text="名称")
        self.tree.heading("size", text="大小")
        self.tree.heading("time", text="修改时间")
        self.tree.column("name", width=320, anchor=tk.W)
        self.tree.column("size", width=90, anchor=tk.E)
        self.tree.column("time", width=140, anchor=tk.CENTER)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<Return>", self.on_double_click)

        return frame

    def _build_control_panel(self, parent) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text="远程控制", padding=8)

        volume_frame = ttk.Frame(frame)
        volume_frame.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(volume_frame, text="音量").pack(side=tk.LEFT)
        self.volume_var = tk.IntVar(value=50)
        self.volume_scale = ttk.Scale(
            volume_frame, from_=0, to=100, orient=tk.HORIZONTAL, variable=self.volume_var
        )
        self.volume_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Button(volume_frame, text="应用", width=6, command=self.apply_volume).pack(side=tk.LEFT)

        grid = ttk.Frame(frame)
        grid.pack(fill=tk.BOTH, expand=True)

        buttons = [
            ("切换静音", self.toggle_mute),
            ("截图", self.take_screenshot),
            ("锁屏", self.lock_screen),
            ("睡眠", self.sleep_pc),
            ("关机", self.shutdown_pc),
            ("重启", self.restart_pc),
            ("读取剪贴板", self.read_clipboard),
            ("写入剪贴板", self.write_clipboard),
            ("发通知", self.send_notification),
            ("进程列表", self.show_processes),
            ("打开网址", self.open_url),
            ("播放/暂停", lambda: self.media("play_pause")),
            ("上一首", lambda: self.media("prev")),
            ("下一首", lambda: self.media("next")),
        ]
        for index, (label, handler) in enumerate(buttons):
            button = ttk.Button(grid, text=label, command=handler)
            button.grid(row=index // 2, column=index % 2, sticky="nsew", padx=3, pady=3)
        for column in range(2):
            grid.columnconfigure(column, weight=1)
        for row in range((len(buttons) + 1) // 2):
            grid.rowconfigure(row, weight=1)

        ttk.Button(frame, text="电脑概况", command=self.show_info).pack(fill=tk.X, pady=(8, 0))
        return frame

    # ==================== 基础工具 ====================
    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{stamp}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _task(
        self,
        label: str,
        func: Callable[[], Any],
        on_success: Optional[Callable[[Any], None]] = None,
        quiet: bool = False,
    ) -> None:
        """在后台线程执行,结果回到主线程。"""

        def runner() -> None:
            try:
                result = func()
            except Exception as exc:  # noqa: BLE001 - 统一展示
                self.after(0, lambda e=exc: self._on_error(label, e))
            else:
                self.after(0, lambda r=result: self._on_success(label, r, on_success, quiet))

        threading.Thread(target=runner, daemon=True, name=f"echolink-{label}").start()

    def _on_error(self, label: str, exc: Exception) -> None:
        if isinstance(exc, RemoteError):
            self.log(f"{label} 失败: {exc}")
        else:
            self.log(f"{label} 失败: {type(exc).__name__}: {exc}")
        self.status_var.set("操作失败")

    def _on_success(self, label: str, result: Any, callback, quiet: bool) -> None:
        if not quiet:
            self.log(f"{label} 完成")
        if callback is not None:
            callback(result)

    def _require_client(self) -> Optional[EchoLinkClient]:
        if self.client is None or not self.client.connected:
            messagebox.showwarning("未连接", "请先连接到电脑端服务")
            return None
        return self.client

    # ==================== 连接 ====================
    def _load_saved_secret(self) -> str:
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as handle:
                return str(json.load(handle).get("secret", ""))
        except (OSError, json.JSONDecodeError):
            return ""

    def _save_settings(self) -> None:
        try:
            os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
            with open(SETTINGS_PATH, "w", encoding="utf-8") as handle:
                json.dump({"target": self.target_var.get().strip(), "secret": self.secret_var.get().strip()}, handle)
            os.chmod(SETTINGS_PATH, 0o600)
        except OSError:
            pass

    def toggle_connection(self) -> None:
        if self.client is not None and self.client.connected:
            self._task("断开", self._disconnect)
            return

        target = self.target_var.get().strip()
        secret = self.secret_var.get().strip()
        if not target:
            messagebox.showwarning("缺少地址", "请填写电脑的地址,例如 192.168.1.8:8765")
            return
        if not secret:
            chosen = filedialog.askopenfilename(title="选择密钥文件", filetypes=[("密钥", "*"), ("全部", "*.*")])
            if chosen:
                try:
                    secret = read_secret_file(chosen)
                except Exception as exc:  # noqa: BLE001
                    messagebox.showerror("读取失败", str(exc))
                    return
                self.secret_var.set(secret)
            else:
                messagebox.showwarning("缺少密钥", "请填写配对密钥(服务端配置里的 secret)")
                return

        self.status_var.set("连接中…")
        self.connect_button.configure(state=tk.DISABLED)

        def do_connect() -> EchoLinkClient:
            return EchoLinkClient.connect(target, secret, timeout=15.0, app="echolink-gui")

        self._task("连接", do_connect, self._after_connect, quiet=True)

    def _after_connect(self, client: EchoLinkClient) -> None:
        self.client = client
        self.connect_button.configure(text="断开", state=tk.NORMAL)
        server = client.server or {}
        self.status_var.set(f"已连接: {server.get('name', '')} ({server.get('platform', '')})")
        self.log(f"连接成功,服务端版本 {server.get('version')},共 {len(client.commands)} 条命令")
        self._save_settings()
        self.refresh_listing()

    def _disconnect(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None
        self.connect_button.configure(text="连接")
        self.status_var.set("未连接")
        self.tree.delete(*self.tree.get_children())
        self._entry_map.clear()
        self.log("已断开连接")

    # ==================== 文件浏览 ====================
    def refresh_listing(self) -> None:
        client = self.client
        if client is None:
            return
        path = self.current_path

        def fetch():
            return client.list_dir(path)

        self._task("列出目录", fetch, self._render_listing, quiet=True)

    def _render_listing(self, data: Dict[str, Any]) -> None:
        self.tree.delete(*self.tree.get_children())
        self._entry_map.clear()
        self.current_path = data.get("path") or ""
        self.path_var.set(data.get("absolute") or "/")
        for entry in data.get("entries") or []:
            name = entry["name"] + ("/" if entry["type"] == "dir" else "")
            size = human_size(entry.get("size")) if entry["type"] != "dir" else "<目录>"
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.get("mtime") or 0))
            item_id = self.tree.insert("", tk.END, values=(name, size, stamp))
            self._entry_map[item_id] = entry
        self.status_var.set(f"{data.get('absolute')} —— {data.get('dirs')} 目录 / {data.get('files')} 文件")

    def _selected_entry(self) -> Optional[Dict[str, Any]]:
        selection = self.tree.selection()
        if not selection:
            return None
        return self._entry_map.get(selection[0])

    def on_double_click(self, _event=None) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        if entry["type"] == "dir":
            self.current_path = entry["path"]
            self.refresh_listing()

    def go_parent(self) -> None:
        if not self.current_path or self.client is None:
            return
        parent = os.path.dirname(self.current_path.replace("/", os.sep)).replace(os.sep, "/") or ""
        self.current_path = parent
        self.refresh_listing()

    def download_selected(self) -> None:
        client = self._require_client()
        entry = self._selected_entry()
        if client is None:
            return
        if entry is None or entry["type"] == "dir":
            messagebox.showinfo("提示", "请选择一个文件")
            return
        local = filedialog.asksaveasfilename(initialfilename=entry["name"])
        if not local:
            return
        self.status_var.set(f"下载 {entry['name']} …")

        def do_download():
            return client.download(entry["path"], local)

        self._task("下载", do_download, lambda r: self.status_var.set(f"已保存 {r['path']} ({human_size(r['size'])})"))

    def upload_file(self) -> None:
        client = self._require_client()
        if client is None:
            return
        local = filedialog.askopenfilename(title="选择要上传的文件")
        if not local:
            return
        remote = join_remote(self.current_path, os.path.basename(local))
        size = os.path.getsize(local)
        if not messagebox.askokcancel("确认上传", f"上传到 {remote} ?\n大小 {human_size(size)}"):
            return

        def do_upload():
            return client.upload(local, remote, overwrite=False)

        self._task("上传", do_upload, lambda _: self.refresh_listing())

    def create_folder(self) -> None:
        client = self._require_client()
        if client is None:
            return
        from tkinter import simpledialog

        name = simpledialog.askstring("新建目录", "目录名:", parent=self)
        if not name:
            return
        target = join_remote(self.current_path, name.strip())
        self._task("新建目录", lambda: client.mkdir(target), lambda _: self.refresh_listing())

    def delete_selected(self) -> None:
        client = self._require_client()
        entry = self._selected_entry()
        if client is None:
            return
        if entry is None:
            messagebox.showinfo("提示", "请先选择一项")
            return
        recursive = entry["type"] == "dir"
        prompt = f"确认删除 {entry['path']} ?" + ("\n(目录会被递归删除)" if recursive else "")
        if not messagebox.askokcancel("确认删除", prompt):
            return
        self._task("删除", lambda: client.delete(entry["path"], recursive=recursive), lambda _: self.refresh_listing())

    # ==================== 控制 ====================
    def toggle_mute(self) -> None:
        client = self._require_client()
        if client is None:
            return
        self._task("静音", lambda: client.mute("toggle"), lambda r: self.log(f"静音状态: {r.get('muted')}"))

    def apply_volume(self) -> None:
        client = self._require_client()
        if client is None:
            return
        level = int(self.volume_var.get())
        self._task("音量", lambda: client.volume(level), lambda r: self.log(f"音量 = {r.get('level')}%"))

    def take_screenshot(self) -> None:
        client = self._require_client()
        if client is None:
            return
        target = os.path.join(os.getcwd(), time.strftime("screenshot-%Y%m%d-%H%M%S.png"))

        def do_shot():
            return client.screenshot(target)

        def done(result):
            self.log(f"截图已保存: {result['path']} ({human_size(result['size'])})")
            try:
                os.startfile(result["path"])  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover
                pass

        self._task("截图", do_shot, done)

    def _power(self, label: str, action: str, prompt: str) -> None:
        client = self._require_client()
        if client is None:
            return
        if not messagebox.askokcancel("确认", prompt):
            return
        self._task(label, lambda: getattr(client, action)())

    def lock_screen(self) -> None:
        self._power("锁屏", "lock", "锁定电脑屏幕?")

    def sleep_pc(self) -> None:
        self._power("睡眠", "sleep", "让电脑进入睡眠?")

    def shutdown_pc(self) -> None:
        self._power("关机", "shutdown", "关闭电脑?当前远程连接会中断。")

    def restart_pc(self) -> None:
        self._power("重启", "restart", "重启电脑?当前远程连接会中断。")

    def read_clipboard(self) -> None:
        client = self._require_client()
        if client is None:
            return

        def show(text: str) -> None:
            window = tk.Toplevel(self)
            window.title("电脑剪贴板")
            window.geometry("520x360")
            box = tk.Text(window, wrap=tk.WORD)
            box.insert("1.0", text)
            box.configure(state=tk.DISABLED)
            box.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
            self.log(f"已读取剪贴板({len(text)} 字符)")

        self._task("读取剪贴板", client.clipboard_get, show, quiet=True)

    def write_clipboard(self) -> None:
        client = self._require_client()
        if client is None:
            return
        window = tk.Toplevel(self)
        window.title("写入电脑剪贴板")
        window.geometry("520x320")
        box = tk.Text(window, wrap=tk.WORD)
        box.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        def submit() -> None:
            text = box.get("1.0", "end-1c")
            window.destroy()
            self._task("写入剪贴板", lambda: client.clipboard_set(text), lambda _: self.log("剪贴板已更新"))

        ttk.Button(window, text="写入", command=submit).pack(pady=(0, 8))

    def send_notification(self) -> None:
        client = self._require_client()
        if client is None:
            return
        from tkinter import simpledialog

        message = simpledialog.askstring("桌面通知", "要在电脑上显示的提示:", parent=self)
        if not message:
            return
        self._task("通知", lambda: client.notify("EchoLink", message))

    def open_url(self) -> None:
        client = self._require_client()
        if client is None:
            return
        from tkinter import simpledialog

        target = simpledialog.askstring("打开网址", "URL 或文件路径:", parent=self)
        if not target:
            return
        self._task("打开", lambda: client.open_target(target))

    def media(self, action: str) -> None:
        client = self._require_client()
        if client is None:
            return
        self._task(f"媒体-{action}", lambda: client.media(action))

    def show_processes(self) -> None:
        client = self._require_client()
        if client is None:
            return

        def render(data: Dict[str, Any]) -> None:
            window = tk.Toplevel(self)
            window.title("进程列表")
            window.geometry("520x460")
            frame = ttk.Frame(window, padding=6)
            frame.pack(fill=tk.BOTH, expand=True)
            tree = ttk.Treeview(frame, columns=("pid", "name"), show="headings")
            tree.heading("pid", text="PID")
            tree.heading("name", text="进程")
            tree.column("pid", width=80, anchor=tk.E)
            tree.pack(fill=tk.BOTH, expand=True)
            for item in data.get("processes") or []:
                tree.insert("", tk.END, values=(item.get("pid"), item.get("name")))

            def kill_selected() -> None:
                selection = tree.selection()
                if not selection:
                    return
                pid = tree.item(selection[0], "values")[0]
                if not messagebox.askokcancel("确认", f"结束进程 {pid} ?"):
                    return
                self._task("结束进程", lambda: client.kill(int(pid)), lambda _: window.destroy())

            ttk.Button(frame, text="结束所选进程", command=kill_selected).pack(fill=tk.X, pady=(6, 0))

        self._task("进程列表", lambda: client.processes(300), render)

    def show_info(self) -> None:
        client = self._require_client()
        if client is None:
            return

        def render(data: Dict[str, Any]) -> None:
            window = tk.Toplevel(self)
            window.title("电脑概况")
            window.geometry("560x520")
            text = tk.Text(window, wrap=tk.WORD)
            text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
            lines = [f"{key}: {value}" for key, value in data.items() if key not in ("capabilities",)]
            caps = data.get("capabilities") or {}
            lines.append("能力: " + ", ".join(name for name, ok in caps.items() if ok))
            text.insert("1.0", "\n".join(lines))
            text.configure(state=tk.DISABLED)

        self._task("概况", client.info, render)

    # ==================== 关闭 ====================
    def _on_close(self) -> None:
        try:
            if self.client is not None:
                self.client.close()
        except Exception:
            pass
        self.destroy()


def main(target: str = "127.0.0.1:8765", secret: str = "") -> None:
    app = EchoLinkWindow(target=target, secret=secret)
    app.mainloop()


if __name__ == "__main__":  # pragma: no cover
    main()
