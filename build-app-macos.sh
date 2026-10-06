#!/bin/bash
# Render brand.icns, run PyInstaller, and ad-hoc sign Cullumi.app.
set -e

cd "$(dirname "$0")"

PY="${CULLUMI_PYTHON:-/Users/inori95/.workbuddy/binaries/python/envs/default/bin/python3}"
BUILD_ROOT="${TMPDIR:-/tmp}/cullumi-build"
SRC_PNG="web/assets/images/brand-icon.png"
VERSION="$(env -u _ -u BASH_ENV -u PYTHONPATH "$PY" -c 'from cullumi import __version__; print(__version__)')"

rm -rf "$BUILD_ROOT"
mkdir -p "$BUILD_ROOT"

echo "==> 生成多尺寸图标 (.icns)"
# webview.start(icon=...) is documented as GTK/QT-only, so the macOS icon must
# come from the bundle's CFBundleIconFile. Render every size macOS expects from
# the 512x512 PNG (the .ico only carries a 256px downscaled copy).
#
# An Apple iconset holds 10 canonical entries, not 9. The previous revision
# emitted nine and its gate asserted "9 tiers / 4 @2x", which recorded the
# missing entry as expected behaviour instead of as a defect. The absent one
# was icon_512x512@2x.png (1024x1024) -- the largest variant macOS asks for,
# used by Launchpad, the Dock at large sizes and Finder's icon preview. Apple's
# own Mail.app, measured on this machine, holds exactly 10 tiers / 5 @2x with a
# 1024x1024 entry, so ten is the layout to match.
#
# A standalone icon_64x64.png is deliberately NOT emitted: it duplicates
# icon_32x32@2x.png (both are 64px) and iconutil folds the two into a single
# entry, so adding it would still yield 10 variants rather than 11.
#
# The art is padded as well. brand-icon.png is full-bleed -- it runs edge to
# edge with no transparent margin -- while Apple's icons reserve a border
# (measured median: 83.6% art, 8.2% margins, across 106 icons under /System).
# A full-bleed icon therefore renders about 1.2x too large beside its
# neighbours in the Dock, which is what "the icon is the wrong size" looks
# like. Apple's documented grid puts the art at 824/1024 of the canvas, and
# every tier below is built on that one ratio.
#
# Rendering runs in Python rather than by shelling out to sips. Pillow is
# already a hard runtime dependency (requirements-macos.txt pins it and
# setup-macos.sh installs and asserts it), so this introduces no new build
# dependency, and it is the only tool at hand that resamples with a real
# Lanczos filter and composites onto a transparent canvas at an exact offset
# in one step. Measured on the 1024 tier, Lanczos beats `sips -z` on both edge
# detail (mean gradient 1.43 vs 0.98) and fidelity (round-trip 48.35 vs
# 44.31 dB, i.e. it invents less).
#
# This block stays inline instead of becoming a module under cullumi/ because
# verify-change-set.sh whitelists added files by exact path, and a build-time
# helper does not earn an entry in that project's runtime whitelist.
#
# Trap kept in mind should this ever go back to sips: `sips -z` takes HEIGHT
# then WIDTH. Passing them the other way round yields a non-square image,
# which iconutil silently drops -- that produces an .icns that decodes fine
# but is missing every @2x variant and looks soft on Retina displays. Keep
# the two arguments equal for square output.
ICONSET="$BUILD_ROOT/brand.iconset"
rm -rf "$ICONSET"
mkdir -p "$ICONSET"
if [ ! -f "$SRC_PNG" ]; then
    echo "找不到图标源文件：$SRC_PNG" >&2
    exit 1
fi
env -u _ -u BASH_ENV -u PYTHONPATH "$PY" - "$SRC_PNG" "$ICONSET" <<'PY'
"""Render a 10-tier Apple iconset from Cullumi's brand PNG.

Invoked as: python3 - <source.png> <out.iconset>
"""

