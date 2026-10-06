from __future__ import annotations

import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

RELEASES_API_URL = "https://api.github.com/repos/Yuumi0221/Cullumi/releases/latest"
RELEASES_PAGE_URL = "https://github.com/Yuumi0221/Cullumi/releases"
_VERSION_PATTERN = re.compile(r"(?<!\d)(\d+)(?:\.(\d+))?(?:\.(\d+))?")

# Release packaging differs per platform. macOS ships a disk image (or a plain
# archive for portable builds) while Windows ships an MSI or a portable zip.
# Scores express "how likely is this the primary installer for this platform".
if sys.platform == "darwin":
    ASSET_SUFFIX_SCORES = {".dmg": 30, ".zip": 20, ".tar.gz": 10}
    ASSET_PLATFORM_HINTS = (
        "macos", "mac", "osx", "apple", "silicon", "arm64", "苹果", "mac版",
    )
else:
    ASSET_SUFFIX_SCORES = {".zip": 30, ".exe": 20, ".msi": 10}
    ASSET_PLATFORM_HINTS = ("windows", "win64", "win-x64", "portable", "便携")


def platform_name() -> str:
    """Return the user-facing name of the platform this build targets."""
    return "macOS" if sys.platform == "darwin" else "Windows"


def version_key(value: str) -> tuple[int, int, int]:
    match = _VERSION_PATTERN.search(str(value))
    if not match:
        return (0, 0, 0)
    return tuple(int(part or 0) for part in match.groups())


def _request(url: str, current_version: str = "") -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Cullumi/{current_version or 'update'}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def select_release_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the release archive that best matches the running platform.

    Source bundles and debug symbol archives are always rejected. The suffix
    allow-list and the platform keyword bonus come from the module-level
    tables so Windows keeps preferring ``.zip``/``.exe``/``.msi`` while macOS
    prefers ``.dmg``.
    """
    candidates: list[tuple[int, dict[str, Any]]] = []
    for asset in assets:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        lowered = name.casefold()
        suffix = Path(name).suffix.casefold()
        # ".tar.gz" is the only two-part suffix we accept; Path.suffix would
        # report just ".gz" for it, so recognise the compound form explicitly.
        if lowered.endswith(".tar.gz"):
            suffix = ".tar.gz"
        if not url or suffix not in ASSET_SUFFIX_SCORES:
            continue
        if any(word in lowered for word in ("source", "源码", "symbols", "debug")):
            continue
        score = ASSET_SUFFIX_SCORES[suffix]
        if any(word in lowered for word in ASSET_PLATFORM_HINTS):
            score += 50
        if "cullumi" in lowered:
            score += 30
        candidates.append((score, asset))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def check_for_update(
    current_version: str,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(_request(RELEASES_API_URL, current_version), timeout=12) as response:
            release = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {
                "current_version": current_version,
                "latest_version": "",
                "update_available": False,
                "download_available": False,
                "release_url": RELEASES_PAGE_URL,
                "release_notes": "",
                "no_release": True,
                "platform": platform_name(),
            }
        raise RuntimeError(f"GitHub 返回错误状态 {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RuntimeError("无法连接 GitHub，请检查网络后重试") from error
    except (ValueError, TypeError, KeyError) as error:
        raise RuntimeError("GitHub 更新信息格式无效") from error

    if not isinstance(release, dict):
        raise RuntimeError("GitHub 更新信息格式无效")
    tag = str(release.get("tag_name") or "")
    latest_version = tag.lstrip("vV")
    asset = select_release_asset(list(release.get("assets") or []))
    update_available = version_key(latest_version) > version_key(current_version)
    release_body = release.get("body")
    return {
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": update_available,
        "download_available": bool(update_available and asset),
        "release_url": str(release.get("html_url") or RELEASES_PAGE_URL),
        "release_name": str(release.get("name") or tag),
        "release_notes": release_body if isinstance(release_body, str) else "",
        "asset_name": str(asset.get("name") or "") if asset else "",
        "download_url": str(asset.get("browser_download_url") or "") if asset else "",
        "no_release": False,
        # Lets the UI name the platform it is actually running on instead of
        # hardcoding "Windows" in the no-matching-asset message.
        "platform": platform_name(),
    }


def downloads_directory() -> Path:
    """Return the user's Downloads folder, honouring the Windows known-folder path.

    macOS has no equivalent registry key, so it always resolves to
    ``~/Downloads`` via the fallback below.
    """
    if os.name == "nt":
        try:
            import winreg

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            value_name = "{374DE290-123F-4565-9164-39C4925E467B}"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                value, _ = winreg.QueryValueEx(key, value_name)
            return Path(os.path.expandvars(value)).resolve()
        except (OSError, ValueError):
            pass
    return (Path.home() / "Downloads").resolve()


def _unused_download_path(directory: Path, name: str) -> Path:
    safe_name = Path(name).name.strip() or "Cullumi-update.zip"
    candidate = directory / safe_name
    for index in range(1, 1000):
        if not candidate.exists():
            return candidate
        candidate = directory / f"{Path(safe_name).stem} ({index}){Path(safe_name).suffix}"
    raise RuntimeError("Downloads 文件夹中同名更新文件过多")


def download_release_asset(
    download_url: str,
    asset_name: str,
    destination: Path | None = None,
    opener: Callable[..., Any] | None = None,
) -> Path:
    parsed = urllib.parse.urlparse(download_url)
    if parsed.scheme != "https" or parsed.hostname not in {"github.com", "www.github.com"}:
        raise ValueError("更新下载地址不是受信任的 GitHub 地址")
    directory = (destination or downloads_directory()).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    target = _unused_download_path(directory, asset_name)
    temp = target.with_name(target.name + ".part")
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(_request(download_url), timeout=30) as response, temp.open("wb") as handle:
            shutil.copyfileobj(response, handle, length=1024 * 1024)
        if not temp.is_file() or temp.stat().st_size == 0:
            raise RuntimeError("下载的更新文件为空")
        temp.replace(target)
        return target
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        temp.unlink(missing_ok=True)
        raise RuntimeError("更新下载失败，请检查网络和 Downloads 文件夹权限") from error
    except Exception:
        temp.unlink(missing_ok=True)
        raise
