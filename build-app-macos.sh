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
# NOTE: `sips -z` takes HEIGHT then WIDTH. Passing them the other way round
# yields a non-square image, which iconutil silently drops -- that produces an
# .icns that decodes fine but is missing every @2x variant and looks soft on
# Retina displays. Keep the two arguments equal for square output.
#
# An Apple iconset holds 9 canonical entries. A standalone icon_64x64.png is
# deliberately NOT emitted: it duplicates icon_32x32@2x.png (both are 64px) and
# iconutil folds the two into a single entry, so adding it would yield 9
# variants rather than 10.
ICONSET="$BUILD_ROOT/brand.iconset"
mkdir -p "$ICONSET"
if [ ! -f "$SRC_PNG" ]; then
    echo "找不到图标源文件：$SRC_PNG" >&2
    exit 1
fi
for s in 16 32 128 256 512; do
    sips -z "$s" "$s" "$SRC_PNG" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
done
# Retina (@2x) variants: 16@2x=32, 32@2x=64, 128@2x=256, 256@2x=512.
sips -z 32   32   "$SRC_PNG" --out "$ICONSET/icon_16x16@2x.png"   >/dev/null
sips -z 64   64   "$SRC_PNG" --out "$ICONSET/icon_32x32@2x.png"   >/dev/null
sips -z 256  256  "$SRC_PNG" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips -z 512  512  "$SRC_PNG" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
# Fail loudly if any expected variant is missing or the wrong size; iconutil
# would otherwise emit a silently degraded .icns.
EXPECTED="icon_16x16.png icon_16x16@2x.png icon_32x32.png icon_32x32@2x.png
icon_128x128.png icon_128x128@2x.png icon_256x256.png icon_256x256@2x.png
icon_512x512.png"
for f in $EXPECTED; do
    if [ ! -f "$ICONSET/$f" ]; then
        echo "图标档位缺失：$f" >&2
        exit 1
    fi
    w=$(sips -g pixelWidth "$ICONSET/$f" 2>/dev/null | /usr/bin/awk '/pixelWidth/{print $2}')
    h=$(sips -g pixelHeight "$ICONSET/$f" 2>/dev/null | /usr/bin/awk '/pixelHeight/{print $2}')
    if [ "$w" != "$h" ]; then
        echo "图标档位非正方形：$f (${w}x${h})" >&2
        exit 1
    fi
done
iconutil -c icns "$ICONSET" -o "$BUILD_ROOT/brand.icns"

# Verify the .icns really round-trips every @2x variant; a 1x-only icon decodes
# without error but renders soft on Retina, which is easy to miss by eye.
VERIFY_SET="$BUILD_ROOT/verify.iconset"
rm -rf "$VERIFY_SET"
if iconutil -c iconset "$BUILD_ROOT/brand.icns" -o "$VERIFY_SET" 2>/dev/null; then
    COUNT=$(ls -1 "$VERIFY_SET" 2>/dev/null | wc -l | tr -d ' ')
    RETINA=$(ls -1 "$VERIFY_SET" 2>/dev/null | grep -c '@2x' || true)
    echo "    .icns 内含 $COUNT 档尺寸，其中 @2x retina 变体 $RETINA 个"
    if [ "$COUNT" -ne 9 ] || [ "$RETINA" -ne 4 ]; then
        echo "图标 .icns 尺寸档不完整（$COUNT/9 档，$RETINA/4 个 @2x），构建中止。" >&2
        ls -1 "$VERIFY_SET" >&2
        exit 1
    fi
else
    echo "无法解码 brand.icns 做尺寸校验" >&2
    exit 1
fi
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
