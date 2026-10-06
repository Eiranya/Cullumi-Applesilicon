"""从 512px 分析缩略图派生「卡片显示资产」。

背景（preview-sharpness）：照片库卡片图片框实测只有 213×142 设备像素（DPR=1），
而磁盘上发布的缩略图长边恒为 512px。浏览器被迫下采样约 2.38 倍并弃用它较差的
重采样滤波器，屏上锐度因此丢掉约 18%。让服务端按「图片框 × DPR」的精确尺寸供图
后倍率恒为 1.00，浏览器不再重采样，锐度回到照片本身应有的水平。

红线：本模块**只读** 512px 分析缩略图。那张图同时是 NIQE / sharpness 指标的输入，
参与 ``analysis_version()`` 缓存指纹（``rgb512-v1`` / ``raw-preview512-v2``）与
``NIQE_VERSION`` 黄金基线，任何路径都不得写入或重写它。派生出的 ``.card-<w>-<fp>.jpg``
是与它同目录、但完全独立的第二套资产（可安全清理）。
"""

from __future__ import annotations

import hashlib
import threading
import uuid
from pathlib import Path

from PIL import Image

# 显示资产最大只有 1024px、典型 213/426px，文件本来就很小：色度抽样省下的字节
# 微乎其微，画质代价却很明显。故用 q95 + 4:4:4（无色度抽样）。
# 与 512 分析缩略图（q86 4:2:0）是两套独立资产，那条线（红线）不在此处。
ASSET_JPEG_QUALITY = 95
# Pillow 的 subsampling=0 即 4:4:4；在 213px 尺度上，4:2:0 会把色度分辨率降到
# 106.5px，实测叠加大约 14.6 的 MSE（vs 理想），4:4:4 可把这部分收敛到约一半。
ASSET_JPEG_SUBSAMPLING = 0
# 参数下界：防滥用 / 异常输入（过小无意义）。
MIN_ASSET_WIDTH = 32
# 参数上界 = 512 缩略图的 2 倍，足以覆盖 DPR<=2；再大也不会更清晰（源就那么大）。
MAX_ASSET_WIDTH = 1024
# 编码器版本标记：并入缓存指纹。否则改编码参数（质量 / 抽样）不会让旧资产失效，
# 浏览器会继续读用旧编码器生成的字节（上一轮的 q90 4:2:0 就是这样）。加上它之后，
# 编码参数一变 → 指纹变 → 旧指纹文件由 _prune_stale_assets 在首次重生成时回收。
ASSET_FORMAT_TAG = "q95-s444"

# ThreadingHTTPServer 是多线程的：按 (缩略图, 宽度) 取模分片，避免同一资产被并发
# 重复生成或读到半成品。分片而非单锁，是为了让不同卡片之间不互相阻塞。
_ASSET_LOCKS = [threading.Lock() for _ in range(64)]


def display_asset_path(thumbnail: Path, width: int) -> Path:
    """返回卡片显示资产的缓存路径。

    缓存键 = 512 缩略图的 ``size:mtime_ns`` + 目标宽度 + 编码器版本标记：
    源文件变化 → 扫描重分析 → 512 缩略图 ``mtime`` 变 → 指纹变 → 自动失效；
    编码参数变化 → ``ASSET_FORMAT_TAG`` 变 → 同样失效。无需显式清理逻辑。
    """
    stat = thumbnail.stat()
    fingerprint = hashlib.sha1(
        f"{stat.st_size}:{stat.st_mtime_ns}:{width}:{ASSET_FORMAT_TAG}".encode("ascii")
    ).hexdigest()[:12]
    return thumbnail.with_name(f"{thumbnail.stem}.card-{width}-{fingerprint}.jpg")


def ensure_display_asset(thumbnail: Path, width: int) -> Path:
    """返回（必要时生成）宽度精确等于 ``width`` 的显示资产；并发安全。

    :raises ValueError: ``width`` 不在 ``[MIN_ASSET_WIDTH, MAX_ASSET_WIDTH]`` 内。
    :raises OSError: 512 缩略图不可读或无法写入缓存目录。
    """
    if not (MIN_ASSET_WIDTH <= width <= MAX_ASSET_WIDTH):
        raise ValueError("卡片资产宽度超出允许范围")
    lock = _ASSET_LOCKS[hash((str(thumbnail), width)) % len(_ASSET_LOCKS)]
    with lock:
        return _build_display_asset(thumbnail, width)


def _build_display_asset(thumbnail: Path, width: int) -> Path:
    target = display_asset_path(thumbnail, width)
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        # 只读打开 512 分析缩略图并派生，绝不回写源。
        with Image.open(thumbnail) as source:
            source.load()
            height = max(1, round(source.height * width / source.width))
            resized = source.convert("RGB").resize(
                (width, height), Image.Resampling.LANCZOS
            )
            try:
                resized.save(
                    temporary, "JPEG",
                    quality=ASSET_JPEG_QUALITY,
                    subsampling=ASSET_JPEG_SUBSAMPLING,
                    optimize=True,
                )
            finally:
                resized.close()
        temporary.replace(target)          # 原子落盘，避免半成品被读到
    finally:
        temporary.unlink(missing_ok=True)
    # 传入 target.name 而不是让 prune 自己现算：若 512 缩略图在「算出 target」与
    # 「prune」之间发生变化（指纹变），现算出的 keep 会与 target 不同，于是把刚生成
    # 的 target 当旧文件删掉，`return target` 便返回一个不存在的路径。
    _prune_stale_assets(thumbnail, width, target.name)
    return target


def _prune_stale_assets(thumbnail: Path, width: int, keep_name: str) -> None:
    """删除同一 512 缩略图、同一宽度下的旧指纹文件（保留 ``keep_name``），避免磁盘泄漏。"""
    for stale in thumbnail.parent.glob(f"{thumbnail.stem}.card-{width}-*.jpg"):
        if stale.name != keep_name:
            try:
                stale.unlink(missing_ok=True)
            except OSError:
                pass
