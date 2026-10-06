# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the macOS .app bundle (Apple Silicon).

Differences from the Windows ``Cullumi.spec``:

* ``BUNDLE`` produces ``Cullumi.app`` instead of a COLLECT directory.
* ``msvcp140.dll`` is dropped (Windows VC++ runtime).
* ``clr`` / ``webview.platforms.edgechromium`` / ``webview.platforms.winforms``
  hidden imports are replaced by the PyObjC modules that back WKWebView.
* UPX is disabled: there is no macOS UPX, and running it over arm64 binaries
  risks corrupting them.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# Set by build-app-macos.sh after rendering brand.iconset into an .icns.
APP_ICON = os.environ.get("CULLUMI_ICNS", "")

# Keep the bundle version in lockstep with cullumi/__init__.py so Finder's
# "Get Info" and the in-app updater never disagree.
sys.path.insert(0, os.path.abspath(os.curdir))
from cullumi import __version__ as APP_VERSION  # noqa: E402

# onnxruntime 1.29.0 ships macosx_14_0_arm64 wheels, so anything older cannot
# load the blink-detection models. Declaring it here turns a confusing launch
# crash on macOS 11-13 into a friendly "requires macOS 14" message.
MINIMUM_MACOS = "14.0"

runtime_datas = []
# Keep each model and its provenance/license files together. Fail packaging
# early if any distributable resource is absent.
model_resources = {
    "blink": (
        "face_detection_yunet_2023mar.onnx",
        "ocec_c.onnx",
        "README.md",
        "LICENSE-YUNET.txt",
        "LICENSE-OCEC.txt",
        "LICENSE-ONNXRUNTIME.txt",
    ),
    "niqe": ("niqe_pris_params.npz", "SOURCE.json", "LICENSE.txt", "ADAPTATION.md"),
}
for model_name, resources in model_resources.items():
    for resource in resources:
        if not os.path.isfile(os.path.join("models", model_name, resource)):
            raise FileNotFoundError(f"Missing {model_name} resource: {resource}")

# imageio-ffmpeg ships the actual ffmpeg binary as package data; motion.py
# resolves it through imageio_ffmpeg.get_ffmpeg_exe().
runtime_datas.extend(collect_data_files("imageio_ffmpeg"))

# onnxruntime and rawpy load their native cores from a .dylib next to the
# Python extension modules, which PyInstaller's static analysis cannot see.
runtime_binaries = collect_dynamic_libs("onnxruntime")
runtime_binaries += collect_dynamic_libs("rawpy")

# PyObjC bridges are .so files loaded at runtime by pyobjc itself.
runtime_binaries += collect_dynamic_libs("objc")

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=runtime_binaries,
    datas=[("web", "web"), ("models", "models"), *runtime_datas],
    hiddenimports=[
        "cullumi.analysis_worker",
        "webview",
        "webview.platforms.cocoa",
        "objc",
        "AppKit",
        "Foundation",
        "WebKit",
        "Quartz",
        "UniformTypeIdentifiers",
        "pillow_heif",
        "rawpy",
        "imageio_ffmpeg",
        "onnxruntime",
        "onnxruntime.capi._pybind_state",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Windows-only backends: importing them pulls in pythonnet, which is
        # not installed on macOS.
        "clr",
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
        "webview.platforms.mshtml",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Cullumi",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Cullumi",
)
app = BUNDLE(
    coll,
    name="Cullumi.app",
    icon=APP_ICON or None,
    bundle_identifier="com.cullumi.macos",
    info_plist={
        "CFBundleName": "Cullumi",
        "CFBundleDisplayName": "Cullumi",
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "LSMinimumSystemVersion": MINIMUM_MACOS,
        "NSHighResolutionCapable": True,
        # The app only reads/writes the project folder the user picks and its
        # own ~/Library/Application Support/Cullumi, so sandboxing is not
        # required and would block the file dialogs.
        "NSRequiresAquaSystemAppearance": False,
    },
)
