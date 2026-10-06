#!/usr/bin/env python3
"""屏上实测软度 vs 管线预测：判断「看着糊」到底出在哪一环。

思路：截图里那张卡片是应用真实渲染的结果。把它与「同一张缩略图经各
路径得到的像素」逐一对齐后比较清晰度，就能回答——屏上比管线允许的
最优结果差多少，这个差值只能由浏览器缩放造成。

输出 measurements-onscreen.json
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "performance-results/preview-resolution"
THUMB = Path.home() / (
    "Library/Application Support/Cullumi/projects/7d97e62d523c8ec6/thumbs/"
    "7453150672657801bf5149cd7e9dafbf1541ed37.jpg")  # DSC02554
SOURCE = Path.home() / "Pictures/未命名文件夹/橘望/DSC02554.JPG"
SHOT = Path.home() / "Desktop/截屏2026-10-04 14.18.40.png"

CARD = (215, 161)
XY = (764, 177)  # 模板匹配一致给出的位置


def lapvar(img: Image.Image) -> float:
    g = np.asarray(img.convert("L"), dtype=np.float32)
    if g.shape[0] < 3 or g.shape[1] < 3:
        return float("nan")
    c = g[1:-1, 1:-1]
    return float((-4 * c + g[:-2, 1:-1] + g[2:, 1:-1]
                  + g[1:-1, :-2] + g[1:-1, 2:]).var())


def roi(img: Image.Image) -> Image.Image:
    """去掉边框与留边，取中间真实的画面区域。"""
    w, h = img.size
    return img.crop((3, 14, w - 3, h - 3))


def main() -> int:
    with Image.open(SHOT) as s:
        s.load()
        shot = s.convert("RGB")
    with Image.open(THUMB) as t:
        t.load()
        thumb = t.convert("RGB")

    x, y = XY
    card = shot.crop((x, y, x + CARD[0], y + CARD[1]))
    screen = roi(card)
    screen.save("/tmp/screen_card_roi.png")

    out: dict = {
        "card_box": list(CARD), "xy": list(XY),
        "screen_roi_size": list(screen.size),
        "lapvar_screen": round(lapvar(screen), 1),
        "paths": {},
    }

    # 各路径都裁到与 screen 完全相同的区域尺寸，保证可比
    tgt = screen.size

    def rec(name: str, img: Image.Image) -> None:
        out["paths"][name] = {
            "native": list(img.size),
            "lapvar_at_display": round(lapvar(img.resize(
                tgt, Image.Resampling.LANCZOS)), 1),
        }

    # ① 磁盘上真实缩略图，按各算法缩到显示尺寸
    for n, m in (("LANCZOS", Image.Resampling.LANCZOS),
                 ("BICUBIC", Image.Resampling.BICUBIC),
                 ("BILINEAR", Image.Resampling.BILINEAR),
                 ("BOX", Image.Resampling.BOX)):
        rec(f"磁盘缩略图512 → {n}", thumb.resize(tgt, m))

    # ② 若不做 512 限制，原图直落显示尺寸（质量上限）
    if SOURCE.is_file():
        with Image.open(SOURCE) as s:
            s.load()
            full = s.convert("RGB")
        rec("原图7008 → 显示尺寸 LANCZOS（上限）",
            full.resize(tgt, Image.Resampling.LANCZOS))

    out["gap_vs_ideal"] = round(
        (max(v["lapvar_at_display"] for k, v in out["paths"].items()
             if "上限" in k)
         / out["lapvar_screen"] - 1) * 100, 1) if any(
        "上限" in k for k in out["paths"]) else None

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "measurements-onscreen.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"屏上截取卡片 {CARD}  内容区 {tgt}")
    print(f"{'路径':<34}{'原生尺寸':>12}{'显示尺寸lapvar':>16}")
    print("-" * 64)
    for k, v in out["paths"].items():
        sz = f"{v['native'][0]}x{v['native'][1]}"
        print(f"{k:<34}{sz:>12}{v['lapvar_at_display']:>16.1f}")
    print("-" * 64)
    print(f"{'屏上实测（应用真实渲染）':<34}{'':>12}"
          f"{out['lapvar_screen']:>16.1f}")
    if out["gap_vs_ideal"] is not None:
        print(f"\n屏上距理论上限差距: {out['gap_vs_ideal']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