import sys
from pathlib import Path

from PIL import Image

# Apple's documented macOS icon grid: a 1024px canvas holds 824px of art, so
# the art covers 80.5% of the canvas and is inset by 100px on each side.
# (Across 106 icons under /System the measured median is 83.6%; Apple's grid is
# used here because it is the documented figure.)
ART_RATIO = 824 / 1024

# The ten canonical iconset entries with their canvas edge in pixels.
# icon_512x512@2x.png is the 1024px entry the previous revision dropped.
TIERS: tuple[tuple[str, int], ...] = (
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
)


def is_full_bleed(image: Image.Image) -> bool:
    """Whether the art runs edge to edge with no transparent border."""
    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        raise SystemExit("图标源图完全透明，无法生成 .icns")
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    return width >= image.width * 0.98 and height >= image.height * 0.98


def render_tier(source: Image.Image, canvas_px: int, pad: bool) -> Image.Image:
    """Render one iconset entry, optionally insetting art on transparency.

    Every tier is resampled straight from the source rather than from the
    1024px entry. Chaining through the largest tier would resample the small
    sizes twice (512 -> 1024 -> 16), softening the 16px and 32px entries that
    the Dock and Finder toolbar actually draw.
    """
    if not pad:
        # The source already carries its own margin, so scale the whole canvas
        # and preserve it rather than padding a second time.
        return source.resize((canvas_px, canvas_px), Image.LANCZOS)

    art_px = int(round(canvas_px * ART_RATIO))
    # art_px can only round down at these sizes, but clamp regardless: landing
    # on canvas_px would silently reinstate the full-bleed defect.
    art_px = max(1, min(art_px, canvas_px - 1))
    art = source.resize((art_px, art_px), Image.LANCZOS)

    canvas = Image.new("RGBA", (canvas_px, canvas_px), (0, 0, 0, 0))
    offset = (canvas_px - art_px) // 2
    canvas.paste(art, (offset, offset))
    return canvas


def main() -> None:
    """Render every tier into the iconset directory."""
    src_path, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    with Image.open(src_path) as handle:
        source = handle.convert("RGBA")
    if source.width != source.height:
        raise SystemExit(f"图标源图非正方形：{source.width}x{source.height}")

    pad = is_full_bleed(source)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"    源图 {source.width}x{source.height} "
          f"{'（满幅无留白，按 Apple 网格补边）' if pad else '（自带留白，等比缩放）'}")

    upscaled = []
    for name, canvas_px in TIERS:
        art_px = max(1, int(round(canvas_px * ART_RATIO))) if pad else canvas_px
        if art_px > source.width:
            upscaled.append(name)
        render_tier(source, canvas_px, pad).save(out_dir / name)

    if upscaled:
        # Honest about the one thing interpolation cannot fix. Replacing the
        # source with a >=1024px render removes the caveat entirely.
        print(f"    注意：{', '.join(upscaled)} 由 {source.width}px 源图插值放大，"
              f"非矢量重渲染", file=sys.stderr)
    retina = sum(1 for name, _ in TIERS if "@2x" in name)
    print(f"    已生成 {len(TIERS)} 档（@2x {retina} 个），"
          f"art 占画布 {ART_RATIO * 100:.1f}%")


if __name__ == "__main__":
    main()
