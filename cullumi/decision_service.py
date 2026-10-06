from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .capture_variants import (
    active_variant_groups,
    active_variant_photo_ids,
    active_variant_rows,
    variant_metadata,
    variant_representative_ids,
)
from .classification import project_photo_counts
from .project_store import Project, connect_db


@dataclass(frozen=True)
class DecisionUpdate:
    photo_id: int
    decision: str
    rows: list[Any]
    previous: dict[int, str]
    variant_extensions: dict[int, list[str]]
    # photo_id -> representative_id, where a photo in no group maps to itself.
    # The caller needs this to tell the UI which affected photos the library
    # folds into one card: a synced decision touches every format in the group,
    # but only the representative occupies a slot on screen.
    variant_representatives: dict[int, int]
    project_counts: dict[str, Any]


def set_photo_decision(
    project: Project,
    photo_id: int,
    decision: str,
    sync_variant_decisions: bool,
) -> DecisionUpdate:
    photo_id = int(photo_id)
    if decision not in {"", "keep", "remove"}:
        raise ValueError("无效决定")
    with closing(connect_db(project.db_path)) as conn:
        source = conn.execute(
            "SELECT id FROM photos WHERE id=? AND status='active'",
            (photo_id,),
        ).fetchone()
        if not source:
            raise ValueError("照片不存在或当前不可用")
        target_ids = active_variant_photo_ids(
            conn, photo_id, sync_variant_decisions
        )
        placeholders = ",".join("?" for _ in target_ids)
        previous = {
            int(row["id"]): str(row["decision"] or "")
            for row in conn.execute(
                f"""SELECT id,decision FROM photos
                      WHERE id IN ({placeholders}) AND status='active'""",
                target_ids,
            )
        }
        conn.execute(
            f"""UPDATE photos SET decision=?
                  WHERE id IN ({placeholders}) AND status='active'""",
            [decision, *target_ids],
        )
        conn.commit()
        rows = conn.execute(
            f"""SELECT * FROM photos
                  WHERE id IN ({placeholders}) AND status='active'
                  ORDER BY id""",
            target_ids,
        ).fetchall()
        extensions = variant_metadata(conn, (int(row["id"]) for row in rows))
        representatives = variant_representative_ids(
            conn, (int(row["id"]) for row in rows)
        )
        counts = project_photo_counts(conn)
    return DecisionUpdate(
        photo_id, decision, rows, previous, extensions, representatives, counts
    )


def _photo_index(conn: Any) -> dict[str, dict[str, Any]]:
    return {
        str(row["relative_path"]): {
            "id": int(row["id"]),
            "decision": str(row["decision"] or ""),
        }
        for row in conn.execute(
            "SELECT id,relative_path,decision FROM photos"
        )
    }


def _parse_decision_csv(
    project: Project,
    csv_path: Path,
    photos: dict[str, dict[str, Any]],
) -> tuple[list[tuple[int, str]], int]:
    entries: list[tuple[int, str]] = []
    missing = 0
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            decision = row.get("决定") or row.get("decision") or ""
            raw_path = (row.get("路径") or row.get("path") or "").replace(
                "\\", "/"
            )
            if decision not in {"keep", "remove"}:
                continue
            prefix = project.root.name + "/"
            candidates = [raw_path]
            if raw_path.startswith(prefix):
                candidates.append(raw_path[len(prefix) :])
            found = next(
                (photos[relative] for relative in candidates if relative in photos),
                None,
            )
            if found is None:
                missing += 1
            else:
                entries.append((int(found["id"]), decision))
    return entries, missing


def _conflicting_variant_groups(
    entries: list[tuple[int, str]], memberships: dict[int, int]
) -> int:
    decisions_by_group: dict[int, set[str]] = {}
    for photo_id, decision in entries:
        representative_id = memberships.get(photo_id)
        if representative_id is not None:
            decisions_by_group.setdefault(representative_id, set()).add(
                decision
            )
    return sum(len(decisions) > 1 for decisions in decisions_by_group.values())


def _decision_assignments(
    entries: list[tuple[int, str]],
    memberships: dict[int, int],
    groups: dict[int, list[Any]],
    sync_variant_decisions: bool,
) -> dict[int, str]:
    assignments: dict[int, str] = {}
    for photo_id, decision in entries:
        representative_id = memberships.get(photo_id)
        if sync_variant_decisions and representative_id is not None:
            assignments.update(
                (int(member["photo_id"]), decision)
                for member in groups.get(representative_id, [])
            )
        else:
            assignments[photo_id] = decision
    return assignments


