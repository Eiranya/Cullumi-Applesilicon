#!/usr/bin/env python3
"""交叉对照：WebKit 的实际渲染最接近 PIL 的哪种重采样。

MSE 是「与理想值的距离」，不能说清浏览器到底用了什么算法。这里改成
两两互比：把 WebKit 渲染结果与 PIL 各算法的结果逐像素比对，距离最近的
那个就是它在数值上最接近的行为。

输出 measurements-crossmatch.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sample import RESULTS, SAMPLE_SOURCE, SAMPLE_THUMB, self_check  # noqa: E402

THUMB = SAMPLE_THUMB
SOURCE = SAMPLE_SOURCE
RENDERS = RESULTS / "renders"
BOX = (215, 143)


def mse(a: Image.Image, b: Image.Image) -> float:
    x = np.asarray(a.convert("RGB"), dtype=np.float32)
    y = np.asarray(b.convert("RGB"), dtype=np.float32)
    if x.shape != y.shape:
        raise ValueError(f"形状不一致 {x.shape} vs {y.shape}")
    return float(((x - y) ** 2).mean())


def masked_mse(a, b, mask):
    x = np.asarray(a.convert("RGB"), dtype=np.float32)
    y = np.asarray(b.convert("RGB"), dtype=np.float32)
    m = mask[:, :, None]
    return float((((x - y) ** 2) * m).sum() / (m.sum() * 3))


def edge_mask(img: Image.Image, pct: float = 75.0) -> np.ndarray:
    """只保留有结构的区域——平坦背景会把所有算法的差异抹平。"""
    g = np.asarray(img.convert("L"), dtype=np.float32)
    grad = np.zeros_like(g)
    grad[1:-1, 1:-1] = np.abs(
        -4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1]
        + g[1:-1, :-2] + g[1:-1, 2:])
    return grad > np.percentile(grad, pct)


def main() -> int:
    self_check()
    with Image.open(SOURCE) as s:
        s.load()
        full = s.convert("RGB")
    with Image.open(THUMB) as t:
        t.load()
        thumb = t.convert("RGB")

    # PIL 候选按每个渲染图各自的尺寸现构造，见下方循环
    cands: dict[str, Image.Image] = {}

    out: dict = {"box": list(BOX), "webkit_vs_pil": {}, "edge_mask": {}}

    # 关键：DPR=2 的渲染图是 430x286 设备像素。若先把它缩到 215 再比，
    # 等于多加一道 LANCZOS，会把差异抹平、让所有算法看起来都像 LANCZOS。
    # 所以按**各自的原始渲染尺寸**构造候选，再逐像素比。
    texts = sorted(RENDERS.glob("tight-*.png"))
    for p in texts:
        with Image.open(p) as im:
            im.load()
            img = im.convert("RGB")
        target = img.size  # (215,143) 或 (430,286)
        cands = {
            f"PIL_{n}": thumb.resize(target, m)
            for n, m in (("LANCZOS", Image.Resampling.LANCZOS),
                         ("BICUBIC", Image.Resampling.BICUBIC),
                         ("BILINEAR", Image.Resampling.BILINEAR),
                         ("BOX", Image.Resampling.BOX),
                         ("HAMMING", Image.Resampling.HAMMING),
                         ("NEAREST", Image.Resampling.NEAREST))
        }
        cands["原图直落LANCZOS上限"] = full.resize(
            target, Image.Resampling.LANCZOS)

        row = {name: round(mse(img, cand), 2) for name, cand in cands.items()}
        best = min(row, key=row.get)
        m = edge_mask(cands["PIL_LANCZOS"])
        out["edge_mask"][p.stem] = {
            name: round(masked_mse(img, cand, m), 2)
            for name, cand in cands.items()}
        out["webkit_vs_pil"][p.stem] = {
            "render_size": list(target),
            "all": row, "closest": best, "closest_mse": row[best]}

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "measurements-crossmatch.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("WebKit 渲染 vs PIL 各算法（按各自渲染尺寸逐像素 MSE，越小越像）")
    print("-" * 100)
    names = ["PIL_LANCZOS", "PIL_BICUBIC", "PIL_BILINEAR", "PIL_BOX",
             "PIL_HAMMING", "PIL_NEAREST", "原图直落LANCZOS上限"]
    print(f"{'WebKit 变体':<40}{'渲染尺寸':>10}" + "".join(
        f"{n.replace('PIL_', '').replace('原图直落LANCZOS上限', '上限'):>10}"
        for n in names))
    for stem, v in out["webkit_vs_pil"].items():
        sz = f"{v['render_size'][0]}x{v['render_size'][1]}"
        line = f"{stem:<40}{sz:>10}" + "".join(
            f"{v['all'][n]:>10.2f}" for n in names)
        print(line + f"   ← {v['closest']}")
    print()
    print("只看有结构的区域（边缘掩膜，排除平坦背景）")
    print("-" * 96)
    for stem, v in out["edge_mask"].items():
        top = sorted(v.items(), key=lambda x: x[1])[:3]
        txt = "  ".join(f"{k}={val:.1f}" for k, val in top)
        print(f"{stem:<40} {txt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
