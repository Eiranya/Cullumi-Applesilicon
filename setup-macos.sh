#!/bin/bash
# Create/refresh the macOS virtualenv and install Cullumi's dependencies.
#
# Written for bash 3.2 (the macOS system shell): no associative arrays, no
# mapfile, no ${var,,} case conversion.
set -e

cd "$(dirname "$0")"

PY="${CULLUMI_PYTHON:-/Users/inori95/.workbuddy/binaries/python/envs/default/bin/python3}"
if [ ! -x "$PY" ]; then
    echo "找不到 Python 解释器：$PY" >&2
    echo "请设置 CULLUMI_PYTHON 指向可用的 python3。" >&2
    exit 1
fi

echo "==> 使用解释器 $PY"
"$PY" -V

echo "==> 安装直接依赖（requirements-macos.txt）"
# --no-deps keeps pip from pulling pywebview, whose proxy_tools dependency
# cannot be built from sdist (see the proxy_tools step below).
"$PY" -m pip install --retries 5 --no-deps -r requirements-macos.txt

echo "==> 安装 PyObjC（pywebview 在 darwin 上通过 WKWebView 使用它们）"
"$PY" -m pip install --retries 5 \
    pyobjc-core \
    pyobjc-framework-Cocoa \
    pyobjc-framework-Quartz \
    pyobjc-framework-WebKit \
    pyobjc-framework-security \
    pyobjc-framework-UniformTypeIdentifiers

echo "==> 安装 pywebview 的无条件导入依赖"
# webview/__init__.py does `from proxy_tools import module_property` and
# webview/http.py does `import bottle` at module scope, so these two are
# required on every platform, not just Windows.
"$PY" -m pip install --retries 5 bottle typing_extensions

echo "==> 安装 pywebview（--no-deps，依赖已单独装好）"
"$PY" -m pip install --retries 5 --no-deps pywebview==6.1

echo "==> 安装 proxy_tools（绕过 sdist 打包缺陷）"
# proxy_tools 0.1.0 ships an sdist whose internal layout collides with pip's
# unpack directory, so both `pip install` and `pip download` fail with
# EEXIST. Downloading the tarball with curl and installing the extracted
# directory sidesteps pip's unpacker entirely.
PROXY_TMP="$(mktemp -d "${TMPDIR:-/tmp}/cullumi-proxytools.XXXXXX")"
if ! "$PY" -m pip download --retries 3 --no-deps --no-binary :all: \
        -d "$PROXY_TMP" proxy_tools >/dev/null 2>&1; then
    curl -fsSL -o "$PROXY_TMP/proxy_tools.tar.gz" \
        "https://files.pythonhosted.org/packages/source/p/proxy_tools/proxy_tools-0.1.0.tar.gz"
    tar xzf "$PROXY_TMP/proxy_tools.tar.gz" -C "$PROXY_TMP"
fi
"$PY" -m pip install --retries 3 --no-build-isolation "$PROXY_TMP"/proxy_tools-0.1.0
rm -rf "$PROXY_TMP"

echo "==> 安装构建与检查工具"
"$PY" -m pip install --retries 5 pyinstaller==6.16.0 ruff==0.12.12

echo "==> 校验导入"
"$PY" - <<'PYCHECK'
import webview
import onnxruntime
import rawpy
import PIL
import numpy
import pillow_heif
import imageio_ffmpeg
from webview.platforms import cocoa  # noqa: F401  (WKWebView backend)

print("  pywebview      6.1")
print("  onnxruntime   ", onnxruntime.__version__, onnxruntime.get_available_providers())
print("  rawpy         ", rawpy.__version__)
print("  Pillow        ", PIL.__version__)
print("  numpy         ", numpy.__version__)
print("  pillow-heif   ", pillow_heif.__version__)
print("  ffmpeg        ", imageio_ffmpeg.get_ffmpeg_exe())
print("  cocoa backend OK")
PYCHECK

echo "==> 依赖安装完成"
