"""启动本地网页面板:浏览器打开 http://127.0.0.1:8000

用法:
    .venv/bin/python scripts/run_web.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import uvicorn

from quant.web.app import build_default_app

if __name__ == "__main__":
    app = build_default_app()
    uvicorn.run(app, host="127.0.0.1", port=8000)
