#!/bin/bash
# One-shot macOS build: dependencies -> checks -> packaged .app.
set -e

cd "$(dirname "$0")"

# `_` holds the last command's path and is invalid UTF-8 because this repo
# lives under a non-ASCII directory; BASH_ENV can shim coreutils so that grep
# and sed fail silently. Drop both before invoking any toolchain.
# PYTHONPATH is dropped because this machine injects a sitecustomize.py that
# breaks os.mkdir(exist_ok=True).
CLEAN_ENV="env -u _ -u BASH_ENV -u PYTHONPATH"

echo "############ 1/3 依赖 ############"
$CLEAN_ENV ./setup-macos.sh

echo
echo "############ 2/3 检查 ############"
$CLEAN_ENV ./verify-macos.sh

echo
echo "############ 3/3 打包 ############"
$CLEAN_ENV ./build-app-macos.sh
