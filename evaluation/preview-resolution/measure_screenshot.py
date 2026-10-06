#!/usr/bin/env python3
"""在用户截图里精确定位一张卡片，再与真实 WebKit 渲染逐像素对照。

做法：把该照片的缩略图按各重采样算法缩到卡片尺寸，在截图中做二维
粗到细模板匹配，取匹配度最高者。匹配度高才说明定位正确，数值才有意义。

输出 measurements-screenshot.json 与 fig3-screenshot-vs-pil-algos.png
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "performance-results/preview-resolution"
THUMB_ROOT = Path.home() / (
    "Library/Application Support/Cullumi/projects/7d97e62d523c8ec6/thumbs")
SHOT = Path.home() / "Desktop/截屏2026-10-04 14.18.40.png"
REF_HASH = "7453150672657801bf5149cd7e9dafbf1541ed37"  # DSC02554

# 卡片缩略图区尺寸：由 CSS 决定，215.19 x 161.39 CSS px @DPR1
CARD = (215, 161)


def ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    denom = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / denom) if denom else 0.0


def coarse_to_fine(shot_g: np.ndarray, tpl: np.ndarray,
                   scale: int = 4) -> tuple[int, int, float]:
    """整幅粗搜 + 邻域细搜，返回 (x, y, ncc)。"""
    H, W = shot_g.shape
    th, tw = tpl.shape
    best = (0, 0, -2.0)
    # 粗搜
    for y in range(0, H - th, scale):
        for x in range(0, W - tw, scale):
            v = ncc(shot_g[y:y + th, x:x + tw], tpl)
            if v > best[2]:
                best = (x, y, v)
    # 细搜
    bx, by = best[0], best[1]
    for y in range(max(0, by - scale), min(H - th, by + scale) + 1):
        for x in range(max(0, bx - scale), min(W - tw, bx + scale) + 1):
            v = ncc(shot_g[y:y + th, x:x + tw], tpl)
            if v > best[2]:
                best = (x, y, v)
    return best


def main() -> int:
    with Image.open(SHOT) as s:
        s.load()
        shot = s.convert("RGB")
    with Image.open(THUMB_ROOT / f"{REF_HASH}.jpg") as t:
        t.load()
        thumb = t.convert("RGB")

    shot_g = np.asarray(shot.convert("L"), dtype=np.float32)
    print(f"截图 {shot.size}  缩略图 {thumb.size}  卡片目标 {CARD}")

    algs = (("LANCZOS", Image.Resampling.LANCZOS),
            ("BICUBIC", Image.Resampling.BICUBIC),
            ("BILINEAR", Image.Resampling.BILINEAR),
            ("BOX", Image.Resampling.BOX),
            ("HAMMING", Image.Resampling.HAMMING),
            ("NEAREST", Image.Resampling.NEAREST))

    out: dict = {"screenshot": str(SHOT), "card_target": list(CARD),
                 "comparisons": {}}
    renders: dict[str, tuple[Image.Image, tuple[int, int]]] = {}

    for name, m in algs:
        tpl = np.asarray(thumb.resize(CARD, m).convert("L"), dtype=np.float32)
        x, y, score = coarse_to_fine(shot_g, tpl)
        out["comparisons"][f"PIL_{name}"] = {
            "ncc": round(score, 4), "xy": [x, y]}
        renders[name] = (thumb.resize(CARD, m), (x, y))
        print(f"  {name:<9} ncc={score:.4f}  at ({x},{y})")

    # WebKit 真实渲染（用同一张 DSC02554 的缩略图渲染的图）
    wk_file = RESULTS / "renders/wk-dsc02554-tight.png"
    if wk_file.is_file():
        with Image.open(wk_file) as w:
            w.load()
            wk = w.convert("RGB")
        wk_tpl = wk.resize(CARD, Image.Resampling.LANCZOS)
        tpl = np.asarray(wk_tpl.convert("L"), dtype=np.float32)
        x, y, score = coarse_to_fine(shot_g, tpl)
        out["comparisons"]["WebKit_真实渲染"] = {
            "ncc": round(score, 4), "xy": [x, y]}
        print(f"  {'WebKit':<9} ncc={score:.4f}  at ({x},{y})")
        renders["WebKit"] = (wk_tpl, (x, y))

    ranked = sorted(out["comparisons"].items(),
                    key=lambda kv: -kv[1]["ncc"])
    out["ranking"] = [{"name": k, "ncc": v["ncc"], "xy": v["xy"]}
                      for k, v in ranked]
    best_name, best_val = ranked[0]
    out["best_match"] = {"name": best_name, "ncc": best_val["ncc"],
                         "xy": best_val["xy"]}

    # 对照图：截图裁片 / 最佳算法 / WebKit
    bx, by = best_val["xy"]
    shot_crop = shot.crop((bx, by, bx + CARD[0], by + CARD[1]))
    picks = [("screenshot (NCC best)", shot_crop)]
    picks.append((f"PIL {best_name.replace('PIL_', '')}",
                  renders[best_name.replace("PIL_", "")][0]))
    if "WebKit" in renders:
        picks.append(("WebKit actual render", renders["WebKit"][0]))
    else:
        picks.append(("PIL LANCZOS (ideal)",
                      thumb.resize(CARD, Image.Resampling.LANCZOS)))

    fig = Image.new("RGB", (CARD[0] * len(picks) + 12 * (len(picks) - 1),
                            CARD[1] + 32), (255, 255, 255))
    d = ImageDraw.Draw(fig)
    for i, (label, img) in enumerate(picks):
        px = i * (CARD[0] + 12)
        fig.paste(img, (px, 30))
        d.text((px + 2, 10), label, fill=(0, 0, 0))
    RESULTS.mkdir(parents=True, exist_ok=True)
    fig.save(RESULTS / "fig3-screenshot-vs-pil-algos.png")

    (RESULTS / "measurements-screenshot.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n最佳匹配: {best_name}  NCC={best_val['ncc']:.4f} @ {best_val['xy']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