def import_decisions(
    project: Project,
    csv_path: Path,
    sync_variant_decisions: bool = False,
    *,
    detailed: bool = False,
) -> dict[str, int | bool]:
    with closing(connect_db(project.db_path)) as conn:
        photos = _photo_index(conn)
        entries, missing = _parse_decision_csv(project, csv_path, photos)
        memberships, groups = active_variant_groups(conn)
        conflicting_groups = (
            _conflicting_variant_groups(entries, memberships)
            if sync_variant_decisions
            else 0
        )
        if conflicting_groups:
            return {
                "imported": 0,
                "matched": len(entries),
                "missing": missing,
                "affected": 0,
                "requires_sync_disable": True,
                "conflicting_groups": conflicting_groups,
            }
        assignments = _decision_assignments(
            entries, memberships, groups, sync_variant_decisions
        )
        previous = {
            int(data["id"]): str(data["decision"])
            for data in photos.values()
            if int(data["id"]) in assignments
        }
        conn.executemany(
            "UPDATE photos SET decision=? WHERE id=?",
            [
                (decision, photo_id)
                for photo_id, decision in assignments.items()
            ],
        )
        conn.commit()
    result: dict[str, int | bool] = {
        "imported": len(entries),
        "matched": len(entries),
        "missing": missing,
        "affected": sum(
            previous.get(photo_id, "") != decision
            for photo_id, decision in assignments.items()
        ),
        "requires_sync_disable": False,
        "conflicting_groups": 0,
    }
    if not detailed and not sync_variant_decisions:
        return {"imported": len(entries), "missing": missing}
    return result


def export_decisions(project: Project) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["决定", "路径", "建议", "原因"])
    with closing(connect_db(project.db_path)) as conn:
        rows = conn.execute(
            """SELECT decision,relative_path,suggestion,reason FROM photos
                WHERE decision<>'' ORDER BY relative_path"""
        )
        for row in rows:
            writer.writerow(
                [
                    row["decision"],
                    row["relative_path"],
                    row["suggestion"],
                    row["reason"],
                ]
            )
    return "\ufeff" + output.getvalue()


def clear_decisions(project: Project) -> int:
    with closing(connect_db(project.db_path)) as conn:
        cursor = conn.execute(
            "UPDATE photos SET decision='' WHERE status='active' AND decision<>''"
        )
        conn.commit()
        return cursor.rowcount


def _expand_batch_assignments(
    conn: Any,
    seeds: Iterable[tuple[int, str]],
    sync_variant_decisions: bool,
    *,
    readable_only: bool,
) -> tuple[dict[int, str], int]:
    """Expand batch decisions without overwriting a conflicting decision."""
    requested: dict[int, str] = {}
    for photo_id, decision in seeds:
        requested[int(photo_id)] = decision
    if not requested:
        return {}, 0

    readable_clause = (
        " AND COALESCE(error,'')=''"
        " AND COALESCE(suggestion,'keep')<>'unreadable'"
        if readable_only
        else ""
    )
    eligible = {
        int(row["id"])
        for row in conn.execute(
            f"""SELECT id FROM photos
                WHERE status='active' AND decision='' {readable_clause}"""
        )
    }
    if not sync_variant_decisions:
        return (
            {
                photo_id: decision
                for photo_id, decision in requested.items()
                if photo_id in eligible
            },
            0,
        )

    memberships, groups = active_variant_groups(conn)
    direct: dict[int, str] = {}
    group_decisions: dict[int, str] = {}
    conflicting_groups: set[int] = set()
    for photo_id, decision in requested.items():
        representative_id = memberships.get(photo_id)
        if representative_id is None:
            direct[photo_id] = decision
            continue
        if representative_id in conflicting_groups:
            continue
        previous = group_decisions.get(representative_id)
        if previous is not None and previous != decision:
            conflicting_groups.add(representative_id)
        else:
            group_decisions[representative_id] = decision

    assignments = {
        photo_id: decision
        for photo_id, decision in direct.items()
        if photo_id in eligible
    }
    for representative_id, decision in group_decisions.items():
        if representative_id in conflicting_groups:
            continue
        members = groups.get(representative_id, [])
        if any(
            str(member["decision"] or "") not in {"", decision}
            for member in members
        ):
            conflicting_groups.add(representative_id)
            continue
        assignments.update(
            (
                int(member["photo_id"]),
                decision,
            )
            for member in members
            if int(member["photo_id"]) in eligible
        )
    return assignments, len(conflicting_groups)


