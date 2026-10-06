#!/usr/bin/env python3
"""评估样本的唯一定义处。

背景：这些脚本曾被一个「静默错配」坑过——渲染图用的是 A 照片，比对基准
却用了 B 照片，而 MSE 只是「大得离谱」（约 9500），看起来像「浏览器缩得
很差」，实际是拿两张不同的照片在比。两张照片尺寸、格式完全一样，所以
文件层面的检查发现不了。

教训：样本必须只有一个来源。所有脚本都从这里取，不再各自硬编码路径。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

HOME = Path.home()
CULLUMI = HOME / "Library/Application Support/Cullumi"
PROJECT_ID = "7d97e62d523c8ec6"
PROJECT = CULLUMI / "projects" / PROJECT_ID
DB = PROJECT / "project.db"
THUMBS = PROJECT / "thumbs"
PICTURES = HOME / "Pictures/未命名文件夹"

# 结果目录沿用上游约定：evaluation/performance-results 已被 .gitignore 忽略
RESULTS = Path(__file__).resolve().parents[1] / "performance-results/preview-resolution"
RENDERS = RESULTS / "renders"

# 选定的样本照片。它是各脚本渲染/比对时用的**同一张**，
# 也是截图 measure_screenshot.py 里定位到的那张卡片。
SAMPLE_RELATIVE_PATH = "DSC02017.JPG"
# 截图中用于定位卡片的那张照片（另一张，仅供截图对照使用）
SCREENSHOT_RELATIVE_PATH = "橘望/DSC02554.JPG"


def thumb_path_for(relative_path: str) -> Path:
    """按相对路径从数据库查出缩略图文件名。

    数据库里 thumbnail 列存的是 'thumbs/<sha1>.jpg'，只取文件名再拼。
    """
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT thumbnail FROM photos WHERE relative_path=?",
            (relative_path,)).fetchone()
    finally:
        con.close()
    if not row:
        raise LookupError(f"数据库里找不到 {relative_path}")
    return THUMBS / Path(row[0]).name


def source_path_for(relative_path: str) -> Path:
    return PICTURES / relative_path


SAMPLE_SOURCE = source_path_for(SAMPLE_RELATIVE_PATH)
SAMPLE_THUMB = thumb_path_for(SAMPLE_RELATIVE_PATH)
SCREENSHOT_SOURCE = source_path_for(SCREENSHOT_RELATIVE_PATH)
SCREENSHOT_THUMB = thumb_path_for(SCREENSHOT_RELATIVE_PATH)


def self_check() -> None:
    """开跑前自证：文件都在，且两张样本确实是不同的照片。"""
    for label, p in (("样本原图", SAMPLE_SOURCE), ("样本缩略图", SAMPLE_THUMB),
                     ("截图原图", SCREENSHOT_SOURCE),
                     ("截图缩略图", SCREENSHOT_THUMB)):
        if not p.is_file():
            raise FileNotFoundError(f"{label} 不存在: {p}")
    if SAMPLE_THUMB == SCREENSHOT_THUMB:
        raise ValueError("样本与截图样本指向同一张缩略图，比对会失去意义")


if __name__ == "__main__":
    import numpy as np
    from PIL import Image

    self_check()
    print("样本（渲染与比对统一使用）:")
    print(f"  原图   {SAMPLE_SOURCE}")
    print(f"  缩略图 {SAMPLE_THUMB.name}")
    print("截图对照样本:")
    print(f"  原图   {SCREENSHOT_SOURCE}")
    print(f"  缩略图 {SCREENSHOT_THUMB.name}")
    for label, p in (("样本原图", SAMPLE_SOURCE),
                     ("截图样本原图", SCREENSHOT_SOURCE)):
        with Image.open(p) as im:
            im.load()
            arr = np.asarray(im.convert("RGB"))
        print(f"  {label}: {im.size} 均值 {arr.mean(axis=(0, 1)).round(1)}")
