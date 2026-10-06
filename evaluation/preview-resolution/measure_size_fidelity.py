#!/usr/bin/env python3
"""尺寸扫描的真实保真度：源尺寸该多大，屏上才最接近理想？

lapvar 会奖励走样（下采样过头会产生高频伪影，数值反而更高），所以
「源越大屏上越锐」这个结论必须用 MSE 复核——与「理想下采样」逐像素
比较，距离最小的那个尺寸才是真的最好。

理想 = 原图 LANCZOS 直接缩到显示尺寸（相当于无限算力的最优解）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sample import RESULTS, SAMPLE_SOURCE, SAMPLE_THUMB, self_check  # noqa: E402

# 渲染图来自 measure_webkit.mjs，其源是 SAMPLE_SOURCE —— 比对基准必须同源，
# 否则就是「拿两张不同的照片在比」，MSE 会大得离谱却不报错。
THUMB = SAMPLE_THUMB
SOURCE = SAMPLE_SOURCE
RENDERS = RESULTS / "renders"
SIZES = (128, 213, 256, 341, 426, 512, 1024, 2048)


def lapvar(img: Image.Image) -> float:
    g = np.asarray(img.convert("L"), dtype=np.float32)
    c = g[1:-1, 1:-1]
    return float((-4 * c + g[:-2, 1:-1] + g[2:, 1:-1]
                  + g[1:-1, :-2] + g[1:-1, 2:]).var())


def content_box(img: Image.Image) -> Image.Image:
    """裁掉 object-fit:contain 留下的底色留边。

    不裁的话，比较的是「图片 + 大片纯色背景」，MSE 会被背景主导，
    任何尺寸看起来都一样差。
    """
    a = np.asarray(img.convert("L"))
    bg = float(np.median(a[0, :]))
    cols = np.where(np.abs(a.astype(np.float32) - bg).max(axis=0) > 10)[0]
    rows = np.where(np.abs(a.astype(np.float32) - bg).max(axis=1) > 10)[0]
    if len(cols) < 8 or len(rows) < 8:
        return img
    return img.crop((cols[0], rows[0], cols[-1] + 1, rows[-1] + 1))


def main() -> int:
    self_check()
    print(f"样本原图 {SOURCE.name}  缩略图 {THUMB.name}")
    with Image.open(SOURCE) as s:
        s.load()
        full = s.convert("RGB")

    rows: list[dict] = []
    ideal_cache: dict[str, Image.Image] = {}

    for dpr in (1, 2):
        for size in SIZES:
            p = RENDERS / f"sz{size}@dpr{dpr}.png"
            if not p.is_file():
                continue
            with Image.open(p) as im:
                im.load()
                render = content_box(im.convert("RGB"))
            # 理想：原图直接落到渲染内容尺寸
            key = f"{render.size[0]}x{render.size[1]}"
            if key not in ideal_cache:
                ideal_cache[key] = full.resize(
                    render.size, Image.Resampling.LANCZOS)
            ideal = ideal_cache[key]
            mse = float(((np.asarray(render, dtype=np.float32)
                          - np.asarray(ideal, dtype=np.float32)) ** 2).mean())
            rows.append({
                "dpr": dpr, "source_px": size,
                "content_size": list(render.size),
                "lapvar": round(lapvar(render), 1),
                "mse_vs_ideal": round(mse, 2),
            })

    out = {"ideal": "原图 LANCZOS 直接缩到渲染内容尺寸（已裁去留边）",
           "rows": rows}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "measurements-size-fidelity.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    for dpr in (1, 2):
        sel = [r for r in rows if r["dpr"] == dpr]
        if not sel:
            continue
        print(f"\nDPR={dpr}  渲染内容尺寸 {sel[0]['content_size'][0]}x"
              f"{sel[0]['content_size'][1]}")
        print(f"  {'源尺寸':>8}{'lapvar':>10}{'MSE↓理想':>12}")
        best = min(sel, key=lambda r: r["mse_vs_ideal"])
        for r in sel:
            mark = "  ← 最接近理想" if r is best else ""
            print(f"  {r['source_px']:>8}{r['lapvar']:>10.1f}"
                  f"{r['mse_vs_ideal']:>12.2f}{mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