def _similarity_seeds(
    conn: Any,
    groups: Iterable[dict[str, Any]],
) -> list[tuple[int, str]]:
    groups = list(groups)
    variants = active_variant_rows(
        conn,
        (
            int(row["id"])
            for group in groups
            if group.get("kind") == "similar"
            for row in group.get("members", [])
        ),
    )
    seeds: list[tuple[int, str]] = []
    for group in groups:
        members = list(group.get("members", []))
        by_id = {int(row["id"]): row for row in members}
        recommended_id = int(group["recommended_id"])
        priority = [
            recommended_id,
            *[
                int(row["id"])
                for row in members
                if int(row["id"]) != recommended_id
            ],
        ]
        group_variants = variants if group.get("kind") == "similar" else {}
        owners: dict[int, int] = {}
        for source_id in priority:
            for row in group_variants.get(source_id) or [by_id[source_id]]:
                owners.setdefault(int(row["id"]), source_id)
        for source_id in priority:
            for row in group_variants.get(source_id) or [by_id[source_id]]:
                photo_id = int(row["id"])
                if owners.get(photo_id) != source_id:
                    continue
                seeds.append(
                    (photo_id, "keep" if source_id == recommended_id else "remove")
                )
    return seeds


def accept_decisions(
    project: Project,
    scope: str,
    remove_review_on_accept: bool = False,
    sync_variant_decisions: bool = True,
    *,
    similarity_groups: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Accept pending suggestions or similarity recommendations in one batch."""
    if scope not in {"library", "undecided", "ai", "similar"}:
        raise ValueError("scope 必须是 library、undecided、ai 或 similar")
    with closing(connect_db(project.db_path)) as conn:
        if scope == "similar":
            if similarity_groups is None:
                raise ValueError("相似照片组不存在或已发生变化")
            seeds = _similarity_seeds(conn, similarity_groups)
        else:
            where = [
                "status='active'",
                "COALESCE(error,'')=''",
                "COALESCE(suggestion,'keep')<>'unreadable'",
                "suggestion IN ('remove','review')",
                "decision=''",
            ]
            rows = conn.execute(
                f"SELECT id,suggestion FROM photos WHERE {' AND '.join(where)}",
            ).fetchall()
            seeds = [
                (int(row["id"]), "remove")
                for row in rows
                if row["suggestion"] != "review" or remove_review_on_accept
            ]

        assignments, skipped = _expand_batch_assignments(
            conn,
            seeds,
            sync_variant_decisions,
            readable_only=True,
        )
        conn.executemany(
            """UPDATE photos SET decision=?
                WHERE id=? AND status='active' AND decision=''""",
            [
                (decision, photo_id)
                for photo_id, decision in sorted(assignments.items())
            ],
        )
        conn.commit()
        counts = project_photo_counts(conn)
    kept = sum(decision == "keep" for decision in assignments.values())
    removed = sum(decision == "remove" for decision in assignments.values())
    return {
        "marked": kept + removed,
        "kept": kept,
        "removed": removed,
        "skipped_conflicting_groups": skipped,
        "project_counts": counts,
    }


def mark_ai_remove_suggestions(
    project: Project,
    sync_variant_decisions: bool = False,
    *,
    detailed: bool = False,
) -> int | dict[str, int]:
    with closing(connect_db(project.db_path)) as conn:
        source_ids = [
            int(row["id"])
            for row in conn.execute(
                """SELECT id FROM photos
                    WHERE status='active' AND COALESCE(error,'')=''
                      AND suggestion='remove' AND decision=''"""
            )
        ]
        assignments, skipped_groups = _expand_batch_assignments(
            conn,
            ((photo_id, "remove") for photo_id in source_ids),
            sync_variant_decisions,
            readable_only=False,
        )
        conn.executemany(
            """UPDATE photos SET decision='remove'
                WHERE id=? AND status='active' AND decision=''""",
            [(photo_id,) for photo_id in sorted(assignments)],
        )
        conn.commit()
    result = {
        "marked": len(assignments),
        "source_candidates": len(source_ids),
        "skipped_kept_groups": skipped_groups,
    }
    return result if detailed or sync_variant_decisions else result["marked"]


__all__ = [
    "DecisionUpdate",
    "clear_decisions",
    "accept_decisions",
    "export_decisions",
    "import_decisions",
    "mark_ai_remove_suggestions",
    "set_photo_decision",
]
