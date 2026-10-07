from __future__ import annotations

import csv
import json
import re
import shutil
import sqlite3
import threading
import uuid
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, ContextManager

from .capture_variants import rebuild_capture_variants
from .fs_utils import atomic_write_json, is_within
from .project_store import Project, connect_db, safe_relative_path

QUARANTINE_DIR = "_照片筛选隔离"

def quarantine_preview(project: Project) -> dict[str, Any]:
    with closing(connect_db(project.db_path)) as conn:
        rows = conn.execute(
            """SELECT id,relative_path,size,mtime,media_type,motion_kind,
                      motion_relative_path,motion_size,motion_mtime
                 FROM photos WHERE decision='remove' AND status='active'
                 ORDER BY relative_path"""
        ).fetchall()
    items = [dict(row) for row in rows]
    return {
        "count": len(items),
        "total_size": sum(
            int(item["size"] or 0)
            + (
                int(item["motion_size"] or 0)
                if item["motion_kind"] == "apple_sidecar"
                and item["motion_relative_path"] != item["relative_path"]
                else 0
            )
            for item in items
        ),
        "items": items,
    }


def _write_manifest_csv(batch_root: Path, manifest: list[dict[str, Any]]) -> None:
    with (batch_root / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = [
            "photo_id", "relative_path", "quarantine_path", "restore_path",
            "companion_relative_path", "companion_quarantine_path",
            "companion_restore_path", "status", "size", "error"
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows([{key: row.get(key, "") for key in fields} for row in manifest])


def _quarantine_batch_root(project: Project, batch_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", batch_id):
        raise ValueError("隔离批次标识无效")
    quarantine_root = safe_relative_path(project.root, QUARANTINE_DIR, "隔离目录")
    return safe_relative_path(quarantine_root, batch_id, "隔离批次路径")


@dataclass(frozen=True)
class _PreparedQuarantine:
    entry: dict[str, Any]
    item: dict[str, Any]
    moves: list[tuple[Path, Path]]


def _prepare_quarantine_item(
    project: Project, batch_root: Path, item: dict[str, Any]
) -> tuple[dict[str, Any], _PreparedQuarantine | None]:
    source = safe_relative_path(project.root, item["relative_path"], "照片路径")
    entry: dict[str, Any] = {
        "photo_id": int(item["id"]),
        "relative_path": item["relative_path"],
        "status": "pending",
        "size": int(item["size"] or 0)
        + (
            int(item["motion_size"] or 0)
            if item["motion_kind"] == "apple_sidecar"
            and item["motion_relative_path"] != item["relative_path"]
            else 0
        ),
    }
    companion = None
    if (
        item["motion_kind"] == "apple_sidecar"
        and item["motion_relative_path"]
        and item["motion_relative_path"] != item["relative_path"]
    ):
        companion = safe_relative_path(
            project.root, item["motion_relative_path"], "动态照片视频路径"
        )
        entry["companion_relative_path"] = item["motion_relative_path"]
    sources = [(source, int(item["size"] or 0), float(item["mtime"] or 0))]
    if companion:
        sources.append(
            (companion, int(item["motion_size"] or 0), float(item["motion_mtime"] or 0))
        )
    if any(not candidate.exists() for candidate, _, _ in sources):
        entry["status"] = "missing"
        return entry, None
    if any(
        candidate.stat().st_size != expected_size
        or abs(candidate.stat().st_mtime - expected_mtime) > 0.01
        for candidate, expected_size, expected_mtime in sources
    ):
        entry["status"] = "changed"
        return entry, None
    destination = safe_relative_path(batch_root, item["relative_path"], "隔离目标路径")
    entry["quarantine_path"] = destination.relative_to(project.root.resolve()).as_posix()
    moves = [(source, destination)]
    if companion:
        companion_destination = safe_relative_path(
            batch_root, item["motion_relative_path"], "动态照片隔离目标路径"
        )
        entry["companion_quarantine_path"] = companion_destination.relative_to(
            project.root.resolve()
        ).as_posix()
        moves.append((companion, companion_destination))
    return entry, _PreparedQuarantine(entry, item, moves)


def _prepared_quarantine_status(prepared: _PreparedQuarantine) -> str:
    if any(not source.exists() for source, _ in prepared.moves):
        return "missing"
    expected = [
        (int(prepared.item["size"] or 0), float(prepared.item["mtime"] or 0)),
        *(
            [
                (
                    int(prepared.item["motion_size"] or 0),
                    float(prepared.item["motion_mtime"] or 0),
                )
            ]
            if len(prepared.moves) > 1
            else []
        ),
    ]
    changed = any(
        source.stat().st_size != expected_size
        or abs(source.stat().st_mtime - expected_mtime) > 0.01
        for (source, _), (expected_size, expected_mtime) in zip(
            prepared.moves, expected
        )
    )
    return "changed" if changed else ""


def _move_quarantine_assets(moves: list[tuple[Path, Path]]) -> None:
    moved_paths: list[tuple[Path, Path]] = []
    try:
        for source, destination in moves:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            moved_paths.append((source, destination))
    except Exception:
        for original, moved in reversed(moved_paths):
            if moved.exists() and not original.exists():
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(moved), str(original))
        raise


def _record_quarantined_item(
    conn: sqlite3.Connection,
    batch_id: str,
    manifest: list[dict[str, Any]],
    entry: dict[str, Any],
) -> None:
    conn.execute("UPDATE photos SET status='quarantined' WHERE id=?", (entry["photo_id"],))
    moved = [row for row in manifest if row["status"] == "moved"]
    conn.execute(
        "UPDATE quarantine_batches SET count=?,total_size=? WHERE id=?",
        (len(moved), sum(int(row.get("size") or 0) for row in moved), batch_id),
    )
    conn.commit()


def _new_batch_id() -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"


def _quarantine_counts(manifest: list[dict[str, Any]]) -> dict[str, int]:
    """Tally a manifest into the counts the UI reports.

    ``skipped`` stays "everything that did not move" for backwards
    compatibility with the original return value, but ``failed`` is broken out
    on purpose: a file that could not be moved is a different fact from one
    that was never eligible (missing / changed on disk), and folding the two
    together is what let a partly-failed run read as a clean one.
    """
    moved = sum(1 for row in manifest if row["status"] == "moved")
    failed = sum(1 for row in manifest if row["status"] == "error")
    return {
        "moved": moved,
        "failed": failed,
        "skipped": len(manifest) - moved,
    }


def prepare_quarantine_batch(
    project: Project, batch_id: str
) -> tuple[Path, Path, list[dict[str, Any]], list[_PreparedQuarantine]]:
    """Create the batch directory, seed the manifest and register the batch.

    Split out of :func:`run_quarantine_batch` so the background task can report
    a real ``total`` (and a real batch_id) before any file has moved. The order
    of operations here is unchanged from the original synchronous function.
    """
    preview = quarantine_preview(project)
    batch_root = _quarantine_batch_root(project, batch_id)
    manifest: list[dict[str, Any]] = []
    prepared: list[_PreparedQuarantine] = []
    for item in preview["items"]:
        entry, candidate = _prepare_quarantine_item(project, batch_root, item)
        manifest.append(entry)
        if candidate is not None:
            prepared.append(candidate)
    batch_root.mkdir(parents=True, exist_ok=False)
    manifest_path = batch_root / "manifest.json"
    atomic_write_json(manifest_path, manifest)
    _write_manifest_csv(batch_root, manifest)
    with closing(connect_db(project.db_path)) as conn:
        conn.execute(
            "INSERT INTO quarantine_batches(id,created_at,manifest_path,count,total_size) VALUES(?,?,?,?,?)",
            (batch_id, datetime.now().isoformat(timespec="seconds"), str(manifest_path), 0, 0),
        )
        conn.commit()
    return manifest_path, batch_root, manifest, prepared


def run_quarantine_batch(
    project: Project,
    batch_id: str,
    manifest_path: Path,
    batch_root: Path,
    manifest: list[dict[str, Any]],
    prepared: list[_PreparedQuarantine],
    report: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Move every prepared item into quarantine, reporting progress as it goes.

    The body is the original loop verbatim: same order, same per-item error
    tolerance (one failure records ``status="error"`` and the run continues),
    same manifest flush per item, no rollback. ``report`` is the only addition.
    """
    total = len(prepared)
    report({"current": 0, "total": total, "current_file": "", "moved": 0, "failed": 0})
    with closing(connect_db(project.db_path)) as conn:
        for index, candidate in enumerate(prepared, start=1):
            status = _prepared_quarantine_status(candidate)
            if status:
                candidate.entry["status"] = status
                atomic_write_json(manifest_path, manifest)
            else:
                try:
                    _move_quarantine_assets(candidate.moves)
                except Exception as error:
                    candidate.entry["status"] = "error"
                    candidate.entry["error"] = str(error)
                else:
                    candidate.entry["status"] = "moved"
                atomic_write_json(manifest_path, manifest)
                if candidate.entry["status"] == "moved":
                    _record_quarantined_item(conn, batch_id, manifest, candidate.entry)
            counts = _quarantine_counts(manifest)
            report(
                {
                    "current": index,
                    "total": total,
                    "current_file": str(candidate.entry.get("relative_path") or ""),
                    "moved": counts["moved"],
                    "failed": counts["failed"],
                }
            )
        rebuild_capture_variants(conn, prune_similar=True)
        conn.commit()
    _write_manifest_csv(batch_root, manifest)
    counts = _quarantine_counts(manifest)
    return {"batch_id": batch_id, **counts}


def apply_quarantine(project: Project) -> dict[str, Any]:
    """Apply a quarantine batch synchronously (kept for tests and scripting).

    The HTTP route no longer calls this -- it starts a background task instead
    (see :class:`QuarantineRunner`) -- but the behaviour and the return value are
    unchanged, and the tests drive this entry point directly.
    """
    batch_id = _new_batch_id()
    manifest_path, batch_root, manifest, prepared = prepare_quarantine_batch(
        project, batch_id
    )
    return run_quarantine_batch(
        project,
        batch_id,
        manifest_path,
        batch_root,
        manifest,
        prepared,
        lambda _progress: None,
    )


@dataclass
class _TaskProgress:
    """Mutable progress record for one background quarantine run.

    Deliberately a separate store from :attr:`Scanner.progress`: a scan is
    keyed by project and reports a *stage*, while a quarantine run is keyed by
    batch and reports a *count*. Sharing one dict would have made the two
    overwrite each other's ``stage``/``done`` fields and required a
    discriminated-union payload at every reader. The lookup-and-lock
    discipline is what is worth sharing, and that is inherited from the same
    shape rather than the same object.
    """

    batch_id: str
    stage: str = "preparing"
    current: int = 0
    total: int = 0
    current_file: str = ""
    moved: int = 0
    failed: int = 0
    done: bool = False
    error: str = ""
    # True once at least one file has actually left its original location.
    #
    # 这个字段是为了区分两种「失败」：搬**之前**失败（例如 project_operation
    # 被占、批次目录建不出来）——一个文件都没动，缓存仍然描述真实的库，不该作废；
    # 搬**之后**失败（rebuild_capture_variants / _write_manifest_csv 抛异常）——
    # 文件已经在磁盘上不在库里，缓存若不作废就会长期描述一批不存在的照片。
    # 只看 stage 无法区分：两种情况都是 stage="error"。
    moved_any: bool = False
    result: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "stage": self.stage,
            "current": self.current,
            "total": self.total,
            "current_file": self.current_file,
            "moved": self.moved,
            "failed": self.failed,
            "done": self.done,
            "error": self.error,
            "moved_any": self.moved_any,
            **({"result": self.result} if self.result else {}),
        }


class QuarantineRunner:
    """Runs :func:`apply_quarantine` on a background thread, one run per project.

    Mirrors :class:`Scanner`'s discipline -- a lock-protected dict of progress
    records, a thread per key, and a cheap ``get_progress`` for the poller --
    but keyed by project with at most one live run, because two concurrent
    quarantine runs on one library would race on the manifest and on
    ``rebuild_capture_variants``.
    """

    def __init__(
        self,
        manager: Any,
        operation: Callable[[str, str], ContextManager[Any]],
    ) -> None:
        self._manager = manager
        self._operation = operation
        self._lock = threading.RLock()
        self._progress: dict[str, _TaskProgress] = {}
        self._threads: dict[str, threading.Thread] = {}

    def get_progress(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._progress.get(project_id)
        if record is None:
            return {"stage": "idle", "done": True}
        return record.payload()

    def start(self, project_id: str) -> dict[str, Any]:
        """Start a run and return immediately with its batch id.

        Returns ``{"batch_id", "started"}``. ``started`` is False when a run is
        already in flight for this project, so the caller can tell "already
        working on it" apart from "here is your batch to poll".
        """
        with self._lock:
            existing = self._threads.get(project_id)
            if existing is not None and existing.is_alive():
                return {"batch_id": self._progress[project_id].batch_id, "started": False}
            batch_id = _new_batch_id()
            record = _TaskProgress(batch_id=batch_id)
            self._progress[project_id] = record
            thread = threading.Thread(
                target=self._run, args=(project_id, record), daemon=True
            )
            self._threads[project_id] = thread
        thread.start()
        return {"batch_id": batch_id, "started": True}

    def _update(self, record: _TaskProgress, **values: Any) -> None:
        with self._lock:
            for key, value in values.items():
                setattr(record, key, value)

    def _reporter(self, record: _TaskProgress) -> Callable[[dict[str, Any]], None]:
        """Adapt ``run_quarantine_batch``'s report callback to the record.

        The per-item ``moved`` count is what tells us a file genuinely left its
        original location, so ``moved_any`` is latched from it as the run
        progresses rather than only at the end -- a later bookkeeping failure
        must not be able to hide the fact that files were already moved.
        """

        def report(progress: dict[str, Any]) -> None:
            self._update(record, **progress)
            if progress.get("moved"):
                self._update(record, moved_any=True)

        return report

    def _run(self, project_id: str, record: _TaskProgress) -> None:
        try:
            # 两层锁都必须保留，缺一不可（这是从同步 handler 拆到线程时最容易丢的一层）：
            #   project_operation —— 一个项目同时只做一件事，扫描也要抢它；
            #   data_operation   —— 写入不得与缓存迁移交叉（见 project_store 的
            #                       docstring），apply_profile 与迁移缓存的路由都取它。
            # 后者与前者语义不同，不是冗余：少了它，后台隔离会在 apply_profile
            # 搬迁缓存的同时搬照片。
            with self._operation(project_id, "隔离照片"):
                with self._manager.data_operation(project_id):
                    project = self._manager.from_id(project_id)
                    manifest_path, batch_root, manifest, prepared = (
                        prepare_quarantine_batch(project, record.batch_id)
                    )
                    self._update(record, stage="moving", total=len(prepared))
                    result = run_quarantine_batch(
                        project,
                        record.batch_id,
                        manifest_path,
                        batch_root,
                        manifest,
                        prepared,
                        self._reporter(record),
                    )
                    # Belt and braces: the live report already flipped
                    # moved_any on the first successful move, but derive it from
                    # the final counts too so the flag cannot be left false
                    # after a run that demonstrably moved something.
                    self._update(
                        record, moved_any=record.moved_any or result["moved"] > 0
                    )
            self._update(record, stage="complete", done=True, result=result)
        except Exception as error:
            self._update(
                record,
                stage="error",
                done=True,
                error=str(error) or error.__class__.__name__,
            )

@dataclass(frozen=True)
class _RestorePaths:
    source: Path | None
    destination: Path
    recorded_restore: Path | None
    companion_source: Path | None
    companion_destination: Path | None
    companion_restore: Path | None


def _load_restore_manifest(
    project: Project, batch_root: Path, stored_path: str
) -> tuple[Path, list[dict[str, Any]]]:
    raw_path = Path(stored_path)
    manifest_path = (
        raw_path.resolve()
        if raw_path.is_absolute()
        else safe_relative_path(project.root, str(raw_path), "清单路径")
    )
    if manifest_path.name != "manifest.json" or not is_within(
        manifest_path, batch_root
    ):
        raise ValueError("隔离清单路径无效")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, list) or any(
        not isinstance(item, dict) for item in manifest
    ):
        raise ValueError("隔离清单格式无效")
    return manifest_path, manifest


def _restore_paths(
    project: Project, batch_root: Path, item: dict[str, Any]
) -> _RestorePaths:
    destination = safe_relative_path(
        project.root, item.get("relative_path", ""), "恢复目标路径"
    )
    source = None
    if item.get("quarantine_path"):
        source = safe_relative_path(
            project.root, item["quarantine_path"], "隔离文件路径"
        )
        if not is_within(source, batch_root):
            raise ValueError("隔离文件路径超出当前批次")
    recorded_restore = (
        safe_relative_path(
            project.root, item["restore_path"], "已恢复文件路径"
        )
        if item.get("restore_path")
        else None
    )
    companion_destination = (
        safe_relative_path(
            project.root,
            item["companion_relative_path"],
            "动态照片恢复目标路径",
        )
        if item.get("companion_relative_path")
        else None
    )
    companion_source = None
    if item.get("companion_quarantine_path"):
        companion_source = safe_relative_path(
            project.root,
            item["companion_quarantine_path"],
            "动态照片隔离文件路径",
        )
        if not is_within(companion_source, batch_root):
            raise ValueError("动态照片隔离文件路径超出当前批次")
    companion_restore = (
        safe_relative_path(
            project.root,
            item["companion_restore_path"],
            "动态照片已恢复文件路径",
        )
        if item.get("companion_restore_path")
        else None
    )
    return _RestorePaths(
        source,
        destination,
        recorded_restore,
        companion_source,
        companion_destination,
        companion_restore,
    )


def _conflict_suffix() -> str:
    return f".restored-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


def _renamed_restore_target(path: Path, suffix: str) -> Path:
    return path.with_name(path.stem + suffix + path.suffix)


def _prepare_restore_targets(
    paths: _RestorePaths, status: str
) -> tuple[_RestorePaths, int]:
    if status == "restoring" and paths.recorded_restore is not None:
        resumed = replace(
            paths,
            destination=paths.recorded_restore,
            companion_destination=(
                paths.companion_restore or paths.companion_destination
            ),
        )
        return _resolve_resume_conflicts(resumed)
    primary_conflict = paths.destination.exists()
    companion_conflict = bool(
        paths.companion_destination is not None
        and paths.companion_destination.exists()
    )
    conflicts = int(primary_conflict) + int(companion_conflict)
    if not conflicts:
        return paths, 0
    suffix = _conflict_suffix()
    return (
        replace(
            paths,
            destination=_renamed_restore_target(paths.destination, suffix),
            companion_destination=(
                _renamed_restore_target(paths.companion_destination, suffix)
                if paths.companion_destination is not None
                else None
            ),
        ),
        conflicts,
    )


def _resolve_resume_conflicts(paths: _RestorePaths) -> tuple[_RestorePaths, int]:
    primary_conflict = bool(
        paths.source and paths.source.exists() and paths.destination.exists()
    )
    companion_conflict = bool(
        paths.companion_source
        and paths.companion_source.exists()
        and paths.companion_destination
        and paths.companion_destination.exists()
    )
    conflicts = int(primary_conflict) + int(companion_conflict)
    if not conflicts:
        return paths, 0
    suffix = _conflict_suffix()
    return (
        replace(
            paths,
            destination=(
                _renamed_restore_target(paths.destination, suffix)
                if primary_conflict
                else paths.destination
            ),
            companion_destination=(
                _renamed_restore_target(paths.companion_destination, suffix)
                if companion_conflict and paths.companion_destination is not None
                else paths.companion_destination
            ),
        ),
        conflicts,
    )


def _restore_asset_pairs(paths: _RestorePaths) -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    if paths.source is not None:
        pairs.append((paths.source, paths.destination))
    if paths.companion_source is not None and paths.companion_destination is not None:
        pairs.append((paths.companion_source, paths.companion_destination))
    return pairs


def _restore_assets_available(paths: _RestorePaths) -> bool:
    pairs = _restore_asset_pairs(paths)
    return bool(pairs) and all(source.exists() or target.exists() for source, target in pairs)


def _record_restoring(
    project: Project,
    manifest_path: Path,
    manifest: list[dict[str, Any]],
    item: dict[str, Any],
    paths: _RestorePaths,
) -> None:
    item["status"] = "restoring"
    item["restore_path"] = paths.destination.relative_to(
        project.root.resolve()
    ).as_posix()
    if paths.companion_destination is not None:
        paths.companion_destination.parent.mkdir(parents=True, exist_ok=True)
        item["companion_restore_path"] = paths.companion_destination.relative_to(
            project.root.resolve()
        ).as_posix()
    paths.destination.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(manifest_path, manifest)


def _move_restore_assets(paths: _RestorePaths) -> None:
    moved: list[tuple[Path, Path]] = []
    try:
        for source, destination in _restore_asset_pairs(paths):
            if source.exists():
                if destination.exists():
                    raise FileExistsError(f"恢复目标已存在：{destination}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
            elif not destination.exists():
                raise FileNotFoundError(f"隔离文件不存在：{source}")
            moved.append((source, destination))
    except Exception:
        for source, destination in reversed(moved):
            if destination.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), str(source))
        raise


def _update_restored_photo(
    project: Project,
    conn: sqlite3.Connection,
    item: dict[str, Any],
    paths: _RestorePaths,
) -> None:
    target_rel = paths.destination.relative_to(project.root.resolve()).as_posix()
    companion_rel = (
        paths.companion_destination.relative_to(project.root.resolve()).as_posix()
        if paths.companion_destination is not None
        else ""
    )
    if item.get("photo_id"):
        conn.execute(
            """UPDATE photos SET status='active',relative_path=?,
                      motion_relative_path=CASE WHEN ?<>'' THEN ? ELSE motion_relative_path END
                 WHERE id=?""",
            (target_rel, companion_rel, companion_rel, int(item["photo_id"])),
        )
    else:
        conn.execute(
            "UPDATE photos SET status='active',relative_path=? WHERE relative_path=?",
            (target_rel, item["relative_path"]),
        )
    conn.commit()


def _restore_manifest_item(
    project: Project,
    conn: sqlite3.Connection,
    manifest_path: Path,
    manifest: list[dict[str, Any]],
    item: dict[str, Any],
    paths: _RestorePaths,
) -> tuple[int, int, int]:
    status = str(item.get("status") or "")
    if status == "restored":
        if paths.recorded_restore and paths.recorded_restore.exists():
            restored_paths = replace(
                paths,
                destination=paths.recorded_restore,
                companion_destination=(
                    paths.companion_restore
                    if paths.companion_restore
                    and paths.companion_restore.exists()
                    else None
                ),
            )
            _update_restored_photo(project, conn, item, restored_paths)
        return 0, 0, 0
    if status not in {"moved", "pending", "restoring"}:
        return 0, 0, 0
    if status == "restoring" and paths.recorded_restore is None:
        return 0, 0, 0
    targets, conflicts = _prepare_restore_targets(paths, status)
    if not _restore_assets_available(targets):
        return 0, conflicts, 1
    _record_restoring(project, manifest_path, manifest, item, targets)
    _move_restore_assets(targets)
    item["status"] = "restored"
    item.pop("error", None)
    atomic_write_json(manifest_path, manifest)
    _update_restored_photo(project, conn, item, targets)
    return 1, conflicts, 0


def _restore_files_remain(
    manifest: list[dict[str, Any]], paths: list[_RestorePaths]
) -> bool:
    pending_statuses = {"moved", "pending", "restoring"}
    for item, item_paths in zip(manifest, paths):
        if item.get("status") not in pending_statuses:
            continue
        if any(source.exists() for source, _target in _restore_asset_pairs(item_paths)):
            return True
    return False


def restore_batch(project: Project, batch_id: str) -> dict[str, Any]:
    batch_root = _quarantine_batch_root(project, batch_id)
    with closing(connect_db(project.db_path)) as conn:
        batch = conn.execute("SELECT * FROM quarantine_batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            raise ValueError("隔离批次不存在")
        manifest_path, manifest = _load_restore_manifest(
            project, batch_root, str(batch["manifest_path"])
        )
        paths = [_restore_paths(project, batch_root, item) for item in manifest]
        restored = conflicts = missing = 0
        for item, item_paths in zip(manifest, paths):
            item_restored, item_conflicts, item_missing = _restore_manifest_item(
                project,
                conn,
                manifest_path,
                manifest,
                item,
                item_paths,
            )
            restored += item_restored
            conflicts += item_conflicts
            missing += item_missing
        rebuild_capture_variants(conn, prune_similar=True)
        conn.commit()
        if not _restore_files_remain(manifest, paths):
            conn.execute(
                "UPDATE quarantine_batches SET restored_at=? WHERE id=?",
                (datetime.now().isoformat(timespec="seconds"), batch_id),
            )
            conn.commit()
    _write_manifest_csv(batch_root, manifest)
    return {"restored": restored, "conflicts": conflicts, "missing": missing}
