"""生成可从 Finder 双击启动的 macOS 市场雷达.app。"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dist" / "市场雷达.app"
CONTENTS = APP / "Contents"
MACOS = CONTENTS / "MacOS"
RESOURCES = CONTENTS / "Resources"
ICON_PNG = ROOT / "assets" / "market-radar-icon.png"


def build_icon() -> None:
    size = 1024
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((58, 58, 966, 966), radius=214, fill="#090b0e", outline="#28313a", width=12)
    center = size // 2
    for radius, width, color in [(316, 18, "#214943"), (226, 16, "#2c6860"), (134, 14, "#3b887e")]:
        draw.ellipse(
            (center - radius, center - radius, center + radius, center + radius),
            outline=color,
            width=width,
        )
    draw.line((center, center, 788, 254), fill="#a78bfa", width=24)
    draw.ellipse((center - 42, center - 42, center + 42, center + 42), fill="#5eead4")
    draw.ellipse((736, 212, 790, 266), fill="#ff6577")
    ICON_PNG.parent.mkdir(parents=True, exist_ok=True)
    image.save(ICON_PNG)


def build_icns() -> None:
    iconset = ROOT / "data" / ".market-radar.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True)
    source = Image.open(ICON_PNG)
    for points in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            pixels = points * scale
            name = f"icon_{points}x{points}{'@2x' if scale == 2 else ''}.png"
            source.resize((pixels, pixels), Image.Resampling.LANCZOS).save(iconset / name)
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(RESOURCES / "AppIcon.icns")], check=True)
    shutil.rmtree(iconset)


def install() -> None:
    python = ROOT / ".venv" / "bin" / "python"
    if not python.exists():
        raise SystemExit("未找到 .venv，请先创建项目虚拟环境")

    if APP.exists():
        shutil.rmtree(APP)
    MACOS.mkdir(parents=True)
    RESOURCES.mkdir(parents=True)
    build_icon()
    build_icns()

    launcher = MACOS / "market-radar"
    launcher.write_text(
        "#!/bin/zsh\n"
        f"cd {str(ROOT)!r}\n"
        f"exec {str(python)!r} {str(ROOT / 'scripts' / 'run_news_window.py')!r} "
        f">> {str(ROOT / 'data' / 'news_window.log')!r} 2>&1\n",
        encoding="utf-8",
    )
    os.chmod(launcher, 0o755)

    info = {
        "CFBundleDevelopmentRegion": "zh_CN",
        "CFBundleDisplayName": "市场雷达",
        "CFBundleExecutable": "market-radar",
        "CFBundleIconFile": "AppIcon",
        "CFBundleIdentifier": "com.jayho.quant.market-radar",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "市场雷达",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
    }
    with (CONTENTS / "Info.plist").open("wb") as handle:
        plistlib.dump(info, handle, sort_keys=False)
    subprocess.run(["touch", str(APP)], check=True)
    print(APP)


if __name__ == "__main__":
    install()
