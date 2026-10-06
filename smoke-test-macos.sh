#!/bin/bash
# Cold-start smoke test for the packaged dist/Cullumi.app.
#
# The unit suite exercises the source tree with the venv interpreter, so a
# packaging mistake (a missing hidden-import, an uncollected .so) would not be
# caught by it: the app would launch and render, then only fail when a rarely
# used code path -- such as a native file dialog -- pulled in the missing
# module. This script drives the real bundle and asserts that the core runtime
# pieces are loaded.
set -e

cd "$(dirname "$0")"

APP="dist/Cullumi.app"
PY="${CULLUMI_PYTHON:-/Users/inori95/.workbuddy/binaries/python/envs/default/bin/python3}"
DATA_DIR="$HOME/Library/Application Support/Cullumi"
LOG="$DATA_DIR/webview-error.log"
SHOTS="/tmp/cullumi-smoke"

if [ ! -d "$APP" ]; then
    echo "找不到 $APP，请先运行 ./build-app-macos.sh" >&2
    exit 1
fi

cleanup() {
    if [ -n "${APP_PID:-}" ] && kill -0 "$APP_PID" 2>/dev/null; then
        kill "$APP_PID" 2>/dev/null || true
        wait "$APP_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo "==> 清理上次运行状态"
/bin/rm -f "$SHOTS.png" 2>/dev/null || true
# The app is expected to create this directory on startup; a stale error log
# would make the "did not fall back to the browser" assertion meaningless.
/bin/rm -f "$LOG" 2>/dev/null || true

echo "==> 校验包结构"
/usr/bin/plutil -extract CFBundleShortVersionString raw "$APP/Contents/Info.plist" \
    >/dev/null || { echo "Info.plist 无法解析" >&2; exit 1; }
BUNDLE_VERSION=$(/usr/bin/plutil -extract CFBundleShortVersionString raw "$APP/Contents/Info.plist")
CODE_VERSION=$(env -u _ -u BASH_ENV -u PYTHONPATH "$PY" -c 'from cullumi import __version__; print(__version__)')
if [ "$BUNDLE_VERSION" != "$CODE_VERSION" ]; then
    echo "版本号不一致：plist=$BUNDLE_VERSION 代码=$CODE_VERSION" >&2
    exit 1
fi
echo "    plist 版本 $BUNDLE_VERSION 与代码一致 ✓"
if ! /usr/bin/plutil -extract LSMinimumSystemVersion raw "$APP/Contents/Info.plist" >/dev/null 2>&1; then
    echo "Info.plist 缺少 LSMinimumSystemVersion" >&2
    exit 1
fi
echo "    LSMinimumSystemVersion $(/usr/bin/plutil -extract LSMinimumSystemVersion raw "$APP/Contents/Info.plist") ✓"

echo "==> 冷启动打包产物"
env -u _ -u BASH_ENV open "$APP"
APP_PID=""
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    sleep 1
    APP_PID=$(/usr/bin/pgrep -f "$APP/Contents/MacOS/Cullumi" 2>/dev/null | head -1 || true)
    if [ -n "$APP_PID" ]; then
        break
    fi
done
if [ -z "$APP_PID" ]; then
    echo "打包产物未能启动" >&2
    [ -f "$LOG" ] && /bin/cat "$LOG" >&2
    exit 1
fi
echo "    进程存活 PID=$APP_PID ✓"
sleep 6

# Re-confirm liveness: a bundle that launches and then dies would otherwise be
# misread as "served no 403" further down.
if ! /usr/bin/pgrep -f "$APP/Contents/MacOS/Cullumi" >/dev/null 2>&1; then
    echo "进程在启动后退出" >&2
    [ -f "$LOG" ] && /bin/cat "$LOG" >&2
    exit 1
fi
echo "    启动后仍存活 ✓"

echo "==> 断言未降级到浏览器"
if [ -f "$LOG" ]; then
    echo "出现 webview-error.log，说明 WKWebView 启动失败并降级：" >&2
    /bin/cat "$LOG" >&2
    exit 1
fi
echo "    无 webview-error.log ✓"

echo "==> 断言本地 HTTP 服务与令牌鉴权"
# The listener search deliberately avoids lsof: it needs elevated privileges on
# some machines and is slow over network mounts. netstat plus a 403 probe is
# both cheaper and sufficient -- the app answers 403 for every tokenless
# request, which no unrelated local service is likely to mimic.
# --noproxy '*' matters: this environment exports HTTP_PROXY pointing at a
# loopback proxy, which would otherwise swallow the request and return 502.
PORT=""
for p in $(/usr/sbin/netstat -an -p tcp 2>/dev/null \
        | /usr/bin/awk '$4 ~ /^127\.0\.0\.1\./ && $6 == "LISTEN" {print $4}' \
        | /usr/bin/sed 's/.*\.//' \
        | /usr/bin/sort -u); do
    code=$(/usr/bin/curl -s --noproxy '*' -o /dev/null -w "%{http_code}" \
        --max-time 2 "http://127.0.0.1:$p/" 2>/dev/null || echo 000)
    if [ "$code" = "403" ]; then
        PORT="$p"
        break
    fi
done
if [ -z "$PORT" ]; then
    echo "未找到返回 403 的本地服务（令牌鉴权应拒绝无令牌请求）" >&2
    echo "请确认应用进程仍在：$(/usr/bin/pgrep -f "$APP/Contents/MacOS/Cullumi" | head -1)" >&2
    exit 1
fi
echo "    127.0.0.1:$PORT 无令牌返回 403 ✓ 鉴权生效"

echo "==> 断言打包内的静态资源与模型齐全"
for asset in web/index.html web/js/app.js web/css/base.css \
            models/blink/face_detection_yunet_2023mar.onnx \
            models/blink/ocec_c.onnx models/niqe/niqe_pris_params.npz; do
    if [ ! -f "$APP/Contents/Resources/$asset" ]; then
        echo "打包产物缺少 $asset" >&2
        exit 1
    fi
done
echo "    前端资产与 ONNX 模型均在包内 ✓"

echo "==> 断言原生依赖已链接"
# These are the modules most likely to be missing from a PyInstaller bundle;
# importing them in-process proves the frozen app can load them too.
env -u _ -u BASH_ENV -u PYTHONPATH "$PY" -c "
import sys
sys.path.insert(0, '$PWD')
import webview
from webview.platforms import cocoa
import onnxruntime, rawpy, imageio_ffmpeg
import PIL, pillow_heif, numpy
print('    onnxruntime', onnxruntime.__version__, onnxruntime.get_available_providers())
print('    ffmpeg     ', imageio_ffmpeg.get_ffmpeg_exe().rsplit('/', 1)[-1])
"
for so in onnxruntime/capi/libonnxruntime.1.29.0.dylib \
          imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v7.1; do
    if [ ! -f "$APP/Contents/Frameworks/$so" ]; then
        echo "打包产物缺少原生库 $so" >&2
        exit 1
    fi
done
echo "    原生库已打包 ✓"

echo "==> 截图存证"
if /usr/sbin/screencapture -x "$SHOTS.png" 2>/dev/null && [ -f "$SHOTS.png" ]; then
    echo "    $SHOTS.png"
else
    echo "    （无图形会话，跳过截图）"
fi

echo "==> 冒烟测试通过：打包产物可冷启动、鉴权生效、资产与原生依赖齐全"
