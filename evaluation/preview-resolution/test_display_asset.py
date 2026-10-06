#!/usr/bin/env python3
"""``cullumi.display_asset`` 的单元验证（preview-sharpness）。

覆盖：
  1. 派生资产宽度精确等于请求的 ``width``，且保持宽高比；
  2. 缓存命中时不重复生成（mtime 不变）；
  3. 源 512 缩略图变化后旧指纹失效、新文件生成、旧文件被回收；
  4. 派生过程只读源文件 —— 512 缩略图逐字节不变（红线）；
  5. 越界宽度被拒绝；
  6. 显示资产以 4:4:4（无色度抽样）保存 —— 213px 尺度上色度抽样明显损失保真度。

为何放在 evaluation/ 而不是 tests/：``tests/`` 与 ``models/`` 是冻结目录，
``verify-change-set.sh`` 硬底线 1 不区分 "Only in" 与 "differ"，在 tests/ 下新增
文件会被判为「冻结目录未授权差异」而直接 EXIT!=0，登记 ``EXPECTED_ADDED`` 也补救
不了。因此本测试落在已被白名单覆盖的 ``evaluation/preview-resolution/`` 下。
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, JpegImagePlugin

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from cullumi.display_asset import (  # noqa: E402
    MAX_ASSET_WIDTH,
    MIN_ASSET_WIDTH,
    ensure_display_asset,
)

THUMBNAIL_SIZE = (512, 384)
REQUEST_WIDTH = 213


def _write_thumbnail(
    path: Path,
    size: tuple[int, int] = THUMBNAIL_SIZE,
    color: tuple[int, int, int] = (120, 90, 60),
) -> None:
    """写一张和真实 512 分析缩略图同构的 JPEG（RGB、长边 512）。"""
    with Image.new("RGB", size, color) as image:
        image.save(path, "JPEG", quality=86, optimize=True)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DisplayAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="cullumi-card-asset-")
        self.addCleanup(self._temp.cleanup)
        self.thumbnail = Path(self._temp.name) / "abcd1234.jpg"
        _write_thumbnail(self.thumbnail)

    def test_asset_width_matches_request(self) -> None:
        for width in (MIN_ASSET_WIDTH, REQUEST_WIDTH, 426, MAX_ASSET_WIDTH):
            with self.subTest(width=width):
                asset = ensure_display_asset(self.thumbnail, width)
                with Image.open(asset) as image:
                    self.assertEqual(image.width, width)

    def test_asset_keeps_aspect_ratio(self) -> None:
        asset = ensure_display_asset(self.thumbnail, REQUEST_WIDTH)
        with Image.open(asset) as image, Image.open(self.thumbnail) as source:
            expected = max(1, round(source.height * REQUEST_WIDTH / source.width))
            self.assertEqual(image.height, expected)

    def test_asset_has_no_chroma_subsampling(self) -> None:
        # 4:4:4（get_sampling()==0）：213px 尺度上色度抽样会明显消耗保真度。
        asset = ensure_display_asset(self.thumbnail, REQUEST_WIDTH)
        with Image.open(asset) as image:
            self.assertEqual(JpegImagePlugin.get_sampling(image), 0)

    def test_cache_hit_reuses_existing_file(self) -> None:
        first = ensure_display_asset(self.thumbnail, REQUEST_WIDTH)
        first_mtime = first.stat().st_mtime_ns
        second = ensure_display_asset(self.thumbnail, REQUEST_WIDTH)
        self.assertEqual(first, second)
        self.assertEqual(second.stat().st_mtime_ns, first_mtime)

    def test_source_change_invalidates_cache(self) -> None:
        first = ensure_display_asset(self.thumbnail, REQUEST_WIDTH)
        _write_thumbnail(self.thumbnail, color=(10, 200, 30))
        second = ensure_display_asset(self.thumbnail, REQUEST_WIDTH)
        self.assertNotEqual(first, second)
        self.assertTrue(second.is_file())

    def test_stale_fingerprints_are_pruned(self) -> None:
        first = ensure_display_asset(self.thumbnail, REQUEST_WIDTH)
        self.assertTrue(first.is_file())
        _write_thumbnail(self.thumbnail, color=(10, 200, 30))
        second = ensure_display_asset(self.thumbnail, REQUEST_WIDTH)
        self.assertNotEqual(first, second)
        self.assertFalse(first.exists(), "旧指纹资产应被回收，避免磁盘泄漏")
        self.assertTrue(second.is_file())

    def test_derivation_does_not_touch_source_thumbnail(self) -> None:
        before = _sha256(self.thumbnail)
        ensure_display_asset(self.thumbnail, REQUEST_WIDTH)
        ensure_display_asset(self.thumbnail, 426)
        self.assertEqual(
            _sha256(self.thumbnail), before,
            "512 分析缩略图必须逐字节不变（NIQE / sharpness 红线）",
        )

    def test_out_of_range_width_is_rejected(self) -> None:
        for width in (MIN_ASSET_WIDTH - 1, MAX_ASSET_WIDTH + 1, 0, -40):
            with self.subTest(width=width):
                with self.assertRaises(ValueError):
                    ensure_display_asset(self.thumbnail, width)

    def test_distinct_widths_coexist(self) -> None:
        narrow = ensure_display_asset(self.thumbnail, REQUEST_WIDTH)
        wide = ensure_display_asset(self.thumbnail, 426)
        self.assertNotEqual(narrow, wide)
        self.assertTrue(narrow.is_file())
        self.assertTrue(wide.is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
