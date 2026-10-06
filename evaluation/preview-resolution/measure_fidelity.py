#!/usr/bin/env python3
"""对照各渲染结果与「理想下采样」的差距。

lapvar 会奖励走样（nearest/crisp-edges 的高频伪影也拉高数值），所以
单看 lapvar 会误判。这里同时给出：
  - lapvar           ：锐度代理指标
  - mse_vs_ideal     ：与「原图 LANCZOS 到目标尺寸」的均方误差，越低越接近理想
  - edge_overshoot   ：边缘过冲幅度，走样会显著抬高它

输出 measurements-fidelity.json
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


def lapvar(img: Image.Image) -> float:
    g = np.asarray(img.convert("L"), dtype=np.float32)
    c = g[1:-1, 1:-1]
    return float((-4 * c + g[:-2, 1:-1] + g[2:, 1:-1]
                  + g[1:-1, :-2] + g[1:-1, 2:]).var())


def edge_overshoot(img: Image.Image) -> float:
    """沿水平方向找强边缘，统计过冲（Gibbs）幅度。"""
    g = np.asarray(img.convert("L"), dtype=np.float32)
    grad = np.abs(np.diff(g, axis=1))
    thresh = np.percentile(grad, 99)
    if thresh <= 0:
        return 0.0
    ys, xs = np.where(grad >= thresh)
    overshoots = []
    for y, x in zip(ys, xs):
        if x < 3 or x > g.shape[1] - 4:
            continue
        left = g[y, x - 3:x].mean()
        right = g[y, x + 1:x + 4].mean()
        step = right - left
        if abs(step) < 12:
            continue
        peak = g[y, x] - left if step > 0 else left - g[y, x]
        overshoots.append(max(0.0, peak - abs(step)))
    return float(np.mean(overshoots)) if overshoots else 0.0


def main() -> int:
    self_check()
    if not RENDERS.is_dir():
        print("先跑 measure_webkit.mjs")
        return 1

    # 理想：直接从原图 LANCZOS 到目标尺寸
    with Image.open(SOURCE) as s:
        s.load()
        full = s.convert("RGB")
    ideal_css = full.copy()
    ideal_css.thumbnail((215, 215), Image.Resampling.LANCZOS)  # 215x143
    with Image.open(THUMB) as t:
        t.load()
        thumb = t.convert("RGB")

    def mse_vs(a: Image.Image, b: Image.Image) -> float:
        x = np.asarray(a.convert("RGB").resize(b.size,
                     Image.Resampling.LANCZOS), dtype=np.float32)
        y = np.asarray(b.convert("RGB"), dtype=np.float32)
        return float(((x - y) ** 2).mean())

    out: dict = {"ideal_source_to_box": list(ideal_css.size), "variants": {}}

    def record(name: str, img: Image.Image) -> None:
        out["variants"][name] = {
            "size": list(img.size),
            "lapvar": round(lapvar(img), 1),
            "mse_vs_ideal": round(mse_vs(img, ideal_css), 2),
            "edge_overshoot": round(edge_overshoot(img), 2),
        }

    # PIL 各算法（基准）
    for n, m in (("LANCZOS", Image.Resampling.LANCZOS),
                 ("BICUBIC", Image.Resampling.BICUBIC),
                 ("BILINEAR", Image.Resampling.BILINEAR),
                 ("BOX", Image.Resampling.BOX),
                 ("NEAREST", Image.Resampling.NEAREST)):
        record(f"PIL_{n}", thumb.resize(ideal_css.size, m))
    record("PIL_原图直落LANCZOS（理想）", ideal_css)

    # WebKit 渲染（无留边，215x143）
    for p in sorted(RENDERS.glob("tight-*.png")):
        with Image.open(p) as im:
            im.load()
            img = im.convert("RGB")
        if img.size != tuple(ideal_css.size):
            img = img.resize(ideal_css.size, Image.Resampling.LANCZOS)
        record(f"WebKit_{p.stem}", img)

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "measurements-fidelity.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'变体':<44}{'lapvar':>9}{'MSE↓理想':>10}{'边缘过冲↓':>10}")
    print("-" * 74)
    for k, v in out["variants"].items():
        print(f"{k:<44}{v['lapvar']:>9.1f}{v['mse_vs_ideal']:>10.2f}"
              f"{v['edge_overshoot']:>10.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
