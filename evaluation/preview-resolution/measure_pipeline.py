#!/usr/bin/env python3
"""预览分辨率排查：跑一遍上游/本机图片管线，逐环节量化。

只读。不写任何照片、不改数据库、不碰工作区源码。
输出 measurements-pipeline.json，供 README 引用。

用法（本机 python 环境，含 PIL + numpy）：
    /Users/inori95/.workbuddy/binaries/python/envs/default/bin/python \
        evaluation/preview-resolution/measure_pipeline.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

HOME = Path.home()
CULLUMI = HOME / "Library/Application Support/Cullumi"
PROJECT_ID = "7d97e62d523c8ec6"
PROJECT = CULLUMI / "projects" / PROJECT_ID
DB = PROJECT / "project.db"
THUMBS = PROJECT / "thumbs"

# 屏上实测的卡片图片框（CSS px）。由 WebKit 复现同一段卡片 CSS 得到，
# 见 measure_webkit.js。列宽 = repeat(auto-fill, minmax(210px,1fr)) 在
# 1212px 视口下的结果，非整数是关键。
DISPLAY_BOX = (213.19, 159.89)

# 结果目录沿用上游约定：evaluation/performance-results 已被 .gitignore 忽略
RESULTS = Path(__file__).resolve().parents[1] / "performance-results/preview-resolution"


def lapvar(img: Image.Image) -> float:
    """Laplacian 方差——常用清晰度指标，越高越锐。"""
    g = np.asarray(img.convert("L"), dtype=np.float32)
    c = g[1:-1, 1:-1]
    lap = -4 * c + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
    return float(lap.var())


def mse(a: Image.Image, b: Image.Image) -> float:
    x = np.asarray(a.convert("RGB"), dtype=np.float32)
    y = np.asarray(b.convert("RGB"), dtype=np.float32)
    return float(((x - y) ** 2).mean())


def jpeg_sampling(path: Path) -> dict:
    """从 SOF 标记解析 JPEG 色度采样因子（4:2:0 / 4:4:4 …）。"""
    data = path.read_bytes()
    i = 2
    while i < len(data) - 4:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        # SOF0..SOF3, SOF5..SOF7, SOF9..SOF11, SOF13..SOF15（排除 DHT/DAC 等）
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            seg = data[i + 4:i + 4 + 6]
            if len(seg) < 6:
                return {"error": "truncated SOF"}
            comps = seg[5]
            factors = data[i + 4 + 6:i + 4 + 6 + comps * 3]
            h, v = factors[1] >> 4, factors[1] & 0x0F
            names = {(1, 1): "4:4:4", (2, 1): "4:2:2", (2, 2): "4:2:0",
                     (1, 2): "4:4:0", (4, 1): "4:1:1", (4, 2): "4:1:0"}
            return {"sof": f"0x{marker:02X}", "sampling": names.get((h, v), f"{h}x{v}")}
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = int.from_bytes(data[i + 2:i + 4], "big")
        i += 2 + length
    return {"error": "no SOF"}


def main() -> int:
    out: dict = {}

    # ---- 环节 1：源文件本身的分辨率 -------------------------------------
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT id, relative_path, width, height, status, thumbnail FROM photos")]
    con.close()

    sizes = Counter(f"{r['width']}x{r['height']}" for r in rows)
    exts = Counter(Path(r["relative_path"]).suffix.lower() for r in rows)
    out["source"] = {
        "photos": len(rows),
        "extensions": dict(exts),
        "distinct_sizes": dict(sizes),
        "min_megapixels": round(min(r["width"] * r["height"] for r in rows) / 1e6, 3),
        "max_megapixels": round(max(r["width"] * r["height"] for r in rows) / 1e6, 3),
        "status": dict(Counter(r["status"] for r in rows)),
    }

    # ---- 环节 2：磁盘上的缩略图（生成与缩放逻辑的产物） -----------------
    thumb_sizes = Counter()
    thumb_files = []
    for p in THUMBS.glob("*.jpg"):
        with Image.open(p) as im:
            thumb_sizes[f"{im.size[0]}x{im.size[1]}"] += 1
            thumb_files.append(p)
    out["thumbnail"] = {
        "count": len(thumb_files),
        "distinct_sizes": dict(thumb_sizes),
        "max_long_edge": max(
            max(int(k.split("x")[0]), int(k.split("x")[1])) for k in thumb_sizes
        ),
    }

    # ---- 显示预览缓存是否存在（HEIC/RAW/TIFF 专用路径） -----------------
    out["display_preview_cache"] = {
        "files": len(list(PROJECT.rglob("display-*"))),
        "extensions_served_verbatim_by_api_photo": [".jpg", ".jpeg"],
    }

    # ---- 环节 3：压缩/采样 —— 取一张代表图做对照 ------------------------
    # 挑一张状态为 active 的、缩略图存在的照片
    sample = next(
        (r for r in rows if r["status"] == "active"
         and (THUMBS / Path(r["thumbnail"]).name).is_file()),
        None,
    )
    if sample is None:
        print("找不到可用的样本照片", file=sys.stderr)
        return 1

    src = Path(HOME / "Pictures/未命名文件夹") / sample["relative_path"]
    if not src.is_file():
        # rel_path 可能已含目录前缀，兜底在项目根下找
        src = Path(CULLUMI / sample["relative_path"])
    on_disk = THUMBS / Path(sample["thumbnail"]).name

    out["sample"] = {
        "relative_path": sample["relative_path"],
        "source_file": str(src),
        "source_exists": src.is_file(),
        "source_size": None,
        "on_disk_thumb": str(on_disk),
    }

    with Image.open(on_disk) as t:
        t.load()
        thumb = t.convert("RGB")
    out["sample"]["on_disk_thumb_size"] = list(thumb.size)
    out["sample"]["on_disk_thumb_bytes"] = on_disk.stat().st_size
    out["sample"]["on_disk_jpeg"] = jpeg_sampling(on_disk)
    out["sample"]["on_disk_lapvar"] = round(lapvar(thumb), 1)

    if src.is_file():
        with Image.open(src) as s:
            s.load()
            full = s.convert("RGB")
        out["sample"]["source_size"] = list(full.size)
        out["sample"]["source_bytes"] = src.stat().st_size
        out["sample"]["source_jpeg"] = jpeg_sampling(src)

        # 理想 512：全解码后 LANCZOS 缩到 512（不做 draft 预缩）
        ideal512 = full.copy()
        ideal512.thumbnail((512, 512), Image.Resampling.LANCZOS)
        out["sample"]["ideal512_lapvar"] = round(lapvar(ideal512), 1)
        out["sample"]["on_disk_vs_ideal512_ratio"] = round(
            lapvar(thumb) / lapvar(ideal512), 3)
        out["sample"]["on_disk_vs_ideal512_mse"] = round(mse(thumb, ideal512), 3)

        # 走产品真实路径：draft((512,512)) 再 thumbnail
        draft_src = Image.open(src)
        draft_src.draft("RGB", (512, 512))
        draft_src.load()
        draft512 = draft_src.convert("RGB")
        out["sample"]["draft_decode_size"] = list(draft512.size)
        draft512.thumbnail((512, 512), Image.Resampling.LANCZOS)
        out["sample"]["draft_path_vs_ideal512_mse"] = round(mse(draft512, ideal512), 3)
        out["sample"]["draft_path_vs_ondisk_mse"] = round(mse(draft512, thumb), 3)

        # 编码损失：同一张 512 图，q86 4:2:0（产品设置）vs q95 4:4:4
        import io
        for label, quality, subsampling in (
            ("q86_420", 86, 2),
            ("q95_444", 95, 0),
            ("q95_420", 95, 1),
        ):
            buf = io.BytesIO()
            ideal512.save(buf, "JPEG", quality=quality, optimize=True,
                          subsampling=subsampling)
            buf.seek(0)
            with Image.open(buf) as rt:
                rt.load()
                rt_img = rt.convert("RGB")
            out["sample"][f"encode_{label}_mse_vs_ideal512"] = round(
                mse(rt_img, ideal512), 3)
            out["sample"][f"encode_{label}_lapvar"] = round(lapvar(rt_img), 1)

        # ---- 环节 4（PIL 侧）：把同一张 512 图缩到屏上卡片框，比各种重采样
        box = (round(DISPLAY_BOX[0]), round(DISPLAY_BOX[1]))
        out["resample_reference"] = {"box": list(box), "note": "PIL 侧对照，非浏览器"}
        for name, method in (
            ("LANCZOS", Image.Resampling.LANCZOS),
            ("BICUBIC", Image.Resampling.BICUBIC),
            ("BILINEAR", Image.Resampling.BILINEAR),
            ("BOX", Image.Resampling.BOX),
            ("NEAREST", Image.Resampling.NEAREST),
            ("HAMMING", Image.Resampling.HAMMING),
        ):
            out["resample_reference"][f"512_to_box_{name}"] = round(
                lapvar(thumb.resize(box, method)), 1)
            out["resample_reference"][f"original_to_box_{name}"] = round(
                lapvar(full.resize(box, method)), 1)

        # ---- 尺寸扫描：源越大是否屏上越锐？（PIL 侧预演） ----------------
        sweep = {}
        for target in (128, 213, 256, 341, 426, 512, 1024):
            s = full.copy()
            s.thumbnail((target, target), Image.Resampling.LANCZOS)
            sweep[str(target)] = {
                "source_px": list(s.size),
                "onscreen_lapvar_lanczos": round(
                    lapvar(s.resize(box, Image.Resampling.LANCZOS)), 1),
            }
        out["source_size_sweep_pil"] = sweep
        full.close()

    thumb.close()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "measurements-pipeline.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