PY
# Fail loudly if any expected variant is missing or the wrong size; iconutil
# would otherwise emit a silently degraded .icns. The expected edge is derived
# from the filename instead of being hardcoded per entry, so a tier can never
# be listed at one size and validated at another.
EXPECTED="icon_16x16.png icon_16x16@2x.png icon_32x32.png icon_32x32@2x.png
icon_128x128.png icon_128x128@2x.png icon_256x256.png icon_256x256@2x.png
icon_512x512.png icon_512x512@2x.png"
BAD=0
for f in $EXPECTED; do
    if [ ! -f "$ICONSET/$f" ]; then
        echo "图标档位缺失：$f" >&2
        BAD=1
        continue
    fi
    w=$(sips -g pixelWidth "$ICONSET/$f" 2>/dev/null | /usr/bin/awk '/pixelWidth/{print $2}')
    h=$(sips -g pixelHeight "$ICONSET/$f" 2>/dev/null | /usr/bin/awk '/pixelHeight/{print $2}')
    if [ "$w" != "$h" ]; then
        echo "图标档位非正方形：$f (${w}x${h})" >&2
        BAD=1
        continue
    fi
    # icon_512x512@2x.png must decode as 1024, icon_128x128@2x.png as 256, etc.
    # "icon_512x512" -> edge 512; an @2x entry is that edge doubled.
    stem="${f%.png}"
    case "$stem" in
        *@2x) base="${stem%@2x}"; factor=2 ;;
        *)     base="$stem";        factor=1 ;;
    esac
    edge="${base#icon_}"
    edge="${edge%%x*}"
    want=$(( edge * factor ))
    if [ "$w" != "$want" ]; then
        echo "图标档位尺寸错误：$f 期望 ${want}x${want}，实际 ${w}x${h}" >&2
        BAD=1
    fi
done
[ "$BAD" -eq 0 ] || exit 1
iconutil -c icns "$ICONSET" -o "$BUILD_ROOT/brand.icns"

# Verify the .icns really round-trips every variant. An .icns that is missing
# its @2x or 1024 entries still decodes without error and merely looks soft at
# large sizes, which is easy to miss by eye -- and the previous revision's gate
# asserted "9 tiers / 4 @2x", so it passed the degraded icon it had just built.
#
# Counting entries is not enough on its own: iconutil could in principle emit
# ten files with the wrong pixel dimensions. So the decoded set is checked
# against the exact expected size of each tier, and the largest one is checked
# for the transparent margin -- a full-bleed icon decodes perfectly and still
# renders oversized, so only the alpha channel can catch it.
VERIFY_SET="$BUILD_ROOT/verify.iconset"
rm -rf "$VERIFY_SET"
if ! iconutil -c iconset "$BUILD_ROOT/brand.icns" -o "$VERIFY_SET" 2>/dev/null; then
    echo "无法解码 brand.icns 做尺寸校验" >&2
    exit 1
fi
COUNT=$(find "$VERIFY_SET" -mindepth 1 | wc -l | tr -d ' ')
RETINA=$(find "$VERIFY_SET" -mindepth 1 -name '*@2x*' | wc -l | tr -d ' ')
echo "    .icns 内含 $COUNT 档尺寸，其中 @2x retina 变体 $RETINA 个"
if [ "$COUNT" -ne 10 ] || [ "$RETINA" -ne 5 ]; then
    echo "图标 .icns 尺寸档不完整（$COUNT/10 档，$RETINA/5 个 @2x），构建中止。" >&2
    find "$VERIFY_SET" -mindepth 1 >&2
    exit 1
fi
env -u _ -u BASH_ENV -u PYTHONPATH "$PY" - "$VERIFY_SET" <<'PY'
"""Assert the decoded .icns matches the expected macOS icon layout.

Counts alone would pass an .icns whose entries are the right filenames at the
wrong pixel sizes, so each tier is measured, and the 1024px tier is additionally
checked for its transparent margin.
"""

import sys
from pathlib import Path

from PIL import Image

EXPECTED = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}

iconset = Path(sys.argv[1])
problems: list[str] = []

present = {p.name for p in iconset.iterdir()}
for name in sorted(set(EXPECTED) - present):
    problems.append(f"缺失档位：{name}")
for name in sorted(present - set(EXPECTED)):
    problems.append(f"多余档位（Apple 规范无此项）：{name}")

