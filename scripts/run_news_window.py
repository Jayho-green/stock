"""启动市场雷达桌面小窗。

用法:
    .venv/bin/python scripts/run_news_window.py
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import uvicorn

try:
    import webview
except ImportError as exc:  # pragma: no cover - depends on the local desktop environment
    raise SystemExit(
        "缺少桌面依赖，请先运行: .venv/bin/pip install 'pywebview>=6,<7'"
    ) from exc

from quant.web.app import build_default_app

WINDOW_TITLE = "市场雷达"
WINDOW_WIDTH = 440
WINDOW_HEIGHT = 760
COMPACT_HEIGHT = 520


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.15):
            return True
    except OSError:
        return False


def _wait_for_server(server: uvicorn.Server, port: int, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.started and _port_open(port):
            return
        time.sleep(0.05)
    raise RuntimeError("资讯服务启动超时")


class DesktopBridge:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.window = None

    def attach(self, window) -> None:
        self.window = window

    def set_always_on_top(self, enabled: bool) -> bool:
        if self.window is not None:
            self.window.on_top = bool(enabled)
        return bool(enabled)

    def resize_window(self, compact: bool) -> dict:
        height = COMPACT_HEIGHT if compact else WINDOW_HEIGHT
        if self.window is not None:
            self.window.resize(WINDOW_WIDTH, height)
        return {"width": WINDOW_WIDTH, "height": height}

    def set_badge(self, unread: int) -> int:
        count = max(0, int(unread or 0))
        if self.window is not None:
            title = f"{WINDOW_TITLE} · {count} 条未读" if count else WINDOW_TITLE
            self.window.set_title(title)
        return count

    def notify(self, title: str, body: str) -> bool:
        title = str(title or WINDOW_TITLE)[:80]
        body = str(body or "有新的市场资讯")[:240]
        script = (
            "on run argv\n"
            "display notification (item 2 of argv) with title (item 1 of argv) sound name \"Glass\"\n"
            "end run"
        )
        try:
            subprocess.Popen(
                ["osascript", "-e", script, "--", title, body],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except OSError:
            return False

    def open_external(self, url: str) -> bool:
        target = str(url or "").strip()
        if urlparse(target).scheme not in {"http", "https"}:
            return False
        return bool(webbrowser.open(target))

    def open_dashboard(self, code: str = "") -> bool:
        dashboard = "http://127.0.0.1:8000" if _port_open(8000) else self.base_url
        suffix = f"/?stock={code}" if str(code).isdigit() and len(str(code)) == 6 else "/"
        return bool(webbrowser.open(f"{dashboard}{suffix}"))


def main() -> None:
    parser = argparse.ArgumentParser(description="市场雷达桌面资讯小窗")
    parser.add_argument("--debug", action="store_true", help="打开 WebView 调试模式")
    args = parser.parse_args()

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    app = build_default_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, name="market-radar-api", daemon=True)
    server_thread.start()

    try:
        _wait_for_server(server, port)
        bridge = DesktopBridge(base_url)
        window = webview.create_window(
            WINDOW_TITLE,
            f"{base_url}/news-window",
            js_api=bridge,
            width=WINDOW_WIDTH,
            height=WINDOW_HEIGHT,
            min_size=(360, 480),
            resizable=True,
            on_top=True,
            background_color="#090b0e",
            text_select=False,
            zoomable=False,
        )
        bridge.attach(window)
        icon = ROOT / "assets" / "market-radar-icon.png"
        webview.start(
            gui="cocoa",
            debug=args.debug,
            private_mode=False,
            storage_path=str(ROOT / "data" / "news_window_webview"),
            icon=str(icon) if icon.exists() else None,
        )
    finally:
        server.should_exit = True
        server_thread.join(timeout=5)


if __name__ == "__main__":
    main()
