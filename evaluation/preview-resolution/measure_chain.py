#!/usr/bin/env python3
"""端到端损失链：从 7008px 原图到屏上 215px 卡片，逐环节算同一块区域的清晰度。

所有数字都取自**同一块内容区域**（用最大图对齐后逐级裁切），因此可以直接
相减比较。只读，不写照片、不改仓库。

输出 measurements-chain.json
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sample import RESULTS, SAMPLE_SOURCE, self_check  # noqa: E402

# 显示盒：与 measure_webkit.mjs 在真实 WebKit 里量到的一致
BOX_CONTENT_H = 143


def lapvar(img: Image.Image) -> float:
    g = np.asarray(img.convert("L"), dtype=np.float32)
    c = g[1:-1, 1:-1]
    lap = -4 * c + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
    return float(lap.var())


def main() -> int:
    self_check()
    src = SAMPLE_SOURCE

    with Image.open(src) as s:
        s.load()
        full = s.convert("RGB")

    chain: list[dict] = []

    # LapVariance 是**尺度相关**的：同一张图放大或缩小都会改变数值，
    # 所以不能在各自的原生分辨率上直接比较。做法统一为——每个环节的产物
    # 都送进同一个 215px 显示盒（浏览器要做的最后一步），再测清晰度。
    # 这样差值才真正反映「该环节丢失了多少可显示的细节」。
    def to_box(img: Image.Image) -> Image.Image:
        out = img.copy()
        out.thumbnail((215, 215), Image.Resampling.LANCZOS)
        return out

    def add(stage: str, img: Image.Image, note: str = "") -> None:
        displayed = to_box(img)
        chain.append({
            "stage": stage,
            "native_size": list(img.size),
            "onscreen_size": list(displayed.size),
            "lapvar_onscreen": round(lapvar(displayed), 1),
            "note": note,
        })

    add("源文件（原始解码）", full, f"源 {full.size[0]}x{full.size[1]}")

    # ① 产品真实路径：draft 预缩 -> LANCZOS 到 512 -> JPEG q86 4:2:0
    d = Image.open(src)
    d.draft("RGB", (512, 512))
    d.load()
    d = d.convert("RGB")
    add("① draft() DCT 预缩后", d, f"draft 解到 {d.size[0]}x{d.size[1]}")

    d512 = d.copy()
    d512.thumbnail((512, 512), Image.Resampling.LANCZOS)
    add("② LANCZOS 收到 512 长边", d512, "产品写入磁盘前")

    buf = io.BytesIO()
    d512.save(buf, "JPEG", quality=86, optimize=True)
    buf.seek(0)
    with Image.open(buf) as rt:
        rt.load()
        enc = rt.convert("RGB")
    add("③ JPEG q86 4:2:0 落盘", enc, "磁盘上的缩略图 = 实际屏上所用")

    # 对照：只改编码参数，隔离「压缩/采样」这一环节的贡献
    for label, quality, sub in (("q95 4:4:4", 95, 0), ("q95 4:2:0", 95, 1)):
        b = io.BytesIO()
        d512.save(b, "JPEG", quality=quality, optimize=True, subsampling=sub)
        b.seek(0)
        with Image.open(b) as rt:
            rt.load()
            variant = rt.convert("RGB")
        add(f"③' 编码 {label}（其余不变）", variant, "隔离编码环节")

    # ④ 如果去掉 512 上限，直接从原图到显示盒
    add("④ 去掉 512 上限（原图直落显示盒）", full, "隔离「生成上限」环节")

    out = {
        "sample": src.name,
        "source_size": list(full.size),
        "box_css_px": [215, 161],
        "method": "每个环节的产物都缩进同一 215px 显示盒后再测 lapvar",
        "chain": chain,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "measurements-chain.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    ref = next(c for c in chain if c["stage"].startswith("源文件"))
    print(f"样本 {src.name}  源 {full.size[0]}x{full.size[1]}")
    print(f"{'环节（均在 215px 显示盒内测）':<40} {'原生尺寸':>12} {'屏上lapvar':>11} {'占上限':>8}")
    print("-" * 76)
    for step in chain:
        sz = f"{step['native_size'][0]}x{step['native_size'][1]}"
        pct = step["lapvar_onscreen"] / ref["lapvar_onscreen"] * 100
        print(f"{step['stage']:<40} {sz:>12} {step['lapvar_onscreen']:>11.1f} {pct:>7.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