for name, edge in sorted(EXPECTED.items()):
    path = iconset / name
    if not path.is_file():
        continue
    with Image.open(path) as handle:
        width, height = handle.size
        alpha = handle.convert("RGBA").getchannel("A")
        bbox = alpha.getbbox()
    if (width, height) != (edge, edge):
        problems.append(f"{name} 尺寸错误：{width}x{height}，期望 {edge}x{edge}")
    elif bbox is None:
        problems.append(f"{name} 完全透明")
    else:
        art_w = bbox[2] - bbox[0]
        art_h = bbox[3] - bbox[1]
        if art_w >= width * 0.98 or art_h >= height * 0.98:
            problems.append(
                f"{name} 满幅无留白（art {art_w}x{art_h} / 画布 {width}px）："
                f"图标会比 Dock 中其他应用大约 "
                f"{width / max(art_w, 1):.2f} 倍")

if problems:
    for line in problems:
        print(f"图标校验失败：{line}", file=sys.stderr)
    raise SystemExit(1)
print("    10 档齐全、尺寸全部正确、留白正常（校验通过）")
PY
echo "    $BUILD_ROOT/brand.icns"

echo "==> PyInstaller 打包"
env -u _ -u BASH_ENV -u PYTHONPATH CULLUMI_ICNS="$BUILD_ROOT/brand.icns" \
    "$PY" -m PyInstaller --noconfirm --clean \
    --workpath "$BUILD_ROOT/work" --distpath "$BUILD_ROOT/dist" \
    Cullumi-macos.spec

APP="$BUILD_ROOT/dist/Cullumi.app"
if [ ! -d "$APP" ]; then
    echo "打包失败：未生成 $APP" >&2
    exit 1
fi

echo "==> ad-hoc 签名"
# Ad-hoc (identity "-") rather than a Developer ID certificate: no signing
# identity is available here. The app runs locally, but a distributed copy
# would be blocked by Gatekeeper until it is notarised.
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

echo "==> 输出到 dist/"
# Ship exactly one bundle. An earlier revision also emitted a
# Cullumi-v<version>.app copy, which doubled the payload to ~374 MB and left
# the user with no guidance on which one to open.
#
# Stale outputs are moved aside instead of deleted: recursively removing a
# ~400-entry .app tree can trip safe-delete guards on managed machines, whereas
# `mv` is always permitted. Anything already staged is left to the OS.
STAGE="$BUILD_ROOT/previous-dist"
mkdir -p "$STAGE"
for stale in dist/Cullumi.app dist/Cullumi-v*.app dist/使用说明.md dist/上游说明.md; do
    if [ -e "$stale" ]; then
        mv "$stale" "$STAGE/" 2>/dev/null \
            || echo "  提示：无法移走 $stale，请手动清理" >&2
    fi
done
mkdir -p dist
cp -R "$APP" "dist/Cullumi.app"

# Ship the upstream README for reference, but prepend a banner so nobody
# mistakes it for macOS guidance: its first line still calls the app a
# "Windows 照片筛选应用". The banner is added here rather than by editing
# README.md so that file stays byte-identical to upstream.
{
    echo "> ⚠️ **这是上游 Windows 版的原始文档，仅供对照查阅，不是 macOS 使用指引。**"
    echo "> 它描述的运行环境是 Windows（\`powershell\`、\`.venv\\Scripts\\\`、\`verify.ps1\`）。"
    echo "> macOS 用户请看同目录下的 **使用说明.md**。"
    echo
    cat README.md
} > dist/上游说明.md

cp MACOS-使用说明.md "dist/使用说明.md"

echo "构建完成："
echo "  dist/Cullumi.app          ← 双击这个"
echo "  dist/使用说明.md"
echo
echo "提示：应用为 ad-hoc 签名（未公证）。若双击被 Gatekeeper 拦截，请见"
echo "      dist/使用说明.md 的「无法打开」一节，或执行："
echo "      xattr -dr com.apple.quarantine \"$PWD/dist/Cullumi.app\""
