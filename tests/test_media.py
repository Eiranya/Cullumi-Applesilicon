from __future__ import annotations

import io
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

from cullumi import media, motion
from cullumi.media import (
    DISPLAY_PREVIEW_EXTENSIONS,
    DISPLAY_PREVIEW_MAX_SIZE,
    analyze_photo,
    ensure_display_preview,
    open_image,
)
from cullumi.motion import (
    MotionAsset,
    embedded_motion_asset,
    ensure_motion_video,
    extract_motion_frame,
    locate_motion_still_time,
    paired_motion_asset,
    probe_motion,
    write_motion_cover_source,
)


class MediaPreviewTests(unittest.TestCase):
    def test_paired_jpeg_cover_writeback_replaces_pixels_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            photo = root / "IMG_0001.JPG"
            frame = root / "frame.jpg"
            video = root / "IMG_0001.MOV"
            with Image.new("RGB", (80, 60), "navy") as image:
                image.save(photo, "JPEG")
            original = photo.read_bytes()
            with Image.new("RGB", (40, 30), "tomato") as image:
                image.save(frame, "JPEG")
            video.write_bytes(b"paired-video")

            result = write_motion_cover_source(
                photo,
                frame,
                MotionAsset("apple_sidecar", video),
                400,
                root / "backups",
                1,
            )

            with Image.open(photo) as image:
                self.assertEqual(image.size, (40, 30))
            self.assertEqual(result["backup"].read_bytes(), original)
            self.assertEqual(result["asset"].path, video)

    def test_embedded_motion_photo_writeback_keeps_video_and_updates_timestamp(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            photo = root / "motion.jpg"
            frame = root / "frame.jpg"
            with Image.new("RGB", (80, 60), "navy") as image:
                image.save(photo, "JPEG")
            base = photo.read_bytes()
            video = b"video-payload"
            xmp = (
                b'<rdf:Description GCamera:MotionPhoto="1" '
                b'GCamera:MicroVideoOffset="13" '
                b'GCamera:MotionPhotoPresentationTimestampUs="0"/>'
            )
            app1 = b"\xff\xe1" + (len(xmp) + 2).to_bytes(2, "big") + xmp
            photo.write_bytes(base[:2] + app1 + base[2:] + video)
            original = photo.read_bytes()
            asset = embedded_motion_asset(photo)
            self.assertIsNotNone(asset)
            with Image.new("RGB", (40, 30), "tomato") as image:
                image.save(frame, "JPEG")

            result = write_motion_cover_source(
                photo, frame, asset, 375, root / "backups", 2
            )

            updated = embedded_motion_asset(photo)
            self.assertIsNotNone(updated)
            self.assertEqual(updated.presentation_timestamp_us, 375000)
            self.assertEqual(photo.read_bytes()[updated.offset :], video)
            self.assertEqual(result["backup"].read_bytes(), original)
            with Image.open(photo) as image:
                self.assertEqual(image.size, (40, 30))

    def test_paired_heic_cover_writeback_preserves_live_photo_container(self):
        with tempfile.TemporaryDirectory() as temporary:
            from pillow_heif import from_pillow

            root = Path(temporary)
            photo = root / "IMG_0002.HEIC"
            frame = root / "frame.jpg"
            video = root / "IMG_0002.MOV"
            with Image.new("RGB", (80, 60), "navy") as image:
                from_pillow(image).save(photo, quality=90)
            original = photo.read_bytes()
            with Image.new("RGB", (40, 30), "tomato") as image:
                image.save(frame, "JPEG")
            video.write_bytes(b"paired-video")

            result = write_motion_cover_source(
                photo,
                frame,
                MotionAsset("apple_sidecar", video),
                500,
                root / "backups",
                1,
            )

            image, _ = open_image(photo)
            try:
                self.assertEqual(image.size, (40, 30))
            finally:
                image.close()
            self.assertEqual(result["backup"].read_bytes(), original)

    def test_standard_android_motion_photo_xmp_locates_appended_video(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "motion.jpg"
            video = b"video-payload"
            xmp = (
                b'<rdf:Description GCamera:MotionPhoto="1" '
                b'GCamera:MicroVideoOffset="13" '
                b'GCamera:MotionPhotoPresentationTimestampUs="240000"/>'
            )
            with Image.new("RGB", (80, 60), "navy") as image:
                image.save(path, "JPEG")
            base = path.read_bytes()
            app1 = b"\xff\xe1" + (len(xmp) + 2).to_bytes(2, "big") + xmp
            path.write_bytes(base[:2] + app1 + base[2:] + video)

            asset = embedded_motion_asset(path)

            self.assertIsNotNone(asset)
            self.assertEqual(asset.kind, "android_embedded")
            self.assertEqual(asset.length, len(video))
            self.assertEqual(path.read_bytes()[asset.offset :], video)
            self.assertEqual(asset.presentation_timestamp_us, 240000)

    def test_ordinary_jpeg_with_mp4_bytes_is_not_guessed_as_motion_photo(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ordinary.jpg"
            path.write_bytes(b"jpeg-data-ftyp-isom-video")

            self.assertIsNone(embedded_motion_asset(path))

    def test_live_photo_sidecar_pairs_by_folder_and_stem(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            photo = root / "IMG_0001.HEIC"
            video = root / "IMG_0001.MOV"
            photo.touch()
            video.touch()

            asset = paired_motion_asset(
                photo, {(root.resolve(), "img_0001"): video}
            )

            self.assertIsNotNone(asset)
            self.assertEqual(asset.kind, "apple_sidecar")
            self.assertEqual(asset.path, video)

    def test_motion_video_can_be_probed_transcoded_and_sampled(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sample.mov"
            from cullumi.motion import ffmpeg_executable

            subprocess.run(
                [
                    ffmpeg_executable(), "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10",
                    "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000",
                    "-t", "0.5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest",
                    str(source),
                ],
                check=True,
            )
            asset = MotionAsset("apple_sidecar", source)

            metadata = probe_motion(asset)
            video = ensure_motion_video(asset, root / "cache")
            frame = extract_motion_frame(video, 200, root / "frame.jpg")

            self.assertEqual(metadata["motion_width"], 160)
            self.assertEqual(metadata["motion_height"], 120)
            self.assertGreater(metadata["motion_frame_count"], 1)
            self.assertTrue(video.is_file())
            cached_probe = subprocess.run(
                [ffmpeg_executable(), "-hide_banner", "-i", str(video)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertIn("Audio: opus", cached_probe.stderr)
            with Image.open(frame) as image:
                self.assertEqual(image.size, (160, 120))

    def test_motion_still_frame_is_located_by_visual_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sample.mov"
            still = root / "sample.jpg"
            from cullumi.motion import ffmpeg_executable

            subprocess.run(
                [
                    ffmpeg_executable(), "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10",
                    "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(source),
                ],
                check=True,
            )
            subprocess.run(
                [
                    ffmpeg_executable(), "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", "0.4", "-i", str(source), "-frames:v", "1", str(still),
                ],
                check=True,
            )
            asset = MotionAsset("apple_sidecar", source)

            time_ms = locate_motion_still_time(still, asset, 1000, 10)

            self.assertLessEqual(abs(time_ms - 400), 100)

    def test_android_presentation_timestamp_is_used_without_frame_matching(self):
        asset = MotionAsset(
            "android_embedded", Path("unused.jpg"), presentation_timestamp_us=750000
        )

        time_ms = locate_motion_still_time(Path("unused.jpg"), asset, 1000, 10)

        self.assertEqual(time_ms, 750)

    def test_analysis_reuses_the_decoded_image_instead_of_copying_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "source.jpg"
            source_path.write_bytes(b"placeholder")
            decoded = Image.new("RGB", (1600, 900), "teal")
            decoded.copy = mock.Mock(side_effect=AssertionError("full-size copy"))

            with mock.patch("cullumi.media.open_image", return_value=(decoded, "")):
                result = analyze_photo(source_path, root / "thumb.jpg")

            self.assertEqual(result["error"], "")
            decoded.copy.assert_not_called()
            self.assertTrue((root / "thumb.jpg").is_file())

    def test_jpeg_scaled_decode_preserves_original_dimensions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "large.jpg"
            thumbnail = root / "thumb.jpg"
            with Image.new("RGB", (4000, 3000), "navy") as image:
                image.save(source, "JPEG", quality=88)

            result = analyze_photo(source, thumbnail)

            self.assertEqual(result["error"], "")
            self.assertEqual((result["width"], result["height"]), (4000, 3000))
            with Image.open(thumbnail) as image:
                self.assertLessEqual(max(image.size), 512)

    def test_jpeg_scaled_decode_preserves_oriented_dimensions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "portrait.jpg"
            exif = Image.Exif()
            exif[274] = 6
            with Image.new("RGB", (1200, 800), "navy") as image:
                image.save(source, "JPEG", quality=88, exif=exif)

            result = analyze_photo(source, root / "thumb.jpg")

            self.assertEqual(result["error"], "")
            self.assertEqual((result["width"], result["height"]), (800, 1200))

    def test_special_formats_include_tiff_raw_and_heif(self):
        self.assertIn(".tiff", DISPLAY_PREVIEW_EXTENSIONS)
        self.assertIn(".dng", DISPLAY_PREVIEW_EXTENSIONS)
        self.assertIn(".heic", DISPLAY_PREVIEW_EXTENSIONS)

    def test_display_preview_is_high_resolution_cached_and_refreshed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "wide.tiff"
            thumbnail = root / "thumbs" / "wide.jpg"
            with Image.new("RGB", (3000, 120), "navy") as image:
                image.save(source, "TIFF")

            first = ensure_display_preview(source, thumbnail)
            self.assertTrue(first.is_file())
            with Image.open(first) as preview:
                self.assertEqual(preview.format, "JPEG")
                self.assertEqual(max(preview.size), max(DISPLAY_PREVIEW_MAX_SIZE))

            with mock.patch(
                "cullumi.media.open_image",
                side_effect=AssertionError("cached preview should be reused"),
            ):
                self.assertEqual(ensure_display_preview(source, thumbnail), first)

            with Image.new("RGB", (2800, 140), "maroon") as image:
                image.save(source, "TIFF")
            with mock.patch.object(Path, "iterdir", side_effect=AssertionError("enumerated library")):
                second = ensure_display_preview(source, thumbnail)

            self.assertNotEqual(second, first)
            self.assertTrue(second.is_file())
            self.assertFalse(first.exists())

    def test_xmp_reader_stops_before_compressed_pixels(self):
        xmp = b'<rdf:Description GCamera:MotionPhoto="1"/>'
        stream = io.BytesIO(b'\xff\xd8\xff\xe1' + (len(xmp) + 2).to_bytes(2, "big") + xmp
                            + b'\xff\xda' + b'x' * (3 * 1024 * 1024))
        with mock.patch.object(Path, "open", return_value=stream):
            value = motion._xmp_prefix(Path("sample.jpg"))
            self.assertIn("MotionPhoto", value)
        # The same marker in compressed pixels must not identify a motion photo.
        with mock.patch.object(Path, "open", return_value=io.BytesIO(b'\xff\xd8\xff\xda' + xmp)):
            self.assertEqual(motion._xmp_prefix(Path("sample.jpg")), "")


    def test_raw_embedded_decode_preserves_size_and_full_preview(self):
        with Image.new("RGB", (4096, 3072), "teal") as image, io.BytesIO() as buffer:
            image.save(buffer, "JPEG")
            data = buffer.getvalue()
        raw = mock.MagicMock()
        raw.__enter__.return_value = raw
        raw.extract_thumb.return_value = mock.Mock(format=media.rawpy.ThumbFormat.JPEG, data=data)
        with mock.patch.object(media.rawpy, "imread", return_value=raw):
            with media.open_image(Path("photo.raf"), (512, 512))[0] as image:
                self.assertLessEqual(image.width, 1024)
                self.assertEqual(image.info["cullumi_original_size"], (4096, 3072))
            with media.open_image(Path("photo.raf"))[0] as image:
                self.assertEqual(image.size, (4096, 3072))
            raw.extract_thumb.side_effect = RuntimeError("no embedded preview")
            raw.postprocess.return_value = np.zeros((80, 100, 3), dtype=np.uint8)
            with media.open_image(Path("photo.raf"), (512, 512))[0] as image:
                self.assertEqual(image.size, (100, 80))
            raw.postprocess.assert_called_once_with(half_size=True, use_camera_wb=True, no_auto_bright=False)


def build_tiff(date_time=b"2026:10:02 17:59:44\x00", tag=0x0132, byte_order="<"):
    """Assemble a minimal TIFF file exposing one ASCII DateTime-style tag.

    Real RAW files carry EXIF inside a TIFF container, which is why Pillow's
    getexif() comes back empty for them. Building the container by hand keeps
    the test independent of any particular camera's IFD layout.
    """
    end = byte_order
    header = (b"II" if byte_order == "<" else b"MM") + struct.pack(
        f"{end}H", 42
    ) + struct.pack(f"{end}I", 8)
    value_offset = 8 + 2 + 12 + 4
    entry = struct.pack(f"{end}HHI", tag, 2, len(date_time)) + struct.pack(
        f"{end}I", value_offset
    )
    ifd = struct.pack(f"{end}H", 1) + entry + struct.pack(f"{end}I", 0)
    return header + ifd + date_time


def build_nested_tiff() -> bytes:
    """A TIFF whose IFD0 holds DateTime and points at an Exif sub-IFD.

    The sub-IFD carries DateTimeOriginal, so a correct reader must prefer it:
    some cameras leave DateTime as a copy date rather than the exposure.
    """
    original = b"2026:10:02 09:30:00\x00"
    copied = b"2026:10:02 17:59:44\x00"
    header_size = 8
    # IFD0: 2 entries (Exif pointer, DateTime) + next-IFD pointer.
    exif_ifd_offset = header_size + 2 + 24 + 4
    exif_ifd_size = 2 + 12 + 4
    copied_value_offset = exif_ifd_offset + exif_ifd_size
    original_value_offset = copied_value_offset + len(copied)
    return b"".join(
        [
            b"II",
            struct.pack("<H", 42),
            struct.pack("<I", header_size),
            struct.pack("<H", 2),
            # Exif IFD pointer -- entries must be in ascending tag order.
            struct.pack("<HHI", 0x8769, 4, 1) + struct.pack("<I", exif_ifd_offset),
            struct.pack("<HHI", 0x0132, 2, len(copied))
            + struct.pack("<I", copied_value_offset),
            struct.pack("<I", 0),
            # Exif sub-IFD.
            struct.pack("<H", 1),
            struct.pack("<HHI", 0x9003, 2, len(original))
            + struct.pack("<I", original_value_offset),
            struct.pack("<I", 0),
            copied,
            original,
        ]
    )


class RawExifDatetimeTests(unittest.TestCase):
    def _write(self, payload: bytes, name: str = "sample.ARW") -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / name
        path.write_bytes(payload)
        return path

    def test_datetime_tag_is_read_from_ifd0(self):
        for order in ("<", ">"):
            with self.subTest(byte_order=order):
                path = self._write(build_tiff(byte_order=order))
                self.assertEqual(
                    media._raw_exif_datetime(path), "2026:10:02 17:59:44"
                )

    def test_datetime_original_is_preferred_over_datetime(self):
        path = self._write(build_nested_tiff())
        self.assertEqual(media._raw_exif_datetime(path), "2026:10:02 09:30:00")

    def test_non_tiff_and_missing_files_return_empty_string(self):
        self.assertEqual(media._raw_exif_datetime(self._write(b"not a tiff at all")), "")
        self.assertEqual(media._raw_exif_datetime(self._write(b"")), "")
        self.assertEqual(media._raw_exif_datetime(Path("/no/such/file.ARW")), "")

    def test_truncated_ifd_returns_empty_string_rather_than_raising(self):
        payload = build_tiff()
        self.assertEqual(
            media._raw_exif_datetime(self._write(payload[: len(payload) - 12])), ""
        )

    def test_out_of_range_ifd_offset_returns_empty_string(self):
        payload = bytearray(build_tiff())
        payload[4:8] = struct.pack("<I", 0xFFFFFFF0)
        self.assertEqual(media._raw_exif_datetime(self._write(bytes(payload))), "")

    def test_raw_branch_reports_the_capture_time(self):
        jpeg_bytes = io.BytesIO()
        Image.new("RGB", (64, 48), (10, 20, 30)).save(jpeg_bytes, "JPEG")
        data = jpeg_bytes.getvalue()
        raw = mock.MagicMock()
        raw.__enter__.return_value = raw
        raw.extract_thumb.return_value = mock.Mock(
            format=media.rawpy.ThumbFormat.JPEG, data=data
        )
        # A TIFF header followed by the embedded JPEG stands in for a real RAW:
        # enough for the EXIF reader, and rawpy itself stays mocked out.
        path = self._write(build_tiff() + b"\x00" * 4 + data)
        with mock.patch.object(media.rawpy, "imread", return_value=raw):
            _, taken = media.open_image(path, (512, 512))
        self.assertEqual(taken, "2026:10:02 17:59:44")

    def test_analysis_version_bumped_so_raw_rescans(self):
        self.assertEqual(media.analysis_version(Path("a.ARW")), "raw-preview512-v3")
        self.assertEqual(media.analysis_version(Path("a.jpg")), "rgb512-v1")


if __name__ == "__main__":
    unittest.main()
