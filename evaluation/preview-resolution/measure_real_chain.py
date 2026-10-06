#!/usr/bin/env python3
"""真实供给链路的验收测量：旧链路 / 新链路 / 理想 三档对照。

与 measure_serve_size.py 的关键差别：
    该脚本的「供给」是从原图现算的理想图，因此它对比的其实永远是最优解本身
    （其「供给 215」的 MSE=0.54 就是理想图本身），**根本不检验真实供给链路**，
    会让保真度门槛（A3/B3）假性通过。本脚本改为**调用真实的
    ``ensure_display_asset``**，产出浏览器实际会拿到的那些字节，再与理想比对。

三档定义（每个 DPR）：
    旧链路    供 512 分析缩略图；浏览器把它按 WebKit 级的滤波器缩到显示框设备像素
    新链路    供 round(213 × DPR) 的显示资产（ensure_display_asset 真实产物），1:1 渲染
    理想      原图 → 显示框设备像素 的 LANCZOS（无限算力的下界）

指标：lapvar（屏上清晰度）、MSE↓理想（保真）、edge_overshoot（走样）。
另附编码器对照：q90 4:2:0（上一轮）vs q95 4:4:4（本轮），量化字节与保真度代价。

⚠️ 旧链路的「浏览器下采样」这一步在本脚本里用 PIL BICUBIC 模拟，**显著偏乐观**：
   真实 WebKit 的滤波器更软，DPR=1 时旧链路真实 MSE=25.31，而 BICUBIC 模拟只有
   4.43（低估 5.7×），lapvar 也被高估（模拟 1499.8 vs 真实 1175.3）。因此**本脚本的
   旧链路数值只可用于各链路的相对量级自检，不可用于结论**；权威对照以真实 WebKit
   渲染为准（DPR=1 旧25.31→新7.50、DPR=2 旧11.88→新10.19，新链路两口径同时更优）。
   新链路的「供给图 1:1 渲染」不受此影响，其 MSE/lapvar 与 WebKit 一致。

输出 evaluation/performance-results/preview-resolution/measurements-real-chain.json
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sample import (  # noqa: E402
    RESULTS,
    SAMPLE_SOURCE,
    SAMPLE_THUMB,
    self_check,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from cullumi.display_asset import ensure_display_asset  # noqa: E402

# 卡片图片框（CSS px），与 web/js/gallery.js 的 CARD_THUMB_BOX 对齐。
CARD_THUMB_BOX_CSS = 213
DPRS = (1, 2)

# 浏览器把供给图缩进显示框时用的滤波器。WebKit 的下采样约为 BICUBIC 级；这里用
# BICUBIC 模拟（不同 WebKit 版本会有小幅出入，故旧链路只作量级对照）。
BROWSER_FILTER = Image.Resampling.BICUBIC

# 编码器对照：(质量, 抽样)。subsampling=0 → 4:4:4；2 → 4:2:0。
# 第一项是上一轮的基线；末尾是本轮采用的参数；中间项给主理人一条取舍曲线。
ENCODER_VARIANTS = (
    ("q90-4:2:0", 90, 2),
    ("q90-4:4:4", 90, 0),
    ("q92-4:4:4", 92, 0),
    ("q95-4:4:4", 95, 0),
)


def lapvar(image: Image.Image) -> float:
    g = np.asarray(image.convert("L"), dtype=np.float32)
    c = g[1:-1, 1:-1]
    return float((-4 * c + g[:-2, 1:-1] + g[2:, 1:-1]
                  + g[1:-1, :-2] + g[1:-1, 2:]).var())


def edge_overshoot(image: Image.Image) -> float:
    g = np.asarray(image.convert("L"), dtype=np.float32)
    grad = np.abs(np.diff(g, axis=1))
    thr = np.percentile(grad, 99)
    if thr <= 0:
        return 0.0
    ys, xs = np.where(grad >= thr)
    values = []
    for y, x in zip(ys, xs):
        if x < 3 or x > g.shape[1] - 4:
            continue
        left, right = g[y, x - 3:x].mean(), g[y, x + 1:x + 4].mean()
        step = right - left
        if abs(step) < 12:
            continue
        peak = g[y, x] - left if step > 0 else left - g[y, x]
        values.append(max(0.0, peak - abs(step)))
    return float(np.mean(values)) if values else 0.0


def mse_vs_ideal(image: Image.Image, ideal: Image.Image) -> float:
    return float(((np.asarray(image.convert("RGB"), dtype=np.float32)
                   - np.asarray(ideal, dtype=np.float32)) ** 2).mean())


def _lanczos_width(image: Image.Image, width: int) -> Image.Image:
    height = max(1, round(image.height * width / image.width))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def _encode_from_thumbnail(
    thumbnail: Path, width: int, quality: int, subsampling: int
) -> bytes:
    """复刻 display_asset 的派生过程，但用指定的编码参数，返回 JPEG 字节。"""
    with Image.open(thumbnail) as source:
        source.load()
        resized = _lanczos_width(source.convert("RGB"), width)
        buffer = io.BytesIO()
        try:
            resized.save(
                buffer, "JPEG",
                quality=quality, subsampling=subsampling, optimize=True,
            )
        finally:
            resized.close()
    return buffer.getvalue()


def _decode(payload: bytes) -> Image.Image:
    with Image.open(io.BytesIO(payload)) as image:
        image.load()
        return image.convert("RGB")


def _row(
    chain: str, dpr: int, box_px: int, served_px: int,
    supply_bytes: int, render: Image.Image, ideal: Image.Image,
) -> dict:
    return {
        "dpr": dpr, "chain": chain, "box_px": box_px, "served_px": served_px,
        "supply_bytes": supply_bytes,
        "downscale_ratio": round(served_px / box_px, 3),
        "lapvar": round(lapvar(render), 1),
        "mse_vs_ideal": round(mse_vs_ideal(render, ideal), 2),
        "edge_overshoot": round(edge_overshoot(render), 2),
    }


def main() -> int:
    self_check()
    print(f"样本原图 {SAMPLE_SOURCE.name}  缩略图 {SAMPLE_THUMB.name}")
    with Image.open(SAMPLE_SOURCE) as source:
        source.load()
        full = source.convert("RGB")

    chain_rows: list[dict] = []
    encoder_rows: list[dict] = []

    for dpr in DPRS:
        box_px = round(CARD_THUMB_BOX_CSS * dpr)
        ideal = _lanczos_width(full, box_px)

        # 旧链路：真实 512 缩略图 → 浏览器缩到显示框。
        with Image.open(SAMPLE_THUMB) as thumb:
            thumb.load()
            old_render = _lanczos_width(thumb.convert("RGB"), box_px)
        old_bytes = SAMPLE_THUMB.stat().st_size

        # 新链路：真实 ensure_display_asset 产物，1:1（不再重采样）。
        asset_path = ensure_display_asset(SAMPLE_THUMB, box_px)
        new_bytes = asset_path.stat().st_size
        with Image.open(asset_path) as asset:
            asset.load()
            new_render = asset.convert("RGB")

        chain_rows.append(_row("旧链路 供512", dpr, box_px, 512, old_bytes,
                               old_render, ideal))
        chain_rows.append(_row("新链路 供box", dpr, box_px, box_px, new_bytes,
                               new_render, ideal))
        chain_rows.append(_row("理想 原图LANCZOS", dpr, box_px, box_px, 0,
                               ideal, ideal))

        # 编码器对照（同一 512 缩略图 → box 尺寸，只改编码参数）。
        baseline_bytes = 0
        for label, quality, subsampling in ENCODER_VARIANTS:
            payload = _encode_from_thumbnail(
                SAMPLE_THUMB, box_px, quality, subsampling)
            rendered = _decode(payload)
            if not baseline_bytes:
                baseline_bytes = len(payload)
            encoder_rows.append({
                "dpr": dpr, "box_px": box_px, "encoder": label,
                "bytes": len(payload),
                "byte_delta_vs_baseline": round(
                    100 * (len(payload) - baseline_bytes) / baseline_bytes, 1),
                "lapvar": round(lapvar(rendered), 1),
                "mse_vs_ideal": round(mse_vs_ideal(rendered, ideal), 2),
                "edge_overshoot": round(edge_overshoot(rendered), 2),
            })

    payload = {
        "note": "供给图来自真实 ensure_display_asset；理想 = 原图 LANCZOS 落到显示框",
        "box_css": CARD_THUMB_BOX_CSS,
        "browser_filter": (
            "BICUBIC 模拟 WebKit 下采样，偏乐观（DPR=1 旧链路真实 MSE=25.31 vs "
            "模拟 4.43，低估 5.7×）——旧链路数值只作量级对照，结论以真实 WebKit 为准"
        ),
        "chain": chain_rows,
        "encoder": encoder_rows,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / "measurements-real-chain.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    for dpr in DPRS:
        selected = [r for r in chain_rows if r["dpr"] == dpr]
        box = selected[0]["box_px"]
        print(f"\nDPR={dpr}  显示框 {box} 设备像素")
        print(f"  {'链路':<18}{'供给':>6}{'倍率':>7}{'字节':>9}"
              f"{'lapvar':>10}{'MSE↓理想':>11}{'过冲↓':>9}")
        for r in selected:
            print(f"  {r['chain']:<18}{r['served_px']:>6}{r['downscale_ratio']:>7.2f}"
                  f"{r['supply_bytes']:>9}{r['lapvar']:>10.1f}"
                  f"{r['mse_vs_ideal']:>11.2f}{r['edge_overshoot']:>9.2f}")

        enc = [r for r in encoder_rows if r["dpr"] == dpr]
        print(f"  编码器对照（供 {box}，基线 = q90 4:2:0）")
        print(f"    {'编码':<10}{'字节':>9}{'Δ字节':>9}{'lapvar':>10}"
              f"{'MSE↓理想':>11}{'过冲↓':>9}")
        for r in enc:
            delta = r["byte_delta_vs_baseline"]
            delta_text = "基线" if delta == 0 else f"{delta:+.1f}%"
            print(f"    {r['encoder']:<10}{r['bytes']:>9}{delta_text:>9}"
                  f"{r['lapvar']:>10.1f}{r['mse_vs_ideal']:>11.2f}"
                  f"{r['edge_overshoot']:>9.2f}")

    print(f"\n已写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
