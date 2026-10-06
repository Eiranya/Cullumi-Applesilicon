#!/usr/bin/env python3
"""生成排查报告用的图。只用 PIL，不引入新依赖。

图 1  源尺寸扫描：屏上清晰度与保真度随源尺寸的变化
图 2  端到端损失链：每个环节放进同一个显示盒后的清晰度
图 3  重采样算法对照：截屏 / 浏览器 / PIL 各算法的同一块画面

输出到 evaluation/performance-results/preview-resolution/fig*.png
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sample import (  # noqa: E402
    RENDERS,
    RESULTS,
    SAMPLE_SOURCE,
    SAMPLE_THUMB,
    self_check,
)

W, H = 980, 460
PAD_L, PAD_R, PAD_T, PAD_B = 90, 40, 56, 62
BG = (255, 255, 255)
INK = (32, 32, 36)
MUTED = (120, 122, 130)
GRID = (228, 229, 234)
C1 = (196, 74, 74)    # lapvar
C2 = (58, 106, 178)   # mse
ACCENT = (222, 143, 40)


def font(size: int) -> ImageFont.FreeTypeFont:
    # 需要覆盖中文：Arial 没有 CJK 字形，会渲染成豆腐块（方框）
    for p in ("/System/Library/Fonts/Hiragino Sans GB.ttc",
              "/System/Library/Fonts/STHeiti Medium.ttc",
              "/System/Library/Fonts/Supplemental/Songti.ttc",
              "/System/Library/Fonts/Supplemental/Arial.ttf"):
        if Path(p).is_file():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


F_TITLE = font(20)
F_LABEL = font(13)
F_TICK = font(11)
F_NOTE = font(12)


def axes(d: ImageDraw.ImageDraw, xmax: float, ymax: float) -> dict:
    plot = (PAD_L, PAD_T, W - PAD_R, H - PAD_B)
    for i in range(6):
        y = PAD_T + (plot[3] - PAD_T) * i / 5
        d.line([(plot[0], y), (plot[2], y)], fill=GRID)
        val = ymax * (5 - i) / 5
        d.text((plot[0] - 10, y), f"{val:,.0f}", fill=MUTED,
               font=F_TICK, anchor="rm")
    d.line([(plot[0], plot[1]), (plot[0], plot[3])], fill=INK)
    d.line([(plot[0], plot[3]), (plot[2], plot[3])], fill=INK)
    return {"plot": plot, "xmax": xmax, "ymax": ymax}


def sx(ax: dict, v: float) -> float:
    p = ax["plot"]
    return p[0] + (p[2] - p[0]) * v / ax["xmax"]


def sy(ax: dict, v: float) -> float:
    p = ax["plot"]
    return p[3] - (p[3] - p[1]) * v / ax["ymax"]


def fig1() -> None:
    data = json.loads(
        (RESULTS / "measurements-size-fidelity.json").read_text("utf-8"))
    rows = data["rows"]
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((PAD_L, 16), "图1  源尺寸扫描：同一 215 CSS px 卡片盒内实测",
           fill=INK, font=F_TITLE)
    ax = axes(d, 2048, 1600)
    xs = sorted({r["source_px"] for r in rows})
    for x in xs:
        px = sx(ax, x)
        d.line([(px, ax["plot"][3]), (px, ax["plot"][3] + 5)], fill=INK)
        # 128/213/256 挨得太近，隔一个标一个
        if x >= 512 or x in (128, 256, 426):
            d.text((px, ax["plot"][3] + 8), str(x), fill=MUTED,
                   font=F_TICK, anchor="ma")
    d.text((W / 2, H - 22), "源图长边（px）", fill=INK,
           font=F_LABEL, anchor="mm")
    d.text((PAD_L, 44), "屏上 lapvar（清晰度，越高越锐）", fill=C1, font=F_LABEL)

    for dpr, col in ((1, C1), (2, ACCENT)):
        sel = [r for r in rows if r["dpr"] == dpr]
        pts = [(sx(ax, r["source_px"]), sy(ax, r["lapvar"])) for r in sel]
        d.line(pts, fill=col, width=3)
        for (x, y) in pts:
            d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=col)
        lx, ly = pts[-1]
        d.text((lx - 8, ly - 10), f"DPR={dpr}", fill=col, font=F_TICK,
               anchor="ra")
    d.text((PAD_L, H - 44),
           "DPR=1 时源 ≥256px 后曲线走平：512 与 2048 的屏上清晰度只差 0.05%",
           fill=MUTED, font=F_NOTE)
    img.save(FIGDIR / "fig1-size-sweep.png")


def fig2() -> None:
    data = json.loads((RESULTS / "measurements-chain.json").read_text("utf-8"))
    chain = data["chain"]
    img = Image.new("RGB", (W, 420), BG)
    d = ImageDraw.Draw(img)
    d.text((PAD_L, 20), "图2  端到端损失链（每环节都放进同一 215px 显示盒后测）",
           fill=INK, font=F_TITLE)
    base = next(c for c in chain if c["stage"].startswith("源文件"))
    ref = base["lapvar_onscreen"]
    y = 66
    bar_l, bar_w = 330, 560
    for c in chain:
        pct = c["lapvar_onscreen"] / ref * 100
        d.text((bar_l - 12, y + 6), c["stage"], fill=INK,
               font=F_LABEL, anchor="ra")
        wpx = int(bar_w * min(pct, 130) / 130)
        col = C2 if pct >= 99 else MUTED
        d.rectangle([bar_l, y, bar_l + wpx, y + 20], fill=col)
        d.text((bar_l + wpx + 8, y + 10),
               f"{c['lapvar_onscreen']:.0f}  ({pct:.1f}%)",
               fill=INK, font=F_LABEL, anchor="lm")
        y += 32
    d.text((PAD_L, y + 8),
           "512px 上限没有带来可测损失：去掉上限反而与源图持平（100.0%）",
           fill=MUTED, font=F_NOTE)
    img.save(FIGDIR / "fig2-chain.png")


def fig3() -> None:
    self_check()
    with Image.open(SAMPLE_THUMB) as t:
        t.load()
        thumb = t.convert("RGB")
    with Image.open(SAMPLE_SOURCE) as s:
        s.load()
        full = s.convert("RGB")
    box = (215, 143)
    # 局部放大，让差异肉眼可见
    zoom = 2
    crop = (60, 40, 60 + 96, 40 + 64)
    panels = [("源图 7008px 直接缩（理想）", full.resize(box, Image.Resampling.LANCZOS)),
              ("512 缩略图 + LANCZOS", thumb.resize(box, Image.Resampling.LANCZOS)),
              ("512 缩略图 + BICUBIC", thumb.resize(box, Image.Resampling.BICUBIC))]
    wk = RENDERS / "tight-irdefault@dpr1.png"
    if wk.is_file():
        with Image.open(wk) as w:
            w.load()
            panels.append(("WebKit 默认（应用实际所见）", w.convert("RGB")))
    tight = RENDERS / "tight-ir-webkit-optimize-contrast@dpr1.png"
    if tight.is_file():
        with Image.open(tight) as w:
            w.load()
            panels.append(("WebKit optimize-contrast", w.convert("RGB")))

    cw, ch = crop[2] - crop[0], crop[3] - crop[1]
    pw, ph = cw * zoom, ch * zoom
    img = Image.new("RGB", (pw * len(panels) + 12 * (len(panels) - 1), ph + 34),
                    BG)
    d = ImageDraw.Draw(img)
    for i, (label, im) in enumerate(panels):
        piece = im.crop(crop).resize((pw, ph), Image.Resampling.NEAREST)
        px = i * (pw + 12)
        img.paste(piece, (px, 30))
        d.text((px + 2, 10), label, fill=INK, font=F_TICK)
    img.save(FIGDIR / "fig3-resampler.png")


if __name__ == "__main__":
    FIGDIR = Path(__file__).resolve().parent / "figures"
    FIGDIR.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    fig1()
    fig2()
    fig3()
    print("figures written to", FIGDIR)
