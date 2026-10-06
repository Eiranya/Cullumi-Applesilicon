#!/usr/bin/env python3
"""供给尺寸 A/B：按显示尺寸供给 vs 当前固定 512，谁在屏上更清晰、更保真？

回答「要不要做 srcset/按尺寸供给」这个决策。三个指标一起看，避免被单一
指标误导（lapvar 奖励走样、MSE 惩罚锐化，两者方向可能相反）：

  lapvar          屏上清晰度（人眼感知的"锐"）
  mse_vs_ideal    与「原图 LANCZOS 落格」的距离（保真）
  edge_overshoot  边缘过冲（走样会抬高它）

输出 measurements-serve-size.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sample import RESULTS, SAMPLE_SOURCE, self_check  # noqa: E402

RENDERS = RESULTS / "renders"
# 卡片里图片的实际显示框（DPR=1 下的设备像素），由 measure_webkit.mjs 量得
CANDIDATES = (215, 216, 256, 341, 430, 512)


def lapvar(img: Image.Image) -> float:
    g = np.asarray(img.convert("L"), dtype=np.float32)
    c = g[1:-1, 1:-1]
    return float((-4 * c + g[:-2, 1:-1] + g[2:, 1:-1]
                  + g[1:-1, :-2] + g[1:-1, 2:]).var())


def content_box(img: Image.Image) -> Image.Image:
    """裁掉 object-fit:contain 的底色留边，否则 MSE 被背景主导。"""
    a = np.asarray(img.convert("L"))
    bg = float(np.median(a[0, :]))
    cols = np.where(np.abs(a.astype(np.float32) - bg).max(axis=0) > 10)[0]
    rows = np.where(np.abs(a.astype(np.float32) - bg).max(axis=1) > 10)[0]
    if len(cols) < 8 or len(rows) < 8:
        return img
    return img.crop((cols[0], rows[0], cols[-1] + 1, rows[-1] + 1))


def edge_overshoot(img: Image.Image) -> float:
    g = np.asarray(img.convert("L"), dtype=np.float32)
    grad = np.abs(np.diff(g, axis=1))
    thr = np.percentile(grad, 99)
    if thr <= 0:
        return 0.0
    ys, xs = np.where(grad >= thr)
    vals = []
    for y, x in zip(ys, xs):
        if x < 3 or x > g.shape[1] - 4:
            continue
        left, right = g[y, x - 3:x].mean(), g[y, x + 1:x + 4].mean()
        step = right - left
        if abs(step) < 12:
            continue
        peak = g[y, x] - left if step > 0 else left - g[y, x]
        vals.append(max(0.0, peak - abs(step)))
    return float(np.mean(vals)) if vals else 0.0


def main() -> int:
    self_check()
    with Image.open(SAMPLE_SOURCE) as s:
        s.load()
        full = s.convert("RGB")

    rows: list[dict] = []
    ideal_cache: dict[str, Image.Image] = {}

    for dpr in (1, 2):
        for size in CANDIDATES:
            p = RENDERS / f"sz{size}@dpr{dpr}.png"
            if not p.is_file():
                continue
            with Image.open(p) as im:
                im.load()
                r = content_box(im.convert("RGB"))
            key = f"{r.size[0]}x{r.size[1]}"
            if key not in ideal_cache:
                ideal_cache[key] = full.resize(r.size, Image.Resampling.LANCZOS)
            ideal = ideal_cache[key]
            ms = float(((np.asarray(r, dtype=np.float32)
                         - np.asarray(ideal, dtype=np.float32)) ** 2).mean())
            rows.append({
                "dpr": dpr, "served_px": size,
                "device_px_needed": round(215.1875 * dpr),
                "downscale_ratio": round(size / (215.1875 * dpr), 2),
                "content_size": list(r.size),
                "lapvar": round(lapvar(r), 1),
                "mse_vs_ideal": round(ms, 2),
                "edge_overshoot": round(edge_overshoot(r), 2),
            })

    out = {"note": "三种指标一起看；lapvar 与 MSE 可能方向相反",
           "rows": rows}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "measurements-serve-size.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    for dpr in (1, 2):
        sel = [r for r in rows if r["dpr"] == dpr]
        if not sel:
            continue
        need = sel[0]["device_px_needed"]
        print(f"\nDPR={dpr}  显示框需要 {need} 设备像素  "
              f"（内容 {sel[0]['content_size'][0]}x{sel[0]['content_size'][1]}）")
        print(f"  {'供给':>6}{'倍率':>7}{'lapvar':>10}{'MSE↓理想':>11}{'过冲↓':>9}")
        best_lap = max(sel, key=lambda r: r["lapvar"])
        best_mse = min(sel, key=lambda r: r["mse_vs_ideal"])
        for r in sel:
            mark = []
            if r is best_lap:
                mark.append("最锐")
            if r is best_mse:
                mark.append("最保真")
            print(f"  {r['served_px']:>6}{r['downscale_ratio']:>7.2f}"
                  f"{r['lapvar']:>10.1f}{r['mse_vs_ideal']:>11.2f}"
                  f"{r['edge_overshoot']:>9.2f}  {' / '.join(mark)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
