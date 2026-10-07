from __future__ import annotations

import copy
import csv
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest import mock

from PIL import Image

from cullumi import http_api, media, project_store
from cullumi.capture_variants import (
    active_variant_rows,
    format_category_counts,
    rebuild_capture_variants,
    variant_metadata,
    variant_metadata_from_rows,
)
from cullumi.classification import project_photo_counts
from cullumi.config import BUILTIN_PROFILES, ConfigStore
from cullumi.media import (
    DISPLAY_PREVIEW_MAX_SIZE,
    display_preview_path,
    ensure_display_preview,
)
from cullumi.photo_query_service import (
    VIEWER_PREVIEW_WIDTH,
    PhotoQueryService,
)
from cullumi.project_store import ProjectManager, connect_db
from cullumi.scanner import Scanner
from cullumi.similarity import SimilarityGroupCache
from cullumi.workflows import (
    apply_quarantine,
    clear_decisions,
    export_decisions,
    import_decisions,
    mark_ai_remove_suggestions,
    restore_batch,
    set_photo_decision,
)


def insert_photo(
    conn,
    relative_path: str,
    *,
    error: str = "",
    decision: str = "",
    suggestion: str = "keep",
    width: int = 4000,
    height: int = 3000,
    size: int = 2_000_000,
    taken: str = "2026:01:01 12:00:00",
    phash: str = "0" * 16,
    dhash: str = "0" * 16,
    sha256: str = "",
) -> int:
    extension = Path(relative_path).suffix.lower()
    cursor = conn.execute(
        """INSERT INTO photos(
             relative_path,extension,size,width,height,megapixels,taken,
             luminance,contrast,dark_clip,bright_clip,sharpness,entropy,
             phash,dhash,sha256,thumbnail,error,suggestion,reason,decision,
             status,analyzed_at,media_type,quality_score
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            relative_path,
            extension,
            size,
            width,
            height,
            width * height / 1_000_000,
            taken,
            110.0,
            60.0,
            0.01,
            0.01,
            900.0,
            7.0,
            phash,
            dhash,
            sha256,
            "",
            error,
            suggestion,
            "",
            decision,
            "active",
            "2026-01-01T12:00:00",
            "image",
            80.0,
        ),
    )
    return int(cursor.lastrowid)


class CaptureVariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.photos = self.root / "photos"
        self.photos.mkdir()
        self.config = ConfigStore(self.root / "config.json")
        self.config.data["default_cache_root"] = str(self.root / "cache")
        self.config.save()
        self.manager = ProjectManager(self.config)
        self.project = self.manager.open(str(self.photos))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def application(self) -> http_api.ApplicationContext:
        scanner = Scanner(self.config, self.manager)
        return http_api.ApplicationContext(
            self.config,
            self.manager,
            scanner,
            SimilarityGroupCache(),
            "test-token",
            Path("web"),
        )

    def test_strict_grouping_and_representative_selection(self) -> None:
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "Trip/IMG_0001.JPG", size=3_000_000)
            raw = insert_photo(conn, "Trip/img_0001.CR3", size=20_000_000)
            # Same stem, different directory, and a capture time months away:
            # a name clash between two events, which must not pair.
            other_dir = insert_photo(
                conn, "Other/IMG_0001.NEF", taken="2025:03:04 09:15:00"
            )
            jpeg_only = insert_photo(conn, "Trip/IMG_0002.JPG")
            heif_only = insert_photo(conn, "Trip/IMG_0002.HEIF")
            raw_only_a = insert_photo(conn, "Trip/IMG_0003.CR2")
            raw_only_b = insert_photo(conn, "Trip/IMG_0003.NEF")
            lower_res_jpg = insert_photo(
                conn, "Trip/IMG_0004.JPG", width=2000, height=1500
            )
            higher_res_png = insert_photo(
                conn, "Trip/IMG_0004.PNG", width=6000, height=4000
            )
            unreadable_raw = insert_photo(
                conn, "Trip/IMG_0004.ARW", error="broken"
            )
            readable_raw = insert_photo(conn, "Trip/img_0004.NEF")
            unreadable_jpg = insert_photo(
                conn,
                "Trip/IMG_0005.JPG",
                error="broken",
                width=1000,
                height=750,
            )
            larger_unreadable_raw = insert_photo(
                conn,
                "Trip/IMG_0005.CR3",
                error="broken",
                width=8000,
                height=6000,
            )
            unreadable_png = insert_photo(
                conn,
                "Trip/IMG_0006.PNG",
                error="broken",
                width=8000,
                height=6000,
            )
            readable_group_raw = insert_photo(
                conn,
                "Trip/IMG_0006.RAF",
                width=1000,
                height=750,
            )

            memberships = rebuild_capture_variants(conn)
            conn.commit()

        self.assertEqual(memberships[jpg], jpg)
        self.assertEqual(memberships[raw], jpg)
        self.assertNotIn(other_dir, memberships)
        for photo_id in (jpeg_only, heif_only, raw_only_a, raw_only_b):
            self.assertNotIn(photo_id, memberships)
        self.assertEqual(memberships[lower_res_jpg], higher_res_png)
        self.assertEqual(memberships[higher_res_png], higher_res_png)
        self.assertEqual(memberships[unreadable_raw], higher_res_png)
        self.assertEqual(memberships[readable_raw], higher_res_png)
        self.assertEqual(memberships[unreadable_jpg], unreadable_jpg)
        self.assertEqual(memberships[larger_unreadable_raw], unreadable_jpg)
        self.assertEqual(memberships[unreadable_png], readable_group_raw)
        self.assertEqual(memberships[readable_group_raw], readable_group_raw)

    def test_visual_similarity_uses_one_representative_per_capture(self) -> None:
        with closing(connect_db(self.project.db_path)) as conn:
            first_jpg = insert_photo(conn, "IMG_0001.JPG")
            first_raw = insert_photo(conn, "IMG_0001.CR3", size=20_000_000)
            second_jpg = insert_photo(conn, "IMG_0002.JPG")
            second_raw = insert_photo(conn, "IMG_0002.CR3", size=20_000_000)
            rebuild_capture_variants(conn)
            conn.commit()

            profile = copy.deepcopy(BUILTIN_PROFILES["balanced"])
            profile["similarity"].update(
                {
                    "exact_duplicates": False,
                    "phash_max": 64,
                    "dhash_max": 64,
                    "structure_min": -1,
                    "allow_cross_time_high_confidence": True,
                }
            )
            Scanner(self.config, self.manager).rebuild_similarity(
                self.project, conn, profile
            )
            pairs = conn.execute(
                "SELECT a_id,b_id,kind FROM similar_pairs ORDER BY a_id,b_id"
            ).fetchall()

        self.assertEqual(
            [(int(row["a_id"]), int(row["b_id"]), row["kind"]) for row in pairs],
            [(first_jpg, second_jpg, "similar")],
        )
        self.assertNotIn(first_raw, {int(pairs[0]["a_id"]), int(pairs[0]["b_id"])})
        self.assertNotIn(second_raw, {int(pairs[0]["a_id"]), int(pairs[0]["b_id"])})

        application = self.application()
        listing = application.photo_queries.similar_groups(
            {"project_id": [self.project.project_id]}
        )
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["items"][0]["capture_count"], 2)
        # One card per exposure, exactly like the library: the RAW is folded
        # into its JPEG here too, so the group counts the two JPEGs and the
        # CR3 files never reach the screen.
        self.assertEqual(listing["items"][0]["count"], 2)
        detail = application.photo_queries.similar_group(
            {
                "project_id": [self.project.project_id],
                "group_id": [listing["items"][0]["id"]],
            }
        )
        self.assertEqual(detail["capture_count"], 2)
        self.assertEqual(detail["count"], 2)
        self.assertEqual(
            {int(item["id"]) for item in detail["members"]},
            {first_jpg, second_jpg},
        )
        self.assertEqual(
            {item["format_category"] for item in detail["members"]}, {"jpeg"}
        )
        # Folding the RAW away must not hide that it exists: the card keeps
        # announcing the CR3 sitting behind it.
        self.assertEqual(
            [item["variant_extensions"] for item in detail["members"]],
            [["CR3", "JPG"], ["CR3", "JPG"]],
        )
        # ...and it must not orphan it either. A decision taken on the card
        # still writes through to the RAW, so no file is ever disposed of
        # without the user having been shown it.
        set_photo_decision(self.project, first_jpg, "remove", True)
        with closing(connect_db(self.project.db_path)) as conn:
            decisions = {
                int(row["id"]): str(row["decision"] or "")
                for row in conn.execute(
                    "SELECT id,decision FROM photos WHERE id IN (?,?)",
                    (first_jpg, first_raw),
                )
            }
        self.assertEqual(
            decisions, {first_jpg: "remove", first_raw: "remove"}
        )

    def test_exact_duplicates_still_include_nonrepresentative_files(self) -> None:
        with closing(connect_db(self.project.db_path)) as conn:
            insert_photo(conn, "IMG_0001.JPG", sha256="rendered-jpeg")
            raw = insert_photo(
                conn, "IMG_0001.CR3", size=20_000_000, sha256="raw-bytes"
            )
            raw_copy = insert_photo(
                conn,
                "Copies/RAW_COPY.CR3",
                size=20_000_000,
                sha256="raw-bytes",
            )
            rebuild_capture_variants(conn)
            conn.commit()

            Scanner(self.config, self.manager).rebuild_similarity(
                self.project,
                conn,
                copy.deepcopy(BUILTIN_PROFILES["balanced"]),
            )
            exact_pairs = {
                (int(row["a_id"]), int(row["b_id"]))
                for row in conn.execute(
                    "SELECT a_id,b_id FROM similar_pairs WHERE kind='exact'"
                )
            }

        self.assertEqual(exact_pairs, {tuple(sorted((raw, raw_copy)))})

    def test_large_jpg_raw_library_has_no_internal_visual_edges(self) -> None:
        with closing(connect_db(self.project.db_path)) as conn:
            for index in range(250):
                visual_hash = f"{index + 1:016x}"
                insert_photo(
                    conn,
                    f"Shoot/IMG_{index:04d}.JPG",
                    phash=visual_hash,
                    dhash=visual_hash,
                )
                insert_photo(
                    conn,
                    f"Shoot/IMG_{index:04d}.CR3",
                    size=20_000_000,
                    phash=visual_hash,
                    dhash=visual_hash,
                )
            rebuild_capture_variants(conn)
            conn.commit()

            profile = copy.deepcopy(BUILTIN_PROFILES["balanced"])
            profile["similarity"].update(
                {
                    "exact_duplicates": False,
                    "phash_max": 0,
                    "dhash_max": 0,
                    "allow_cross_time_high_confidence": True,
                }
            )
            Scanner(self.config, self.manager).rebuild_similarity(
                self.project, conn, profile
            )
            similar_count = conn.execute(
                "SELECT COUNT(*) FROM similar_pairs WHERE kind='similar'"
            ).fetchone()[0]

        self.assertEqual(similar_count, 0)

    def test_manual_decision_sync_can_be_disabled(self) -> None:
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_0042.JPG", suggestion="review")
            raw = insert_photo(conn, "IMG_0042.CR3", suggestion="review")
            rebuild_capture_variants(conn)
            conn.commit()

        handler = object.__new__(http_api.Handler)
        handler._send_json = mock.Mock()
        with mock.patch.object(http_api, "APPLICATION", self.application()):
            handler.api_decision(
                {
                    "project_id": self.project.project_id,
                    "photo_id": raw,
                    "decision": "remove",
                }
            )
            payload = handler._send_json.call_args.args[0]

        self.assertEqual(
            {item["id"] for item in payload["affected_photos"]}, {jpg, raw}
        )
        self.assertTrue(
            all(item["previous_decision"] == "" for item in payload["affected_photos"])
        )
        with closing(connect_db(self.project.db_path)) as conn:
            self.assertEqual(
                {row["decision"] for row in conn.execute("SELECT decision FROM photos")},
                {"remove"},
            )

        handler._send_json.reset_mock()
        with mock.patch.object(http_api, "APPLICATION", self.application()):
            handler.api_decision(
                {
                    "project_id": self.project.project_id,
                    "photo_id": raw,
                    "decision": "",
                }
            )
        payload = handler._send_json.call_args.args[0]
        self.assertTrue(
            all(
                item["previous_decision"] == "remove"
                for item in payload["affected_photos"]
            )
        )
        with closing(connect_db(self.project.db_path)) as conn:
            self.assertEqual(
                {row["decision"] for row in conn.execute("SELECT decision FROM photos")},
                {""},
            )

        handler._send_json.reset_mock()
        with mock.patch.object(http_api, "APPLICATION", self.application()):
            handler.api_decision(
                {
                    "project_id": self.project.project_id,
                    "photo_id": raw,
                    "decision": "remove",
                }
            )

        with self.config.edit() as data:
            data["sync_variant_decisions"] = False
        handler._send_json.reset_mock()
        with mock.patch.object(http_api, "APPLICATION", self.application()):
            handler.api_decision(
                {
                    "project_id": self.project.project_id,
                    "photo_id": raw,
                    "decision": "keep",
                }
            )
        payload = handler._send_json.call_args.args[0]
        self.assertEqual([item["id"] for item in payload["affected_photos"]], [raw])
        with closing(connect_db(self.project.db_path)) as conn:
            decisions = {
                int(row["id"]): row["decision"]
                for row in conn.execute("SELECT id,decision FROM photos")
            }
        self.assertEqual(decisions, {jpg: "remove", raw: "keep"})

    def test_ai_batch_does_not_override_a_kept_variant(self) -> None:
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_0100.JPG", suggestion="remove")
            raw = insert_photo(conn, "IMG_0100.NEF", decision="keep")
            rebuild_capture_variants(conn)
            conn.commit()

        skipped = mark_ai_remove_suggestions(
            self.project, True, detailed=True
        )
        self.assertEqual(skipped["marked"], 0)
        self.assertEqual(skipped["skipped_kept_groups"], 1)

        with closing(connect_db(self.project.db_path)) as conn:
            conn.execute("UPDATE photos SET decision='' WHERE id=?", (raw,))
            conn.commit()
        marked = mark_ai_remove_suggestions(self.project, True, detailed=True)
        self.assertEqual(marked["marked"], 2)
        with closing(connect_db(self.project.db_path)) as conn:
            decisions = {
                int(row["id"]): row["decision"]
                for row in conn.execute("SELECT id,decision FROM photos")
            }
        self.assertEqual(decisions, {jpg: "remove", raw: "remove"})

    def test_accept_library_respects_review_setting(self) -> None:
        with closing(connect_db(self.project.db_path)) as conn:
            remove = insert_photo(conn, "IMG_0400.JPG", suggestion="remove")
            review = insert_photo(conn, "IMG_0401.JPG", suggestion="review")
            keep = insert_photo(conn, "IMG_0402.JPG", suggestion="keep")
            conn.commit()

        handler = object.__new__(http_api.Handler)
        handler._send_json = mock.Mock()
        with mock.patch.object(http_api, "APPLICATION", self.application()):
            handler.api_decision_accept(
                {"project_id": self.project.project_id, "scope": "library"}
            )
        result = handler._send_json.call_args.args[0]
        self.assertEqual(result["marked"], 1)
        self.assertEqual(result["kept"], 0)
        self.assertEqual(result["removed"], 1)
        with closing(connect_db(self.project.db_path)) as conn:
            decisions = {
                int(row["id"]): row["decision"]
                for row in conn.execute("SELECT id,decision FROM photos")
            }
        self.assertEqual(
            decisions,
            {remove: "remove", review: "", keep: ""},
        )

        with self.config.edit() as data:
            data["remove_review_on_accept"] = True
        handler._send_json.reset_mock()
        with mock.patch.object(http_api, "APPLICATION", self.application()):
            handler.api_decision_accept(
                {"project_id": self.project.project_id, "scope": "undecided"}
            )
        result = handler._send_json.call_args.args[0]
        self.assertEqual(result["marked"], 1)
        self.assertEqual(result["kept"], 0)
        self.assertEqual(result["removed"], 1)
        with closing(connect_db(self.project.db_path)) as conn:
            decisions = {
                int(row["id"]): row["decision"]
                for row in conn.execute("SELECT id,decision FROM photos")
            }
        self.assertEqual(
            decisions,
            {remove: "remove", review: "remove", keep: ""},
        )

        with closing(connect_db(self.project.db_path)) as conn:
            conn.execute("UPDATE photos SET decision='' WHERE id IN (?,?)", (remove, review))
            conn.commit()
        handler._send_json.reset_mock()
        with mock.patch.object(http_api, "APPLICATION", self.application()):
            handler.api_decision_accept(
                {"project_id": self.project.project_id, "scope": "ai"}
            )
        result = handler._send_json.call_args.args[0]
        self.assertEqual(result["marked"], 2)
        self.assertEqual(result["kept"], 0)
        self.assertEqual(result["removed"], 2)
        with closing(connect_db(self.project.db_path)) as conn:
            decisions = {
                int(row["id"]): row["decision"]
                for row in conn.execute("SELECT id,decision FROM photos")
            }
        self.assertEqual(
            decisions,
            {remove: "remove", review: "remove", keep: ""},
        )

    def test_accept_similar_assigns_variants_and_protects_conflicts(self) -> None:
        with closing(connect_db(self.project.db_path)) as conn:
            recommended = insert_photo(
                conn, "IMG_0500.JPG", taken="2026:01:01 12:00:00"
            )
            recommended_raw = insert_photo(
                conn,
                "IMG_0500.CR3",
                size=20_000_000,
                taken="2026:01:01 12:00:00",
            )
            other = insert_photo(
                conn, "IMG_0501.JPG", taken="2026:01:01 12:00:01"
            )
            other_raw = insert_photo(
                conn,
                "IMG_0501.CR3",
                size=20_000_000,
                taken="2026:01:01 12:00:01",
            )
            rebuild_capture_variants(conn)
            conn.execute(
                """INSERT INTO similar_pairs(
                     a_id,b_id,score,kind,recommended_id,face_safe
                   ) VALUES(?,?,?,?,?,?)""",
                (recommended, other, 0.95, "similar", recommended, 0),
            )
            conn.execute("UPDATE photos SET decision='keep' WHERE id=?", (other,))
            conn.commit()

        application = self.application()
        with closing(connect_db(self.project.db_path)) as conn:
            group = application.similarity_groups.get(
                self.project.project_id,
                conn,
                self.config.get_profile(self.project.profile_id),
            )[0]
        handler = object.__new__(http_api.Handler)
        handler._send_json = mock.Mock()
        with mock.patch.object(http_api, "APPLICATION", application):
            handler.api_decision_accept(
                {
                    "project_id": self.project.project_id,
                    "scope": "similar",
                    "group_id": group["id"],
                }
            )
        result = handler._send_json.call_args.args[0]
        self.assertEqual(result["skipped_conflicting_groups"], 1)
        self.assertEqual(result["kept"], 2)
        with closing(connect_db(self.project.db_path)) as conn:
            decisions = {
                int(row["id"]): row["decision"]
                for row in conn.execute("SELECT id,decision FROM photos")
            }
        self.assertEqual(
            decisions,
            {
                recommended: "keep",
                recommended_raw: "keep",
                other: "keep",
                other_raw: "",
            },
        )

        with closing(connect_db(self.project.db_path)) as conn:
            conn.execute("UPDATE photos SET decision='' WHERE id=?", (other,))
            conn.commit()
        handler._send_json.reset_mock()
        with mock.patch.object(http_api, "APPLICATION", application):
            handler.api_decision_accept(
                {
                    "project_id": self.project.project_id,
                    "scope": "similar",
                    "group_id": group["id"],
                }
            )
        result = handler._send_json.call_args.args[0]
        self.assertEqual(result["removed"], 2)
        with closing(connect_db(self.project.db_path)) as conn:
            self.assertEqual(
                {
                    row["decision"]
                    for row in conn.execute("SELECT decision FROM photos")
                },
                {"keep", "remove"},
            )

    def test_csv_variant_conflict_requires_disabling_sync_before_writes(self) -> None:
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_0200.JPG")
            raw = insert_photo(conn, "IMG_0200.ARW")
            rebuild_capture_variants(conn)
            conn.commit()
        csv_path = self.root / "decisions.csv"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["decision", "path"])
            writer.writerow(["keep", "IMG_0200.JPG"])
            writer.writerow(["remove", "IMG_0200.ARW"])

        conflict = import_decisions(
            self.project, csv_path, True, detailed=True
        )
        self.assertTrue(conflict["requires_sync_disable"])
        self.assertEqual(conflict["conflicting_groups"], 1)
        with closing(connect_db(self.project.db_path)) as conn:
            self.assertEqual(
                {row["decision"] for row in conn.execute("SELECT decision FROM photos")},
                {""},
            )

        imported = import_decisions(
            self.project, csv_path, False, detailed=True
        )
        self.assertFalse(imported["requires_sync_disable"])
        with closing(connect_db(self.project.db_path)) as conn:
            decisions = {
                int(row["id"]): row["decision"]
                for row in conn.execute("SELECT id,decision FROM photos")
            }
        self.assertEqual(decisions, {jpg: "keep", raw: "remove"})

    def test_format_filter_and_variant_payload_are_bulk_hydrated(self) -> None:
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_0300.JPG")
            raw = insert_photo(conn, "IMG_0300.CR2")
            insert_photo(conn, "standalone.PNG")
            rebuild_capture_variants(conn)
            conn.commit()
            variants = variant_metadata(conn, [jpg, raw])
        self.assertEqual(variants[jpg], ["CR2", "JPG"])
        self.assertEqual(variants[raw], ["CR2", "JPG"])

        application = self.application()
        result = application.photo_queries.photos(
            {
                "project_id": [self.project.project_id],
                "formats": ["raw"],
                "decisions": ["all"],
                "ai_states": ["all"],
            }
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["id"], raw)
        self.assertEqual(result["items"][0]["format_category"], "raw")
        self.assertEqual(result["items"][0]["variant_extensions"], ["CR2", "JPG"])

        combined = application.photo_queries.photos(
            {
                "project_id": [self.project.project_id],
                "formats": ["raw,jpeg"],
                "decisions": ["all"],
                "ai_states": ["all"],
                "search": ["IMG_0300"],
                "limit": ["1"],
                "offset": ["1"],
                # Opt out of folding so this keeps testing what it was written
                # to test -- that the hydrated payload paginates over BOTH
                # formats of one exposure. The folded listing is asserted
                # separately in test_library_folds_one_card_per_capture_variant.
                "collapse_variants": ["0"],
            }
        )
        self.assertEqual(combined["total"], 2)
        self.assertEqual(len(combined["items"]), 1)
        empty = application.photo_queries.photos(
            {
                "project_id": [self.project.project_id],
                "formats": ["none"],
                "decisions": ["all"],
                "ai_states": ["all"],
            }
        )
        self.assertEqual(empty, {"total": 0, "items": []})

    def test_library_folds_one_card_per_capture_variant(self) -> None:
        """A RAW/JPEG pair must occupy a single card, keeping the JPEG's badge.

        The representative's payload keeps `variant_extensions`, so the card
        still shows the "关联格式" badge telling the user a RAW exists. The
        opt-out parameter returns both rows for callers that genuinely need
        every file.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_0700.JPG")
            raw = insert_photo(conn, "IMG_0700.CR3", size=20_000_000)
            standalone = insert_photo(conn, "SOLO.PNG")
            rebuild_capture_variants(conn)
            conn.commit()

        application = self.application()

        def listed(**overrides) -> dict:
            query = {
                "project_id": [self.project.project_id],
                "decisions": ["all"],
                "ai_states": ["all"],
            }
            for key, value in overrides.items():
                query[key] = [value]
            return application.photo_queries.photos(query)

        folded = listed()
        self.assertEqual(folded["total"], 2)
        self.assertEqual({item["id"] for item in folded["items"]}, {jpg, standalone})
        card = next(item for item in folded["items"] if item["id"] == jpg)
        self.assertEqual(card["variant_extensions"], ["CR3", "JPG"])
        self.assertFalse(card["is_capture_variant"])

        # The three categories that must NOT be folded away: a photo in no
        # variant group, and the two members of a similarity group. Byte-level
        # duplicates live in the same table and are covered by
        # test_library_folding_never_hides_duplicates_or_similarity_groups.
        expanded = listed(collapse_variants="0")
        self.assertEqual(expanded["total"], 3)
        self.assertEqual(
            {item["id"] for item in expanded["items"]}, {jpg, raw, standalone}
        )

    def test_library_folding_follows_the_active_filters(self) -> None:
        """Folding must never make a photo unreachable.

        Whether a photo may fold is a function of the filter, not just of the
        variant table: filter down to RAW and the JPEG representative leaves
        the result set, so the RAW has to become the card instead of vanishing
        with it. The same holds for a search or a decision filter that
        separates the two formats.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_0710.JPG")
            raw = insert_photo(conn, "IMG_0710.CR3", size=20_000_000)
            rebuild_capture_variants(conn)
            conn.commit()

        application = self.application()

        def listed(**overrides) -> list[int]:
            query = {
                "project_id": [self.project.project_id],
                "decisions": ["all"],
                "ai_states": ["all"],
            }
            for key, value in overrides.items():
                query[key] = [value]
            return [
                int(item["id"])
                for item in application.photo_queries.photos(query)["items"]
            ]

        # RAW-only: the JPEG is filtered out, so the RAW must be shown.
        self.assertEqual(listed(formats="raw"), [raw])
        self.assertEqual(listed(formats="jpeg"), [jpg])
        # A search that only matches the folded member's filename.
        self.assertEqual(listed(search="IMG_0710.CR3"), [raw])
        # A decision filter that separates the pair. sync_variant_decisions is
        # a UI setting, not a query one, so a library can hold exactly this
        # state; folding the RAW here would delete it from the UI entirely.
        with closing(connect_db(self.project.db_path)) as conn:
            conn.execute(
                "UPDATE photos SET decision='keep' WHERE id=?", (jpg,)
            )
            conn.commit()
        self.assertEqual(listed(decisions="undecided"), [raw])
        self.assertEqual(listed(decisions="keep"), [jpg])

    def test_library_folding_never_hides_duplicates_or_similarity_groups(self) -> None:
        """Only capture variants fold. Everything else stays on screen.

        Guards the two neighbouring features that also keep rows in
        `similar_pairs`: byte-identical duplicates, where the design is to
        still inspect every file, and visual burst groups, which have their own
        dedicated UI. Folding either would silently drop them from the library.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            duplicate_a = insert_photo(conn, "DUP_A.JPG", sha256="identical")
            duplicate_b = insert_photo(
                conn, "Copies/DUP_B.JPG", sha256="identical"
            )
            burst_a = insert_photo(conn, "BURST_A.JPG")
            burst_b = insert_photo(conn, "BURST_B.JPG")
            conn.executemany(
                """INSERT INTO similar_pairs(
                     a_id,b_id,score,kind,recommended_id,face_safe
                   ) VALUES(?,?,1.0,?,?,0)""",
                [
                    (duplicate_a, duplicate_b, "exact", duplicate_a),
                    (burst_a, burst_b, "similar", burst_a),
                ],
            )
            conn.commit()

        listed = self.application().photo_queries.photos(
            {
                "project_id": [self.project.project_id],
                "decisions": ["all"],
                "ai_states": ["all"],
            }
        )
        self.assertEqual(
            {int(item["id"]) for item in listed["items"]},
            {duplicate_a, duplicate_b, burst_a, burst_b},
        )
        self.assertEqual(listed["total"], 4)

    def test_decision_response_marks_the_folded_variant_member(self) -> None:
        """A synced decision spans formats, but only one of them is on screen.

        The library uses this flag to keep its "显示 N / 总数" denominator
        honest: a folded file never occupied a card, so its decision moving
        must not change the count.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_0720.JPG")
            raw = insert_photo(conn, "IMG_0720.CR3", size=20_000_000)
            rebuild_capture_variants(conn)
            conn.commit()

        handler = object.__new__(http_api.Handler)
        handler._send_json = mock.Mock()
        with mock.patch.object(http_api, "APPLICATION", self.application()):
            handler.api_decision(
                {
                    "project_id": self.project.project_id,
                    "photo_id": jpg,
                    "decision": "remove",
                }
            )
        payload = handler._send_json.call_args.args[0]
        self.assertEqual(
            {int(item["id"]) for item in payload["affected_photos"]}, {jpg, raw}
        )
        self.assertEqual(
            {
                int(item["id"]): bool(item["is_capture_variant"])
                for item in payload["affected_photos"]
            },
            {jpg: False, raw: True},
        )

    def test_similar_groups_are_paginated_and_search_raw_paths_casefolded(
        self,
    ) -> None:
        with closing(connect_db(self.project.db_path)) as conn:
            groups = []
            for index, folder in enumerate(("旅行", "Straße", "归档")):
                jpg = insert_photo(
                    conn,
                    f"{folder}/IMG_{index:04d}.JPG",
                    taken=f"2026:01:0{index + 1} 12:00:00",
                )
                raw = insert_photo(
                    conn,
                    f"{folder}/IMG_{index:04d}.NEF",
                    size=20_000_000,
                    taken=f"2026:01:0{index + 1} 12:00:00",
                )
                mate = insert_photo(
                    conn,
                    f"{folder}/MATE_{index:04d}.JPG",
                    taken=f"2026:01:0{index + 1} 12:00:01",
                )
                groups.append((jpg, raw, mate))
            rebuild_capture_variants(conn)
            conn.executemany(
                """INSERT INTO similar_pairs(
                     a_id,b_id,score,kind,recommended_id,face_safe
                   ) VALUES(?,?,?,?,?,?)""",
                [
                    (jpg, mate, 0.9, "similar", jpg, 0)
                    for jpg, _raw, mate in groups
                ],
            )
            conn.commit()

        application = self.application()
        all_groups = application.photo_queries.similar_groups(
            {"project_id": [self.project.project_id]}
        )
        first_page = application.photo_queries.similar_groups(
            {
                "project_id": [self.project.project_id],
                "limit": ["2"],
                "offset": ["0"],
            }
        )
        second_page = application.photo_queries.similar_groups(
            {
                "project_id": [self.project.project_id],
                "limit": ["2"],
                "offset": ["2"],
            }
        )
        self.assertEqual(all_groups["total"], 3)
        self.assertEqual(first_page["total"], 3)
        self.assertEqual(second_page["total"], 3)
        self.assertEqual(len(first_page["items"]), 2)
        self.assertEqual(len(second_page["items"]), 1)
        self.assertEqual(
            [item["id"] for item in all_groups["items"]],
            [
                item["id"]
                for item in [*first_page["items"], *second_page["items"]]
            ],
        )

        raw_search = application.photo_queries.similar_groups(
            {
                "project_id": [self.project.project_id],
                "search": ["STRASSE/img_0001.nef"],
                "limit": ["1"],
                "offset": ["0"],
            }
        )
        # The group is found by the RAW's path even though no RAW is listed:
        # the search maps a folded file onto its representative, so searching
        # for the file the user remembers still lands on the right group.
        self.assertEqual(raw_search["total"], 1)
        self.assertEqual(len(raw_search["items"]), 1)
        # Two captures (the NEF+JPG exposure and its MATE), not three files.
        self.assertEqual(raw_search["items"][0]["count"], 2)
        self.assertIn(
            ["NEF", "JPG"],
            [
                photo["variant_extensions"]
                for photo in raw_search["items"][0]["covers"]
            ],
        )
        exhausted = application.photo_queries.similar_groups(
            {
                "project_id": [self.project.project_id],
                "limit": ["2"],
                "offset": ["99"],
            }
        )
        self.assertEqual(exhausted, {"total": 3, "items": []})

    def test_variant_rows_and_metadata_queries_scale_by_parameter_batch(
        self,
    ) -> None:
        with closing(connect_db(self.project.db_path)) as conn:
            photo_ids = []
            for index in range(3):
                photo_ids.extend(
                    (
                        insert_photo(conn, f"IMG_{index:04d}.JPG"),
                        insert_photo(conn, f"IMG_{index:04d}.CR3"),
                    )
                )
            rebuild_capture_variants(conn)
            conn.commit()
            statements = []
            conn.set_trace_callback(statements.append)
            with mock.patch(
                "cullumi.capture_variants.SQLITE_PARAMETER_BATCH", 2
            ):
                rows = active_variant_rows(conn, photo_ids)
                query_count = len(
                    [
                        statement
                        for statement in statements
                        if statement.lstrip().upper().startswith("SELECT")
                    ]
                )
                metadata = variant_metadata_from_rows(rows)

        self.assertEqual(query_count, 5)
        self.assertEqual(
            len(
                [
                    statement
                    for statement in statements
                    if statement.lstrip().upper().startswith("SELECT")
                ]
            ),
            query_count,
        )
        self.assertEqual(set(metadata), set(photo_ids))
        self.assertTrue(
            all(extensions == ["CR3", "JPG"] for extensions in metadata.values())
        )

    def test_photo_library_sort_orders_are_whitelisted_and_paginated(self) -> None:
        with closing(connect_db(self.project.db_path)) as conn:
            alpha = insert_photo(
                conn,
                "Z-folder/alpha.JPG",
                suggestion="keep",
                size=100,
                taken="2024:01:01 10:00:00",
            )
            beta = insert_photo(
                conn,
                "A-folder/beta.JPG",
                suggestion="remove",
                size=300,
                taken="2026:01:01 10:00:00",
            )
            gamma = insert_photo(
                conn,
                "M-folder/gamma.JPG",
                suggestion="review",
                size=200,
                taken="2025:01:01 10:00:00",
            )
            conn.commit()

        application = self.application()

        def sorted_ids(
            sort: str,
            direction: str = "asc",
            *,
            limit: int = 10,
            offset: int = 0,
        ) -> list[int]:
            result = application.photo_queries.photos(
                {
                    "project_id": [self.project.project_id],
                    "sort": [sort],
                    "direction": [direction],
                    "formats": ["all"],
                    "decisions": ["all"],
                    "ai_states": ["all"],
                    "limit": [str(limit)],
                    "offset": [str(offset)],
                }
            )
            return [int(item["id"]) for item in result["items"]]

        self.assertEqual(sorted_ids("suggestion"), [beta, gamma, alpha])
        self.assertEqual(sorted_ids("filename"), [alpha, beta, gamma])
        self.assertEqual(sorted_ids("size"), [alpha, gamma, beta])
        self.assertEqual(sorted_ids("taken"), [alpha, gamma, beta])
        self.assertEqual(sorted_ids("suggestion", "desc"), [alpha, gamma, beta])
        self.assertEqual(sorted_ids("filename", "desc"), [gamma, beta, alpha])
        self.assertEqual(sorted_ids("size", "desc"), [beta, gamma, alpha])
        self.assertEqual(sorted_ids("taken", "desc"), [beta, gamma, alpha])
        self.assertEqual(sorted_ids("filename", limit=1, offset=1), [beta])
        with self.assertRaisesRegex(ValueError, "sort 必须是"):
            sorted_ids("relative_path DESC; DROP TABLE photos")
        with self.assertRaisesRegex(ValueError, "direction 必须是"):
            sorted_ids("filename", "sideways")

    def test_quarantine_and_restore_refresh_variant_memberships(self) -> None:
        jpg_path = self.photos / "IMG_0400.JPG"
        raw_path = self.photos / "IMG_0400.CR3"
        jpg_path.write_bytes(b"jpeg")
        raw_path.write_bytes(b"raw-data")
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(
                conn,
                jpg_path.name,
                decision="remove",
                size=jpg_path.stat().st_size,
            )
            raw = insert_photo(conn, raw_path.name, size=raw_path.stat().st_size)
            conn.execute(
                "UPDATE photos SET mtime=? WHERE id=?",
                (jpg_path.stat().st_mtime, jpg),
            )
            conn.execute(
                "UPDATE photos SET mtime=? WHERE id=?",
                (raw_path.stat().st_mtime, raw),
            )
            rebuild_capture_variants(conn)
            conn.commit()

        quarantined = apply_quarantine(self.project)
        self.assertEqual(quarantined["moved"], 1)
        with closing(connect_db(self.project.db_path)) as conn:
            memberships = {
                int(row["photo_id"]): int(row["representative_id"])
                for row in conn.execute(
                    "SELECT photo_id,representative_id FROM capture_variant_members"
                )
            }
        self.assertEqual(memberships, {})

        restored = restore_batch(self.project, str(quarantined["batch_id"]))
        self.assertEqual(restored["restored"], 1)
        with closing(connect_db(self.project.db_path)) as conn:
            memberships = {
                int(row["photo_id"]): int(row["representative_id"])
                for row in conn.execute(
                    "SELECT photo_id,representative_id FROM capture_variant_members"
                )
            }
        self.assertEqual(memberships, {jpg: jpg, raw: jpg})

    def test_cross_directory_pairing_accepts_matching_capture_times(self) -> None:
        """RAW filed under a `raw/` subdirectory pairs with its JPEG at the root."""
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_0100.JPG", taken="2026:10:02 17:59:44")
            raw = insert_photo(conn, "raw/IMG_0100.ARW", taken="2026:10:02 17:59:44")
            memberships = rebuild_capture_variants(conn)
            conn.commit()

        self.assertEqual(memberships[jpg], jpg)
        self.assertEqual(memberships[raw], jpg)

    def test_cross_directory_pairing_rejects_conflicting_capture_times(self) -> None:
        """Same stem but months apart means two events, not one exposure."""
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_0101.JPG", taken="2026:10:02 17:59:44")
            other = insert_photo(conn, "raw/IMG_0101.ARW", taken="2025:03:04 09:15:00")
            memberships = rebuild_capture_variants(conn)
            conn.commit()

        self.assertNotIn(jpg, memberships)
        self.assertNotIn(other, memberships)

    def test_pairing_tolerates_small_capture_time_drift(self) -> None:
        """A few seconds of drift is precision noise, not a different exposure."""
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_0102.JPG", taken="2026:10:02 17:59:44")
            raw = insert_photo(conn, "raw/IMG_0102.ARW", taken="2026:10:02 17:59:47")
            memberships = rebuild_capture_variants(conn)
            conn.commit()

        self.assertEqual(memberships[raw], jpg)

    def test_pairing_falls_back_to_mtime_when_exif_is_missing(self) -> None:
        """A RAW with no readable EXIF still pairs on file modification time."""
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_0103.JPG", taken="2026:10:02 17:59:44")
            raw = insert_photo(conn, "raw/IMG_0103.ARW", taken="")
            conn.execute(
                "UPDATE photos SET mtime=? WHERE id=?", (1_700_000_000.0, jpg)
            )
            conn.execute(
                "UPDATE photos SET mtime=? WHERE id=?", (1_700_000_000.0, raw)
            )
            memberships = rebuild_capture_variants(conn)
            conn.commit()

        self.assertEqual(memberships[raw], jpg)

    def test_pairing_rejects_mtime_fallback_when_it_disagrees(self) -> None:
        """Without EXIF, an mtime far apart must not silently pair."""
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_0104.JPG", taken="2026:10:02 17:59:44")
            raw = insert_photo(conn, "raw/IMG_0104.ARW", taken="")
            conn.execute(
                "UPDATE photos SET mtime=? WHERE id=?", (1_700_000_000.0, jpg)
            )
            conn.execute(
                "UPDATE photos SET mtime=? WHERE id=?", (1_700_060_000.0, raw)
            )
            memberships = rebuild_capture_variants(conn)
            conn.commit()

        self.assertNotIn(jpg, memberships)
        self.assertNotIn(raw, memberships)

    def test_pairing_falls_back_to_stem_when_no_timestamps_exist(self) -> None:
        """Neither EXIF nor mtime available: trust the stem rather than lose the pair."""
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_0105.JPG", taken="")
            raw = insert_photo(conn, "raw/IMG_0105.ARW", taken="")
            memberships = rebuild_capture_variants(conn)
            conn.commit()

        self.assertEqual(memberships[raw], jpg)

    def test_same_directory_pairing_still_works_without_timestamps(self) -> None:
        """The pre-existing same-directory layout keeps working after the change."""
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "Trip/IMG_0106.JPG", taken="")
            raw = insert_photo(conn, "Trip/IMG_0106.CR3", taken="")
            memberships = rebuild_capture_variants(conn)
            conn.commit()

        self.assertEqual(memberships[jpg], jpg)
        self.assertEqual(memberships[raw], jpg)

    def test_burst_neighbours_are_not_paired_by_filename(self) -> None:
        """Measured burst frames sit ~1s apart, so the window must stay tight."""
        with closing(connect_db(self.project.db_path)) as conn:
            first_jpg = insert_photo(
                conn, "IMG_0107.JPG", taken="2026:10:02 18:04:56"
            )
            second_jpg = insert_photo(
                conn, "IMG_0108.JPG", taken="2026:10:02 18:04:57"
            )
            # Shares the first frame's stem, but captured minutes later -- well
            # outside the window, so the stem clash must not carry it across.
            stale_raw = insert_photo(
                conn, "raw/IMG_0107.ARW", taken="2026:10:02 18:14:56"
            )
            memberships = rebuild_capture_variants(conn)
            conn.commit()

        self.assertNotIn(first_jpg, memberships)
        self.assertNotIn(second_jpg, memberships)
        self.assertNotIn(stale_raw, memberships)


class CaptureVariantFoldingTests(unittest.TestCase):
    """One card per capture-variant group in the library listing.

    The pairing itself is unchanged; what is under test is the *display*
    layer: a RAW and the JPEG rendered from it are one exposure, so the
    library shows the representative once instead of two cards. Anything
    that is not a capture variant -- exact duplicates, visual-similarity
    groups, unpaired photos, motion photos -- keeps its own card.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.photos = self.root / "photos"
        self.photos.mkdir()
        self.config = ConfigStore(self.root / "config.json")
        self.config.data["default_cache_root"] = str(self.root / "cache")
        self.config.save()
        self.manager = ProjectManager(self.config)
        self.project = self.manager.open(str(self.photos))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def application(self) -> http_api.ApplicationContext:
        scanner = Scanner(self.config, self.manager)
        return http_api.ApplicationContext(
            self.config,
            self.manager,
            scanner,
            SimilarityGroupCache(),
            "test-token",
            Path("web"),
        )

    def library(self, **overrides: Any) -> dict[str, Any]:
        """Query the library exactly as the photo grid does."""
        query: dict[str, list[str]] = {
            "project_id": [self.project.project_id],
            "decisions": ["all"],
            "ai_states": ["all"],
            "formats": ["all"],
        }
        for key, value in overrides.items():
            query[key] = [str(value)]
        return self.application().photo_queries.photos(query)

    def library_ids(self, **overrides: Any) -> set[int]:
        """Every photo id the library grid would render as a card."""
        return {int(item["id"]) for item in self.library(**overrides)["items"]}

    # ------------------------------------------------------------------
    # B. What must be folded
    # ------------------------------------------------------------------

    def test_raw_and_jpeg_of_one_exposure_share_a_single_card(self) -> None:
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_2001.JPG", taken="2026:10:02 17:59:44")
            insert_photo(
                conn,
                "raw/IMG_2001.ARW",
                taken="2026:10:02 17:59:44",
                size=20_000_000,
            )
            rebuild_capture_variants(conn)
            conn.commit()

        self.assertEqual(self.library_ids(), {jpg})

    def test_representative_card_reports_both_linked_formats(self) -> None:
        """The badge the card renders comes from `variant_extensions`."""
        with closing(connect_db(self.project.db_path)) as conn:
            insert_photo(conn, "IMG_2002.JPG", taken="2026:10:02 17:59:44")
            insert_photo(
                conn,
                "raw/IMG_2002.ARW",
                taken="2026:10:02 17:59:44",
                size=20_000_000,
            )
            rebuild_capture_variants(conn)
            conn.commit()

        items = self.library()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["variant_extensions"], ["ARW", "JPG"])

    def test_group_of_three_keeps_one_card_with_every_extension(self) -> None:
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_2003.JPG", taken="2026:10:02 17:59:44")
            insert_photo(
                conn, "raw/IMG_2003.ARW", taken="2026:10:02 17:59:44",
                size=20_000_000,
            )
            insert_photo(
                conn, "raw/IMG_2003.NEF", taken="2026:10:02 17:59:44",
                size=21_000_000,
            )
            rebuild_capture_variants(conn)
            conn.commit()

        items = self.library()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(int(items[0]["id"]), jpg)
        self.assertEqual(items[0]["variant_extensions"], ["ARW", "NEF", "JPG"])

    # ------------------------------------------------------------------
    # A. What must NOT be folded -- regressions, not features
    # ------------------------------------------------------------------

    def test_byte_identical_duplicates_keep_their_own_cards(self) -> None:
        """Upstream inspects every file even when the bytes are identical.

        Two files sharing a sha256 but filed under different stems are not a
        capture variant, and `kind='exact'` similarity must still see both.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            first = insert_photo(conn, "Copy_A.JPG", sha256="identical-bytes")
            second = insert_photo(conn, "Copy_B.JPG", sha256="identical-bytes")
            rebuild_capture_variants(conn)
            Scanner(self.config, self.manager).rebuild_similarity(
                self.project, conn, copy.deepcopy(BUILTIN_PROFILES["balanced"])
            )
            conn.commit()
            exact = [
                (int(row["a_id"]), int(row["b_id"]))
                for row in conn.execute(
                    "SELECT a_id,b_id FROM similar_pairs WHERE kind='exact'"
                )
            ]

        self.assertEqual(exact, [(first, second)])
        self.assertEqual(self.library_ids(), {first, second})

    def test_exact_duplicate_of_a_raw_joins_its_group_card(self) -> None:
        """A byte-identical RAW copy must not become a second card."""
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_2004.JPG", taken="2026:10:02 17:59:44")
            insert_photo(
                conn, "raw/IMG_2004.ARW", taken="2026:10:02 17:59:44",
                size=20_000_000, sha256="raw-bytes",
            )
            insert_photo(
                conn, "Backup/IMG_2004.ARW", taken="2026:10:02 17:59:44",
                size=20_000_000, sha256="raw-bytes",
            )
            rebuild_capture_variants(conn)
            conn.commit()

        # The copy shares the stem so it joins the group; the group still
        # renders as exactly one card.
        self.assertEqual(self.library_ids(), {jpg})

    def test_visual_similarity_group_members_are_not_folded(self) -> None:
        """Similar groups have their own expand UI; members must survive.

        "Not folded" here means *not merged into one card for the whole
        group*: each exposure keeps a card of its own. Within a card the
        library's rule still applies, so a capture's RAW stays folded into
        its JPEG -- the same dialect the photo grid speaks.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            first_jpg = insert_photo(conn, "IMG_2005.JPG")
            insert_photo(conn, "IMG_2005.CR3", size=20_000_000)
            second_jpg = insert_photo(conn, "IMG_2006.JPG")
            insert_photo(conn, "IMG_2006.CR3", size=20_000_000)
            rebuild_capture_variants(conn)
            conn.executemany(
                """INSERT INTO similar_pairs(
                     a_id,b_id,score,kind,recommended_id,face_safe
                   ) VALUES(?,?,?,?,?,?)""",
                [(first_jpg, second_jpg, 0.95, "similar", first_jpg, 0)],
            )
            conn.commit()

        # One card per exposure: the two representatives -- never one card for
        # the whole group, and never four.
        self.assertEqual(self.library_ids(), {first_jpg, second_jpg})

        listing = self.application().photo_queries.similar_groups(
            {"project_id": [self.project.project_id]}
        )
        detail = self.application().photo_queries.similar_group(
            {
                "project_id": [self.project.project_id],
                "group_id": [listing["items"][0]["id"]],
            }
        )
        self.assertEqual(detail["count"], 2)
        self.assertEqual(
            {int(item["id"]) for item in detail["members"]},
            {first_jpg, second_jpg},
        )
        # Every card is a representative of its own capture, so nothing in
        # the group claims to be a folded sibling any more.
        self.assertFalse(
            any(item["is_capture_variant"] for item in detail["members"])
        )

    def test_unpaired_photos_each_keep_a_card(self) -> None:
        """Only RAW, only JPEG, only PNG -- no partner, so nothing to fold."""
        with closing(connect_db(self.project.db_path)) as conn:
            raw_only = insert_photo(conn, "IMG_3001.CR3", size=20_000_000)
            jpeg_only = insert_photo(conn, "IMG_3002.JPG")
            png_only = insert_photo(conn, "IMG_3003.PNG")
            heif_only = insert_photo(conn, "IMG_3004.HEIF")
            rebuild_capture_variants(conn)
            conn.commit()

        self.assertEqual(
            self.library_ids(), {raw_only, jpeg_only, png_only, heif_only}
        )

    def test_same_stem_at_different_times_is_not_a_variant(self) -> None:
        """Two events that share a filename are not one exposure."""
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_3005.JPG", taken="2026:10:02 17:59:44")
            other = insert_photo(
                conn, "raw/IMG_3005.ARW", taken="2025:03:04 09:15:00",
                size=20_000_000,
            )
            rebuild_capture_variants(conn)
            conn.commit()

        self.assertEqual(self.library_ids(), {jpg, other})

    def test_motion_photo_keeps_its_card_and_its_video(self) -> None:
        """HEIF+MOV pairing is a separate mechanism and must be untouched."""
        with closing(connect_db(self.project.db_path)) as conn:
            live = insert_photo(conn, "IMG_3006.HEIF", taken="2026:10:02 17:59:44")
            conn.execute(
                """UPDATE photos
                      SET media_type='motion_photo',motion_kind='apple_sidecar',
                          motion_relative_path='IMG_3006.MOV',
                          motion_duration_ms=3000,motion_fps=30.0,
                          motion_frame_count=90
                    WHERE id=?""",
                (live,),
            )
            standalone = insert_photo(conn, "IMG_3007.JPG")
            rebuild_capture_variants(conn)
            conn.commit()

        # The motion photo is a standalone card, and so is the unrelated JPEG
        # next to it: folding keys on the stem, so neither may disappear.
        self.assertEqual(self.library_ids(), {live, standalone})
        payload = next(
            item
            for item in self.library()["items"]
            if int(item["id"]) == live
        )
        self.assertEqual(payload["motion"]["kind"], "apple_sidecar")
        self.assertIn("/api/motion/video?", payload["motion"]["video_url"])

    def test_motion_photo_beside_a_raw_pair_keeps_both_cards(self) -> None:
        """Folding the RAW pair must not swallow the unrelated motion photo."""
        with closing(connect_db(self.project.db_path)) as conn:
            live = insert_photo(conn, "IMG_3008.HEIF")
            conn.execute(
                "UPDATE photos SET media_type='motion_photo',"
                "motion_kind='apple_sidecar',motion_relative_path='IMG_3008.MOV' "
                "WHERE id=?",
                (live,),
            )
            jpg = insert_photo(conn, "IMG_3009.JPG", taken="2026:10:02 17:59:44")
            insert_photo(
                conn, "raw/IMG_3009.ARW", taken="2026:10:02 17:59:44",
                size=20_000_000,
            )
            rebuild_capture_variants(conn)
            conn.commit()

        self.assertEqual(self.library_ids(), {live, jpg})

    # ------------------------------------------------------------------
    # C. Existing behaviour that must survive the change
    # ------------------------------------------------------------------

    def test_library_counts_fold_but_format_counts_stay_per_file(self) -> None:
        """The two counting bases, side by side.

        `project_photo_counts` describes the library the user sees, and the
        library folds a RAW+JPEG pair into one card -- so it counts two cards
        here (the group plus the PNG). `format_category_counts` describes what
        is on disk, so it still counts all three files: "how many RAW do I
        have" must not silently become "how many groups contain a RAW".
        """
        with closing(connect_db(self.project.db_path)) as conn:
            insert_photo(conn, "IMG_4001.JPG", decision="keep")
            insert_photo(
                conn, "raw/IMG_4001.ARW", decision="keep", size=20_000_000
            )
            insert_photo(conn, "IMG_4002.PNG")
            rebuild_capture_variants(conn)
            conn.commit()
            counts = project_photo_counts(conn)
            formats = {
                entry["id"]: entry["count"]
                for entry in format_category_counts(conn)
            }

        # 3 files -> 2 cards.
        self.assertEqual(counts["total"], 2)
        self.assertEqual(counts["library_counts"]["readable"], 2)
        self.assertEqual(counts["library_counts"]["keep"], 1)
        # Format tallies are unaffected by folding.
        self.assertEqual(formats, {"raw": 1, "jpeg": 1, "png": 1})

    def test_deciding_on_a_card_still_reaches_the_whole_group(self) -> None:
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_4003.JPG", suggestion="remove")
            raw = insert_photo(conn, "raw/IMG_4003.ARW", suggestion="remove")
            rebuild_capture_variants(conn)
            conn.commit()

        # Only one card is rendered, but deciding on it must still decide both
        # files -- otherwise the folded RAW is orphaned.
        result = set_photo_decision(self.project, jpg, "remove", True)
        self.assertEqual({int(row["id"]) for row in result.rows}, {jpg, raw})

    def test_accept_library_scope_covers_the_folded_file(self) -> None:
        with closing(connect_db(self.project.db_path)) as conn:
            insert_photo(conn, "IMG_4004.JPG", suggestion="remove")
            insert_photo(conn, "raw/IMG_4004.ARW", suggestion="remove")
            rebuild_capture_variants(conn)
            conn.commit()

        handler = object.__new__(http_api.Handler)
        handler._send_json = mock.Mock()
        with mock.patch.object(http_api, "APPLICATION", self.application()):
            handler.api_decision_accept(
                {"project_id": self.project.project_id, "scope": "library"}
            )
        self.assertEqual(handler._send_json.call_args.args[0]["marked"], 2)

    def test_quarantine_and_restore_keep_the_pair_paired(self) -> None:
        """`capture_variant_members` must survive so restore re-pairs."""
        jpg_path = self.photos / "IMG_4005.JPG"
        raw_path = self.photos / "IMG_4005.CR3"
        jpg_path.write_bytes(b"jpeg")
        raw_path.write_bytes(b"raw")
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(
                conn, jpg_path.name, decision="remove",
                size=jpg_path.stat().st_size,
            )
            raw = insert_photo(
                conn, raw_path.name, decision="remove",
                size=raw_path.stat().st_size,
            )
            conn.execute(
                "UPDATE photos SET mtime=? WHERE id IN (?,?)",
                (jpg_path.stat().st_mtime, jpg, raw),
            )
            rebuild_capture_variants(conn)
            conn.commit()

        quarantined = apply_quarantine(self.project)
        self.assertEqual(quarantined["moved"], 2)
        restored = restore_batch(self.project, str(quarantined["batch_id"]))
        self.assertEqual(restored["restored"], 2)

        with closing(connect_db(self.project.db_path)) as conn:
            memberships = {
                int(row["photo_id"]): int(row["representative_id"])
                for row in conn.execute(
                    "SELECT photo_id,representative_id FROM capture_variant_members"
                )
            }
        self.assertEqual(memberships, {jpg: jpg, raw: jpg})
        # And the restored pair is folded again, not shown as two cards.
        self.assertEqual(self.library_ids(), {jpg})

    def test_csv_export_and_import_cover_the_whole_group(self) -> None:
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_4006.JPG", decision="keep")
            raw = insert_photo(conn, "raw/IMG_4006.ARW", decision="keep")
            rebuild_capture_variants(conn)
            conn.commit()

        exported = export_decisions(self.project)
        self.assertIn("IMG_4006.JPG", exported)
        self.assertIn("raw/IMG_4006.ARW", exported)

        csv_path = self.root / "round-trip.csv"
        csv_path.write_text(
            "decision,path\nremove,IMG_4006.JPG\n", encoding="utf-8"
        )
        import_decisions(self.project, csv_path, True, detailed=True)
        with closing(connect_db(self.project.db_path)) as conn:
            decisions = {
                int(row["id"]): row["decision"]
                for row in conn.execute("SELECT id,decision FROM photos")
            }
        self.assertEqual(decisions, {jpg: "remove", raw: "remove"})

    def test_searching_a_folded_filename_still_finds_the_group(self) -> None:
        """A user searching `DSC01931` must still reach the exposure.

        Which member of the group answers the search is a design choice --
        what must not happen is the group disappearing from the results.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "DSC01931.JPG", taken="2026:10:02 17:59:44")
            raw = insert_photo(
                conn, "raw/DSC01931.ARW", taken="2026:10:02 17:59:44",
                size=20_000_000,
            )
            other = insert_photo(conn, "OTHER0001.JPG")
            rebuild_capture_variants(conn)
            conn.commit()

        for term in ("DSC01931", "raw/DSC01931.ARW", "DSC01931.JPG"):
            with self.subTest(term=term):
                found = self.library(search=term)
                ids = {int(item["id"]) for item in found["items"]}
                # Exactly one card for the exposure, and the unrelated
                # photo never matches.
                self.assertEqual(len(ids), 1)
                self.assertNotIn(other, ids)
                self.assertIn(next(iter(ids)), {jpg, raw})
                self.assertEqual(
                    found["items"][0]["variant_extensions"], ["ARW", "JPG"]
                )

    def test_format_filter_still_selects_raw_backed_groups(self) -> None:
        """`formats=raw` must keep working after folding.

        The card is whichever member of the group the filter selects, so this
        asserts the invariant -- the RAW-backed exposure stays reachable and
        its badge still lists both formats -- rather than pinning which of the
        two files wins.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_4007.JPG", taken="2026:10:02 17:59:44")
            raw = insert_photo(
                conn, "raw/IMG_4007.ARW", taken="2026:10:02 17:59:44",
                size=20_000_000,
            )
            png = insert_photo(conn, "IMG_4008.PNG")
            rebuild_capture_variants(conn)
            conn.commit()

        raw_result = self.library(formats="raw")
        raw_items = raw_result["items"]
        self.assertEqual(len(raw_items), 1)
        self.assertIn(int(raw_items[0]["id"]), {jpg, raw})
        self.assertEqual(raw_items[0]["variant_extensions"], ["ARW", "JPG"])
        self.assertEqual(raw_result["total"], 1)

        png_ids = {
            int(item["id"]) for item in self.library(formats="png")["items"]
        }
        self.assertEqual(png_ids, {png})

        # Asking for both categories still yields a single card per exposure.
        both = self.library(formats="raw,jpeg")
        self.assertEqual(len(both["items"]), 1)
        self.assertIn(int(both["items"][0]["id"]), {jpg, raw})

    # ------------------------------------------------------------------
    # D. Pagination stays self-consistent
    # ------------------------------------------------------------------

    def test_total_counts_cards_not_files_and_pages_do_not_overlap(self) -> None:
        with closing(connect_db(self.project.db_path)) as conn:
            for index in range(4):
                stem = f"IMG_50{index:02d}"
                insert_photo(conn, f"{stem}.JPG", taken="2026:10:02 17:59:44")
                insert_photo(
                    conn, f"raw/{stem}.ARW", taken="2026:10:02 17:59:44",
                    size=20_000_000,
                )
            insert_photo(conn, "LONE0001.JPG")
            rebuild_capture_variants(conn)
            conn.commit()

        full = self.library()
        # 9 files on disk, 5 cards rendered.
        self.assertEqual(len(full["items"]), 5)
        self.assertEqual(full["total"], 5)

        seen: list[int] = []
        for offset in (0, 2, 4):
            page = self.library(limit="2", offset=str(offset))
            self.assertEqual(page["total"], 5)
            seen.extend(int(item["id"]) for item in page["items"])
        self.assertEqual(len(seen), 5)
        self.assertEqual(len(set(seen)), 5)

        exhausted = self.library(limit="2", offset="99")
        self.assertEqual(exhausted["items"], [])
        self.assertEqual(exhausted["total"], 5)

    # ------------------------------------------------------------------
    # Reverse proof: the guard must actually be capable of failing
    # ------------------------------------------------------------------

    def test_matrix_detects_a_deliberately_over_folded_pairing(self) -> None:
        """Sanity check on the matrix itself.

        If every assertion above passed for any input they would prove
        nothing. Here the pairing table is corrupted so an independent photo
        is folded away; the same listing assertion must notice.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_6001.JPG", taken="2026:10:02 17:59:44")
            insert_photo(
                conn, "raw/IMG_6001.ARW", taken="2026:10:02 17:59:44",
                size=20_000_000,
            )
            bystander = insert_photo(conn, "IMG_6002.JPG")
            rebuild_capture_variants(conn)
            conn.commit()

        self.assertEqual(self.library_ids(), {jpg, bystander})

        # Forge a membership that folds the bystander into the JPEG's group.
        with closing(connect_db(self.project.db_path)) as conn:
            conn.execute(
                """INSERT INTO capture_variant_members(photo_id,representative_id)
                   VALUES(?,?)""",
                (bystander, jpg),
            )
            conn.commit()

        self.assertEqual(self.library_ids(), {jpg})
        self.assertNotIn(bystander, self.library_ids())


class CaptureVariantCountingBasisTests(unittest.TestCase):
    """The library counts cards; the format counts count files.

    Folding a RAW+JPEG pair into one card made the sidebar disagree with the
    grid: 734 cards were reported as 1468 photos. The fix gives the two
    aggregates deliberately different bases, so both questions can be answered
    at once -- and pins the sum identities that keep the sidebar coherent.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.photos = self.root / "photos"
        self.photos.mkdir()
        self.config = ConfigStore(self.root / "config.json")
        self.config.data["default_cache_root"] = str(self.root / "cache")
        self.config.save()
        self.manager = ProjectManager(self.config)
        self.project = self.manager.open(str(self.photos))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def pair(self, stem: str = "IMG_8001", **kwargs: Any) -> tuple[int, int]:
        """A RAW + JPEG pair of one exposure, rebuilt and committed."""
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(
                conn, f"{stem}.JPG", taken="2026:10:02 17:59:44", **kwargs
            )
            raw = insert_photo(
                conn,
                f"raw/{stem}.ARW",
                taken="2026:10:02 17:59:44",
                size=20_000_000,
            )
            rebuild_capture_variants(conn)
            conn.commit()
        return jpg, raw

    def counts(self) -> dict[str, Any]:
        with closing(connect_db(self.project.db_path)) as conn:
            return project_photo_counts(conn)

    def formats(self) -> dict[str, int]:
        with closing(connect_db(self.project.db_path)) as conn:
            return {
                entry["id"]: entry["count"]
                for entry in format_category_counts(conn)
            }

    # ------------------------------------------------------------------
    # A. One group counts once
    # ------------------------------------------------------------------

    def test_a_group_of_two_files_counts_as_one_card(self) -> None:
        """The bug, pinned: 1 RAW + 1 JPEG is ONE card."""
        self.pair()
        counts = self.counts()
        self.assertEqual(counts["total"], 1)
        self.assertEqual(counts["library_counts"]["readable"], 1)
        self.assertEqual(counts["library_counts"]["undecided"], 1)

    def test_the_unfolded_photo_count_is_exactly_half(self) -> None:
        """Two independent pairs must halve, not merely shift by one.

        A constant offset would pass the single-pair test above while still
        miscounting a real library, so this asserts the ratio.
        """
        self.pair("IMG_8002")
        self.pair("IMG_8003")
        self.assertEqual(self.counts()["total"], 2)
        with closing(connect_db(self.project.db_path)) as conn:
            files = conn.execute(
                "SELECT COUNT(*) FROM photos WHERE status='active'"
            ).fetchone()[0]
        self.assertEqual(files, 4)

    def test_a_photo_outside_any_group_is_still_counted(self) -> None:
        """Folding must not become a filter that hides photos.

        A lone JPEG belongs to no group, so it is its own representative and
        must appear exactly once. This is the reverse proof that the dedup is
        scoped to real groups rather than to, say, every non-RAW file.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            insert_photo(conn, "LONE8004.JPG")
            insert_photo(conn, "LONE8005.PNG")
            rebuild_capture_variants(conn)
            conn.commit()
        self.assertEqual(self.counts()["total"], 2)

    def test_format_counts_still_count_every_file(self) -> None:
        """"How many RAW do I have" is a question about the disk.

        Folding must not leak into this aggregate: the user filters by RAW to
        find originals to process elsewhere, and a per-card count would
        understate that by silently hiding the JPEGs.
        """
        self.pair()
        self.assertEqual(self.formats(), {"raw": 1, "jpeg": 1})

    # ------------------------------------------------------------------
    # B. Decisions inside one group
    # ------------------------------------------------------------------

    def test_a_decided_group_counts_the_decision_once(self) -> None:
        """Syncing writes one decision to both files; it must count once."""
        self.pair()
        set_photo_decision(self.project, self.counts() and 1, "keep", True)
        counts = self.counts()
        self.assertEqual(counts["library_counts"]["keep"], 1)
        self.assertEqual(counts["library_counts"]["undecided"], 0)
        self.assertEqual(counts["decisions"], {"keep": 1})

    def test_an_unsynced_group_counts_the_representative_s_decision(self) -> None:
        """The deliberate call: when members disagree, the card decides.

        With syncing OFF the JPEG can be `keep` while the RAW stays undecided.
        The screen shows ONE card, wearing the representative's badge, so the
        sidebar must count that card -- not the group as both decided and
        undecided, which would break the identity asserted below.
        """
        jpg, raw = self.pair()
        set_photo_decision(self.project, jpg, "keep", False)
        with closing(connect_db(self.project.db_path)) as conn:
            divergent = {
                int(row["id"]): str(row["decision"] or "")
                for row in conn.execute(
                    "SELECT id,decision FROM photos WHERE id IN (?,?)",
                    (jpg, raw),
                )
            }
        # The premise must actually hold, or this test proves nothing.
        self.assertEqual(divergent, {jpg: "keep", raw: ""})

        counts = self.counts()
        self.assertEqual(counts["total"], 1)
        self.assertEqual(counts["library_counts"]["readable"], 1)
        self.assertEqual(counts["library_counts"]["keep"], 1)
        self.assertEqual(counts["library_counts"]["undecided"], 0)
        # The decisive consequence: the group is in exactly one bucket.
        self.assertEqual(
            counts["library_counts"]["keep"]
            + counts["library_counts"]["undecided"],
            counts["library_counts"]["readable"],
        )

    def test_the_representative_is_the_readable_non_raw_file(self) -> None:
        """Why representative-based counting is the consistent choice.

        `_representative_sort_key` ranks readable non-RAW highest, so the card
        on screen is the JPEG. Counting any other member would mean the number
        beside the card describes a file the user cannot see.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(
                conn, "IMG_8006.JPG", taken="2026:10:02 17:59:44", decision="keep"
            )
            raw = insert_photo(
                conn,
                "raw/IMG_8006.ARW",
                taken="2026:10:02 17:59:44",
                size=20_000_000,
                decision="remove",
            )
            rebuild_capture_variants(conn)
            conn.commit()
            representative = conn.execute(
                "SELECT representative_id FROM capture_variant_members"
                " WHERE photo_id=?",
                (raw,),
            ).fetchone()[0]
        self.assertEqual(int(representative), jpg)
        # And the counts follow that card, not the folded RAW.
        counts = self.counts()
        self.assertEqual(counts["decisions"], {"keep": 1})

    # ------------------------------------------------------------------
    # C. The sum identities the sidebar depends on
    # ------------------------------------------------------------------

    def assert_sum_identities(self, counts: dict[str, Any]) -> None:
        """Every identity the sidebar's arithmetic depends on.

        These are the assertions that keep the UI from showing numbers that
        cannot be reconciled: the sidebar renders `keep`, `remove` and
        `undecided` as sibling tallies under `readable`, and a user who adds
        two of them to get the third must not get a different answer.
        """
        library = counts["library_counts"]
        self.assertEqual(
            library["keep"] + library["remove"] + library["undecided"],
            library["readable"],
            f"decisions must partition readable: {library}",
        )
        self.assertEqual(
            library["readable"] + library["unreadable"],
            counts["total"],
            f"readable + unreadable must equal total: {library}",
        )
        # ai_pending is a SUBSET of undecided, ai_remove_pending of ai_pending.
        self.assertLessEqual(library["ai_pending"], library["undecided"])
        self.assertLessEqual(library["ai_remove_pending"], library["ai_pending"])
        self.assertLessEqual(library["unreadable"], counts["total"])
        for name in ("keep", "remove"):
            self.assertLessEqual(counts["decisions"].get(name, 0), library[name])

    def test_identities_hold_across_a_mixed_library(self) -> None:
        """Every bucket at once, not one at a time.

        The identities are easy to satisfy in a library that only ever has
        keep-or-nothing. This fixture mixes decided groups, undecided groups,
        unreadable files and a lone photo, which is what a real library looks
        like after a partial review.
        """
        self.pair("IMG_8010", decision="keep")
        self.pair("IMG_8011", suggestion="remove")
        self.pair("IMG_8012")
        with closing(connect_db(self.project.db_path)) as conn:
            insert_photo(conn, "LONE8013.JPG")
            insert_photo(
                conn, "BROKEN8014.JPG", error="无法解码", suggestion="unreadable"
            )
            gone = insert_photo(conn, "GONE8015.JPG")
            # A file that has gone missing on disk: still a row, no longer part
            # of the library, so it must drop out of every tally.
            conn.execute(
                "UPDATE photos SET status='missing' WHERE id=?", (gone,)
            )
            rebuild_capture_variants(conn)
            conn.commit()
        counts = self.counts()
        # 3 groups + 1 lone + 1 unreadable = 5 cards; the missing file is not
        # active and must not be counted at all.
        self.assertEqual(counts["total"], 5)
        self.assertEqual(counts["library_counts"]["readable"], 4)
        self.assertEqual(counts["library_counts"]["unreadable"], 1)
        self.assert_sum_identities(counts)

    def test_identities_hold_for_an_empty_library(self) -> None:
        """Zero rows must not produce None-vs-0 drift in the arithmetic."""
        self.assert_sum_identities(self.counts())
        self.assertEqual(self.counts()["total"], 0)

    def test_clearing_every_decision_returns_the_group_to_undecided(self) -> None:
        """The round trip through the UI's own action."""
        self.pair("IMG_8020", decision="keep")
        self.assertEqual(self.counts()["library_counts"]["keep"], 1)
        clear_decisions(self.project)
        counts = self.counts()
        self.assertEqual(counts["library_counts"]["keep"], 0)
        self.assertEqual(counts["library_counts"]["undecided"], 1)
        self.assert_sum_identities(counts)


class CaptureVariantPreviewRenditionTests(unittest.TestCase):
    """The viewer loads a preview first and the original only on request.

    Originals measured at a 19.5MB median (24MB max) for this library, so
    sending one per photo opened is pure waste; but a 1:1 detail check needs
    real pixels. Both halves are pinned here, plus the guarantee that folding
    is untouched by any of it.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.photos = self.root / "photos"
        self.photos.mkdir()
        self.config = ConfigStore(self.root / "config.json")
        self.config.data["default_cache_root"] = str(self.root / "cache")
        self.config.save()
        self.manager = ProjectManager(self.config)
        self.project = self.manager.open(str(self.photos))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def queries(self) -> "PhotoQueryService":
        scanner = Scanner(self.config, self.manager)
        return http_api.ApplicationContext(
            self.config,
            self.manager,
            scanner,
            SimilarityGroupCache(),
            "test-token",
            Path("web"),
        ).photo_queries

    def payload(self, photo_id: int) -> dict[str, Any]:
        listing = self.queries().photos(
            {
                "project_id": [self.project.project_id],
                "decisions": ["all"],
                "ai_states": ["all"],
                "formats": ["all"],
            }
        )
        return next(
            item for item in listing["items"] if int(item["id"]) == photo_id
        )

    def test_preview_url_is_a_distinct_smaller_rendition(self) -> None:
        with closing(connect_db(self.project.db_path)) as conn:
            photo_id = insert_photo(conn, "IMG_9001.JPG")
            rebuild_capture_variants(conn)
            conn.commit()
        item = self.payload(photo_id)
        self.assertIn("preview_url", item)
        self.assertIn(f"w={VIEWER_PREVIEW_WIDTH}", item["preview_url"])
        # The two URLs must not be the same string, or the preview is dead code
        # and every open would still pull the original.
        self.assertNotEqual(item["preview_url"], item["photo_url"])
        self.assertRegex(item["photo_url"], rf"[?&]id={photo_id}(&|$)")

    def test_the_preview_width_is_bounded(self) -> None:
        """A preview bound must be a real bound.

        VIEWER_PREVIEW_WIDTH is the whole point of the feature: if it were 0,
        negative or absurd the preview would be unusable and the change would
        be a net loss.
        """
        self.assertGreater(VIEWER_PREVIEW_WIDTH, 0)
        self.assertLessEqual(VIEWER_PREVIEW_WIDTH, DISPLAY_PREVIEW_MAX_SIZE[0])

    def api_photo_handler(
        self, source_name: str, query: dict[str, list[str]]
    ) -> tuple[Any, mock.Mock, Path]:
        """A handler wired to a real on-disk JPEG plus a stubbed query.

        ``Path.resolve()`` is load-bearing, not cosmetic: macOS puts temp dirs
        under /private/tmp, and ``api_photo`` resolves internally, so an
        unresolved expected path would never match the call.
        """
        root = Path(self.temporary.name).resolve()
        source = root / source_name
        source.write_bytes(b"\xff\xd8\xff\xd9")
        thumbnail = root / "cache" / f"{source.stem}.jpg"
        thumbnail.parent.mkdir(parents=True, exist_ok=True)
        thumbnail.write_bytes(b"\xff\xd8\xff\xd9")
        project = mock.Mock(root=root, thumb_dir=thumbnail.parent)
        row = {"relative_path": source.name, "thumbnail": str(thumbnail)}
        handler = object.__new__(http_api.Handler)
        handler._photo_row = mock.Mock(return_value=(project, row))
        handler._send_file = mock.Mock()
        handler._query = mock.Mock(return_value={"id": ["1"], **query})
        return handler, source, thumbnail

    def test_api_photo_serves_a_width_bounded_jpeg_for_any_extension(self) -> None:
        """`&w=` works for JPEG too, not only for RAW/HEIF.

        Before this change a JPEG went out as the ORIGINAL bytes (24MB) because
        JPEG is not in DISPLAY_PREVIEW_EXTENSIONS and so skipped the preview
        branch entirely. If `&w=` only worked for RAW the complaint would be
        unaddressed for the most common format.
        """
        handler, source, thumbnail = self.api_photo_handler(
            "IMG_9002.JPG", {"w": [str(VIEWER_PREVIEW_WIDTH)]}
        )
        with mock.patch.object(
            http_api, "ensure_display_preview", return_value=thumbnail
        ) as build:
            handler.api_photo()
        build.assert_called_once_with(
            source,
            thumbnail,
            max_size=(VIEWER_PREVIEW_WIDTH, VIEWER_PREVIEW_WIDTH),
        )
        handler._send_file.assert_called_once_with(thumbnail, "image/jpeg")

    def test_api_photo_without_a_width_keeps_serving_the_original(self) -> None:
        """No `&w=` means no behaviour change -- that is the fallback path."""
        handler, source, _ = self.api_photo_handler("IMG_9003.JPG", {})
        handler.api_photo()
        handler._send_file.assert_called_once_with(source)

    def test_a_non_numeric_width_is_rejected_rather_than_ignored(self) -> None:
        handler, _, _ = self.api_photo_handler("IMG_9004.JPG", {"w": ["wide"]})
        with self.assertRaises(ValueError):
            handler.api_photo()
        handler._send_file.assert_not_called()

    def test_preview_failures_fall_back_to_the_original_bytes(self) -> None:
        """A preview that cannot be built must not blank the viewer."""
        handler, source, _ = self.api_photo_handler(
            "IMG_9005.JPG", {"w": [str(VIEWER_PREVIEW_WIDTH)]}
        )
        with mock.patch.object(
            http_api, "ensure_display_preview", side_effect=OSError("offline")
        ):
            handler.api_photo()
        handler._send_file.assert_called_once_with(source)

    def test_preview_cache_keys_separate_widths(self) -> None:
        """Reverse proof: two widths must not share one cached file.

        If the bound were left out of the fingerprint, whichever rendition was
        built first would be served to both -- a "preview" silently blurrier
        than it claims, or a full-size render downloaded for a small box.
        """
        source = self.root / "photos" / "IMG_9006.JPG"
        source.write_bytes(b"\xff\xd8\xff\xd9")
        thumbnail = self.root / "cache" / "IMG_9006.jpg"
        thumbnail.parent.mkdir(parents=True, exist_ok=True)
        thumbnail.write_bytes(b"\xff\xd8\xff\xd9")
        narrow = display_preview_path(source, thumbnail, (512, 512))
        wide = display_preview_path(source, thumbnail, (2048, 2048))
        default = display_preview_path(source, thumbnail)
        self.assertNotEqual(narrow, wide)
        self.assertNotEqual(wide, default)
        self.assertEqual(default, display_preview_path(source, thumbnail))

    def test_a_non_positive_bound_is_rejected(self) -> None:
        source = self.root / "photos" / "IMG_9007.JPG"
        source.write_bytes(b"\xff\xd8\xff\xd9")
        thumbnail = self.root / "cache" / "IMG_9007.jpg"
        thumbnail.parent.mkdir(parents=True, exist_ok=True)
        thumbnail.write_bytes(b"\xff\xd8\xff\xd9")
        with self.assertRaises(ValueError):
            ensure_display_preview(source, thumbnail, max_size=(0, 100))


class CaptureVariantFormatSwitchWithdrawnTests(unittest.TestCase):
    """The RAW/JPEG switch is gone; the fold and the hint stay.

    A rollback has to be complete: an orphaned route, an unreachable endpoint
    or a dead handler would all read as "removed" in review while still being
    reachable at runtime.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.photos = self.root / "photos"
        self.photos.mkdir()
        self.config = ConfigStore(self.root / "config.json")
        self.config.data["default_cache_root"] = str(self.root / "cache")
        self.config.save()
        self.manager = ProjectManager(self.config)
        self.project = self.manager.open(str(self.photos))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def web_root(self) -> Path:
        return Path(__file__).parents[1] / "web"

    def script(self, name: str) -> str:
        return (self.web_root() / "js" / name).read_text(encoding="utf-8")

    def test_the_variant_endpoint_is_no_longer_routed(self) -> None:
        self.assertNotIn("/api/photo/variants", http_api.GET_ROUTES)

    def test_the_service_no_longer_exposes_the_group_query(self) -> None:
        self.assertFalse(
            hasattr(PhotoQueryService, "capture_variants"),
            "PhotoQueryService 仍暴露 capture_variants()",
        )

    def test_the_handler_method_is_gone(self) -> None:
        self.assertFalse(
            hasattr(http_api.Handler, "api_photo_variants"),
            "http_api.Handler 仍保留 api_photo_variants()",
        )

    def test_no_script_still_calls_the_withdrawn_endpoint(self) -> None:
        for path in (self.web_root() / "js").glob("*.js"):
            self.assertNotIn(
                "/api/photo/variants",
                path.read_text(encoding="utf-8"),
                path.name,
            )

    def test_no_script_still_carries_the_variant_switch_state(self) -> None:
        """`viewerVariants` and friends were only ever for the switch.

        Leaving them in `state` would be the kind of dead field that later
        reads as "the browser mirrors the whole group", which is no longer
        true.
        """
        for name in ("runtime.js", "viewer.js", "app.js", "gallery.js"):
            source = self.script(name)
            for symbol in (
                "viewerVariants",
                "viewerVariantIndex",
                "cycleViewerVariant",
                "loadViewerVariants",
                "viewerCurrentPhoto",
                "renderViewerVariantSwitch",
            ):
                self.assertNotIn(symbol, source, f"{name} 仍引用 {symbol}")

    def test_the_variant_button_style_is_gone(self) -> None:
        style = (self.web_root() / "css" / "viewer.css").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(".viewer-variant-option", style)

    def test_the_badge_element_id_survives(self) -> None:
        """Frozen: the DOM spec asserts on this id, so it must not change."""
        markup = (self.web_root() / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="viewerVariantBadge"', markup)

    def test_the_badge_stays_plain_text_with_no_button_semantics(self) -> None:
        """The hint must not be clickable any more.

        Asserted on the rendered markup because that is what the browser
        exposes: no `role="group"`, no button element, and the text is still
        the `ARW + JPEG` form the DOM spec matches on.
        """
        markup = (self.web_root() / "index.html").read_text(encoding="utf-8")
        badge = markup.split('id="viewerVariantBadge"', 1)[1].split(">", 1)[0]
        self.assertNotIn("button", badge.lower())
        self.assertNotIn("role=", badge.lower())

        source = self.script("viewer.js")
        self.assertIn("renderViewerVariantBadge", source)
        # The badge's text comes from the shared formatter, unchanged, so
        # "CR3 + JPG" still renders exactly as the DOM spec expects.
        self.assertIn("variantFormatText(p, true)", source)

    def test_the_badge_title_states_that_raw_is_not_previewed(self) -> None:
        """The tooltip has to be accurate, not just present.

        Users asked why a RAW they can see listed never opens; the title is
        where that question gets answered.
        """
        source = self.script("viewer.js")
        self.assertIn("不提供 RAW 预览", source)
        self.assertIn("本组包含", source)

    def test_folding_still_works_without_the_switch(self) -> None:
        """The rollback must not disturb the feature that was kept."""
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(
                conn, "IMG_9101.JPG", taken="2026:10:02 17:59:44"
            )
            raw = insert_photo(
                conn,
                "raw/IMG_9101.ARW",
                taken="2026:10:02 17:59:44",
                size=20_000_000,
            )
            lone = insert_photo(conn, "LONE9102.JPG")
            rebuild_capture_variants(conn)
            conn.commit()

        scanner = Scanner(self.config, self.manager)
        context = http_api.ApplicationContext(
            self.config,
            self.manager,
            scanner,
            SimilarityGroupCache(),
            "test-token",
            Path("web"),
        )
        listing = context.photo_queries.photos(
            {
                "project_id": [self.project.project_id],
                "decisions": ["all"],
                "ai_states": ["all"],
                "formats": ["all"],
            }
        )
        ids = [int(item["id"]) for item in listing["items"]]
        self.assertEqual(set(ids), {jpg, lone})
        self.assertNotIn(raw, ids)
        self.assertEqual(listing["total"], 2)
        # The representative still advertises the whole group, which is what
        # keeps the "ARW + JPG" badge meaningful after the switch was removed.
        card = next(item for item in listing["items"] if int(item["id"]) == jpg)
        self.assertEqual(card["variant_extensions"], ["ARW", "JPG"])

    def test_the_f_key_serves_the_original_instead_of_the_format(self) -> None:
        """F was the switch's key; it is reused, not left dangling."""
        app = self.script("app.js")
        self.assertIn('key === "f"', app)
        self.assertIn("loadViewerOriginal()", app)


class CaptureVariantMigrationTests(unittest.TestCase):
    def test_v4_upgrade_builds_variants_prunes_visual_edges_and_keeps_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "project.db"
            with closing(connect_db(path)) as conn:
                jpg = insert_photo(conn, "IMG_1000.JPG", decision="keep")
                raw = insert_photo(conn, "IMG_1000.CR3", decision="remove")
                conn.execute(
                    """INSERT INTO similar_pairs(
                         a_id,b_id,score,kind,recommended_id,face_safe
                       ) VALUES(?,?,?,?,?,?)""",
                    (jpg, raw, 1.0, "similar", jpg, 0),
                )
                conn.execute("DROP TABLE capture_variant_members")
                conn.execute("PRAGMA user_version=4")
                conn.commit()
            project_store._INITIALIZED_DATABASES.pop(path.resolve(), None)

            with closing(connect_db(path)) as migrated:
                memberships = {
                    int(row["photo_id"]): int(row["representative_id"])
                    for row in migrated.execute(
                        "SELECT photo_id,representative_id FROM capture_variant_members"
                    )
                }
                decisions = {
                    int(row["id"]): row["decision"]
                    for row in migrated.execute("SELECT id,decision FROM photos")
                }
                similar_count = migrated.execute(
                    "SELECT COUNT(*) FROM similar_pairs WHERE kind='similar'"
                ).fetchone()[0]

            self.assertEqual(memberships, {jpg: jpg, raw: jpg})
            self.assertEqual(decisions, {jpg: "keep", raw: "remove"})
            self.assertEqual(similar_count, 0)
            self.assertEqual(len(list(path.parent.glob("project.pre-v5-*.db"))), 1)


class ViewerTierSwapTests(unittest.TestCase):
    """The viewer fetches pixels to match the display size instead of upscaling.

    The complaint under test: zooming a photo stayed soft even after "view
    original" reported 1:1. The cause was that magnification went through CSS
    ``transform: scale()``, which resamples an already-decoded bitmap -- so a
    7008px photo shown through a 2048px preview was interpolated no matter how
    far the user zoomed. The fix fetches a rendition as wide as the display
    actually needs, so the browser only ever downsamples.

    These tests drive the real ``viewer.js`` source through Node with a stub DOM
    rather than asserting on its text. A text assertion ("the word hysteresis
    appears") passes just as happily when the logic is wrong; executing the
    function is the only way to show the tier actually moves.
    """

    SCRIPT = """
    // Extract the tier machinery from viewer.js and run it against a stub DOM.
    const fs = require("fs");
    // With `node -e` the extra operand lands in argv[1], not argv[2].
    const path = process.argv[1];
    const source = fs.readFileSync(path, "utf8");

    // Minimal DOM good enough for the tier code paths.
    const listeners = new Map();
    function makeEl(id) {
      return {
        id,
        naturalWidth: 0,
        naturalHeight: 0,
        offsetWidth: 0,
        offsetHeight: 0,
        complete: true,
        style: {},
        dataset: {},
        classList: {
          _s: new Set(),
          add(c) { this._s.add(c); },
          remove(c) { this._s.delete(c); },
          contains(c) { return this._s.has(c); },
          toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); },
        },
        _attrs: {},
        // viewer.js assigns `img.src = url` but reads it back with
        // getAttribute("src"). A real element keeps those in sync, so the stub
        // must too -- otherwise a swap looks like it never happened.
        get src() { return this._attrs.src ?? ""; },
        set src(v) { this._attrs.src = v; },
        getAttribute(k) { return this._attrs[k] ?? null; },
        setAttribute(k, v) { this._attrs[k] = v; },
        addEventListener(ev, fn) {
          (listeners.get(this.id + ":" + ev) ?? listeners.set(this.id + ":" + ev, []).get(this.id + ":" + ev)).push(fn);
        },
        removeEventListener() {},
        getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 }),
        parentElement: null,
        focus() {},
      };
    }
    const els = new Map();
    function $(sel) {
      const id = sel.replace(/^#/, "");
      if (!els.has(id)) {
        const el = makeEl(id);
        el.parentElement = makeEl(id + "-parent");
        // The viewer sizes the image by writing width/height, so it derives the
        // "fit" size from the media box rather than from offsetWidth (which now
        // includes the zoom). Give the stub a real box or every fit computation
        // collapses to NaN and the whole harness goes dark.
        el.parentElement.clientWidth = 1521;
        el.parentElement.clientHeight = 1013;
        els.set(id, el);
      }
      return els.get(id);
    }
    const $$ = () => [];
    const toasts = [];
    const toast = (m) => toasts.push(m);

    global.$ = $;
    global.$$ = $$;
    global.toast = toast;
    global.toasts = toasts;
    global.window = { devicePixelRatio: 1 };
    global.document = { querySelector: $ };
    global.state = {
      viewerIndex: 0,
      items: [],
      viewerMotion: { active: false },
      viewerTransform: { scale: 1, x: 0, y: 0, dragging: false },
      viewerTier: 0,
      viewerTierPending: null,
      viewerTierAutoOneToOne: false,
      viewerTierTimer: null,
      viewerOriginalFailed: new Set(),
      viewerClickTimer: null,
    };
    // A controllable Image: tests decide when (and whether) it loads.
    global.__probes = [];
    global.Image = class {
      constructor() {
        this.onload = null;
        this.onerror = null;
        this._src = "";
        global.__probes.push(this);
      }
      set src(v) { this._src = v; }
      get src() { return this._src; }
      addEventListener() {}
      removeEventListener() {}
    };

    // Load viewer.js. It has no module system; everything is a top-level
    // declaration, so appending an export works inside the same scope.
    const src = source + `
;module.exports = {
  viewerPickTier, viewerNeededPixels, viewerBitmapPixels, viewerTierUrl,
  viewerTierWidth, viewerTierLabel, syncViewerTier, loadViewerTier,
  renderViewerScaleHint, viewerIsSourcingDetail, viewerAtPixelCeiling,
  viewerNeedsBetterSource, viewerIsOneToOne, applyViewerTransform,
  resetViewerTransform, loadViewerOriginal,
  viewerShowingOriginal, viewerBitmapBelowSource, viewerOneToOneScale,
  syncViewerOriginalState, renderViewerZoomState,
  viewerWheelPixels, viewerWheelDeviceKind,
  viewerWheelDevice, viewerWheelSensitivity, viewerWheelZoomFactor,
  resetViewerWheelSession, zoomViewer,
  VIEWER_TIER_WIDTHS, VIEWER_TIER_HYSTERESIS, VIEWER_TIER_FAILED,
  VIEWER_WHEEL_BASE, VIEWER_WHEEL_REFERENCE_NOTCH_PX, VIEWER_WHEEL_ZOOM_PER_NOTCH,
  VIEWER_WHEEL_LINE_PX, VIEWER_WHEEL_PAGE_PX, VIEWER_WHEEL_SESSION_GAP_MS,
  VIEWER_WHEEL_MOUSE_DELTA_PX, VIEWER_WHEEL_TRACKPAD_DELTA_PX,
  VIEWER_WHEEL_SENSITIVITY_MIN, VIEWER_WHEEL_SENSITIVITY_MAX,
  _state: state, _probeCount: () => global.__probes.length,
  _probeSrc: (i) => global.__probes[i].src,
  _fireLoad: (i) => global.__probes[i].onload && global.__probes[i].onload(),
  _fireError: (i) => global.__probes[i].onerror && global.__probes[i].onerror(),
  _resetProbes: () => { global.__probes.length = 0; },
};
`;
    const module_ = { exports: {} };
    new Function("module", "exports", "require", src)(
      module_, module_.exports, require,
    );
    global.__viewer = module_.exports;
    // Drive a listener the viewer registered through addEventListener: the stub
    // records them in `listeners` keyed by "<id>:<event>", but nothing ever
    // dispatches them. Needed to exercise the post-load path that auto-lands on
    // 1:1 once the original bitmap decodes (the `1` shortcut has been withdrawn,
    // so that callback is the only remaining way to reach 1:1).
    global.__viewer._fireElement = (id, ev) => {
      (listeners.get(id + ":" + ev) || []).forEach((fn) => fn());
    };
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("需要 node 才能执行 viewer.js 的换源逻辑")
        cls.web = Path(__file__).parents[1] / "web" / "js" / "viewer.js"
        cls.harness = Path(tempfile.mkdtemp()) / "harness.js"
        cls.harness.write_text(cls.SCRIPT, encoding="utf-8")

    def run_viewer(self, body: str) -> dict[str, Any]:
        """Execute ``body`` with ``__viewer`` in scope; return its JSON result."""
        script = f"require({str(self.harness)!r});\n{body}\n"
        completed = subprocess.run(
            [self.node, "-e", script, str(self.web)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if completed.returncode != 0:
            self.fail(f"harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_the_tier_ladder_moves_up_as_the_demand_grows(self) -> None:
        """Reverse proof that the picker is real, not a constant.

        Each step demands more device pixels than the tier below can supply and
        asserts the ladder answered with a higher tier. A picker that ignored
        ``needed`` would return the same index every time and fail step 2.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            const seq = [];
            // Stage width 1521 CSS px (a 3440px-wide window minus the viewer
            // chrome), DPR 1 -- the measured display, not a round number.
            const stage = 1521;
            const at = (needed) => v.viewerPickTier(needed, 0);
            seq.push(at(stage * 1.0));   // 1521  -> 2048 够用（1024 不够）
            seq.push(at(stage * 2.0));   // 3042  -> needs 4096
            seq.push(at(stage * 3.0));   // 4563  -> needs the original
            seq.push(at(stage * 8.0));   // 12168 -> original is all there is
            console.log(JSON.stringify({seq, widths: v.VIEWER_TIER_WIDTHS}));
            """
        )
        # 最小档是 1024，不是 2048：浏览器把大位图缩到很小的时候走快速降采样路径
        # （实测只有 Lanczos 的一半高频），所以首屏要有一档接近显示尺寸的供给。
        self.assertEqual(result["widths"], [1024, 2048, 4096])
        # Non-decreasing, and it must actually reach the top tier (index 3).
        self.assertEqual(result["seq"], sorted(result["seq"]))
        self.assertEqual(result["seq"][0], 1, "需求 1521 应停在 2048 档（1024 不足）")
        self.assertEqual(result["seq"][1], 2, "需求超过 2048 应升到 4096 档")
        self.assertEqual(result["seq"][2], 3, "需求超过 4096 应升到原图档")
        self.assertEqual(result["seq"][3], 3, "超过原图宽度仍只能是原图档")

    def test_hysteresis_stops_boundary_flapping(self) -> None:
        """The failure mode this exists for: oscillating across a threshold.

        Sitting exactly on the 2048 boundary and nudging the zoom by a single
        wheel notch (x1.18) must not walk the tier up and down. Without the
        dead band, every notch would re-request and thrash the cache.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            // Straddle the 2048 boundary from both sides, one notch at a time.
            const start = 2040;
            const notches = [1.18, 1 / 1.18, 1.18, 1 / 1.18, 1.18, 1 / 1.18];
            let needed = start, current = 0, switches = 0;
            const trail = [];
            for (const n of notches) {
              needed *= n;
              const next = v.viewerPickTier(needed, current);
              if (next !== current) switches += 1;
              current = next;
              trail.push([Math.round(needed), current]);
            }
            // And the reverse direction from a firmly-high tier.
            let needed2 = 4090, cur2 = 2, back = 0;
            for (const n of notches) {
              needed2 *= n;
              const next = v.viewerPickTier(needed2, cur2);
              if (next !== cur2) back += 1;
              cur2 = next;
            }
            console.log(JSON.stringify({
              hysteresis: v.VIEWER_TIER_HYSTERESIS,
              notch: 1.18, switches, back, trail,
            }));
            """
        )
        # The dead band must be wider than one wheel notch, or the design is
        # wrong. The band spans needed/hysteresis .. needed, i.e. a factor of
        # 1/0.75 = 1.33; a notch only moves the demand by 1.18. If the notch were
        # the wider of the two, a single scroll could cross the whole band.
        self.assertLess(result["notch"], 1 / result["hysteresis"])
        self.assertLessEqual(
            result["switches"], 2, f"边界抖动：{result['trail']}"
        )
        self.assertLessEqual(result["back"], 2, "缩小时同样不应抖动")

    def test_a_downgrade_needs_the_dead_band_not_merely_a_smaller_demand(self) -> None:
        """A smaller demand is not on its own a reason to drop a tier.

        Between "fits 2048 comfortably" and "comfortably below the next step
        down" the viewer must stay put. Dropping early would re-request the
        smaller tier and then immediately need the bigger one again -- the exact
        ping-pong the band exists to prevent.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            const keep = v.viewerPickTier(1900, 1);   // fits 2048; 1024's band is 768
            const drop = v.viewerPickTier(700, 1);    // inside 1024's dead band
            console.log(JSON.stringify({keep, drop}));
            """
        )
        self.assertEqual(result["keep"], 1, "未进死区就不该降档")
        self.assertEqual(result["drop"], 0, "进入死区才降档")

    def test_zooming_back_out_drops_straight_to_the_small_tier(self) -> None:
        """Cross-tier downgrade, which the first implementation could not do.

        The original rule was "one step down, and only from the tier directly
        above" (``tier + 1 === current``). From the original tier back to the
        fit view that is two or three steps, so the condition was never true and
        the viewer stayed on the source file -- forcing the browser to shrink a
        7008px bitmap down to a few hundred pixels for *every* fit view, which
        is precisely when it is fastest and least accurate (measured: half the
        high-frequency energy of a proper Lanczos downscale).

        So the dead band still governs neighbouring tiers, but a larger gap is
        crossed in one go. One extra request beats staying permanently soft.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            console.log(JSON.stringify({
              fromOriginal: v.viewerPickTier(900, 3),   // 原图档 -> 1024
              fromHigh: v.viewerPickTier(900, 2),       // 4096 -> 1024
              stillBanded: v.viewerPickTier(1900, 1),   // 相邻档仍受死区约束
            }));
            """
        )
        self.assertEqual(result["fromOriginal"], 0, "原图档缩回首屏应一次跨到 1024")
        self.assertEqual(result["fromHigh"], 0, "4096 档缩回首屏同样应跨档")
        self.assertEqual(result["stillBanded"], 1, "相邻档不能因为跨档放开了就失去死区")

    def test_the_source_url_actually_carries_the_requested_width(self) -> None:
        """The tier index must reach the wire as ``&w=``.

        This is the assertion that the whole mechanism is not decorative: if the
        URL ignored the tier, every zoom would refetch the same 2048px JPEG and
        the page would stay soft exactly as reported.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            const p = { photo_url: "/api/photo?project_id=p&id=7&token=t&v=0" };
            console.log(JSON.stringify({
              first: v.viewerTierUrl(p, 0),
              mid: v.viewerTierUrl(p, 1),
              high: v.viewerTierUrl(p, 2),
              original: v.viewerTierUrl(p, 3),
              labels: [0, 1, 2, 3].map(v.viewerTierLabel),
            }));
            """
        )
        self.assertIn("w=1024", result["first"])
        self.assertIn("w=2048", result["mid"])
        self.assertIn("w=4096", result["high"])
        # The top tier must NOT carry a width: that is what makes the server
        # send the untouched source file rather than another re-encode.
        self.assertNotIn("w=", result["original"])
        self.assertEqual(result["original"], "/api/photo?project_id=p&id=7&token=t&v=0")
        self.assertEqual(result["labels"], ["1024px", "2048px", "4096px", "原图"])

    def test_swapping_the_source_keeps_the_current_frame_on_screen(self) -> None:
        """A swap must not blank the viewer while the new tier is in flight.

        Regression guard for the obvious implementation: assigning ``img.src``
        directly. That clears the frame immediately, so a NAS hiccup leaves a
        broken image instead of the photo the user was looking at.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            const st = v._state;
            st.items = [{ id: 7, width: 7008, height: 4672, photo_url: "/p",
                          preview_url: "/p&w=2048" }];
            const img = document.querySelector("#viewerImage");
            img._attrs.src = "/p&w=2048";
            img.offsetWidth = 1521; img.offsetHeight = 1013; img.naturalWidth = 2048; img.naturalHeight = 1365;
            st.viewerTransform.scale = 2;
            v.syncViewerTier();
            const duringPending = {
              src: img.getAttribute("src"),
              pending: st.viewerTierPending,
              hidden: document.querySelector("#viewerLoading").classList.contains("hidden"),
            };
            v._fireLoad(0);                       // the probe resolves
            const afterSwap = { src: img.getAttribute("src"), tier: st.viewerTier };
            console.log(JSON.stringify({duringPending, afterSwap}));
            """
        )
        # Still showing the old tier, with the indicator up.
        self.assertEqual(result["duringPending"]["src"], "/p&w=2048")
        self.assertEqual(result["duringPending"]["pending"], 2)
        self.assertFalse(result["duringPending"]["hidden"], "换源时应有可见反馈")
        # Only once the new bitmap has actually loaded does src change.
        self.assertEqual(result["afterSwap"]["src"], "/p&w=4096")
        self.assertEqual(result["afterSwap"]["tier"], 2)

    def test_a_stale_response_for_a_previous_photo_is_discarded(self) -> None:
        """Flipping through photos must not install the wrong image.

        The user pages on while a 4096 request is still open. When it lands, the
        naive code assigns ``img.src`` unconditionally and the *previous* photo's
        pixels end up under the *current* photo's name.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            const st = v._state;
            st.items = [{ id: 7, width: 7008, height: 4672, photo_url: "/p",
                          preview_url: "/p&w=2048" }];
            const img = document.querySelector("#viewerImage");
            img._attrs.src = "/p&w=2048";
            img.offsetWidth = 1521; img.naturalWidth = 2048; img.naturalHeight = 1365;
            st.viewerTransform.scale = 2;
            v.syncViewerTier();
            // The user pages on before the probe resolves.
            st.items = [{ id: 8, width: 7008, height: 4672, photo_url: "/q",
                          preview_url: "/q&w=2048" }];
            v._fireLoad(0);
            console.log(JSON.stringify({
              src: img.getAttribute("src"),
              pending: st.viewerTierPending,
              hidden: document.querySelector("#viewerLoading").classList.contains("hidden"),
            }));
            """
        )
        self.assertEqual(result["src"], "/p&w=2048", "过期响应被装到了当前照片上")
        self.assertIsNone(result["pending"], "过期响应必须清掉在途标记")
        self.assertTrue(result["hidden"], "过期后指示器应收起")

    def test_a_failed_swap_degrades_and_never_leaves_a_blank_frame(self) -> None:
        """An unreachable NAS must cost sharpness, not the picture."""
        result = self.run_viewer(
            """
            const v = __viewer;
            const st = v._state;
            st.items = [{ id: 7, width: 7008, height: 4672, photo_url: "/p",
                          preview_url: "/p&w=2048" }];
            const img = document.querySelector("#viewerImage");
            img._attrs.src = "/p&w=2048";
            img.offsetWidth = 1521; img.naturalWidth = 2048; img.naturalHeight = 1365;
            st.viewerTransform.scale = 2;
            v.syncViewerTier();
            v._fireError(0);
            const afterFail = {
              src: img.getAttribute("src"),
              pending: st.viewerTierPending,
              tier: st.viewerTier,
              indicator: document.querySelector("#viewerLoading").classList.contains("hidden"),
            };
            // Retrying the same broken tier must be suppressed.
            v._resetProbes();
            st.viewerTransform.scale = 2;
            v.syncViewerTier();
            const probesOnRetry = v._probeCount();
            console.log(JSON.stringify({afterFail, probesOnRetry,
                                         toasts: global.toasts.length}));
            """
        )
        self.assertEqual(result["afterFail"]["src"], "/p&w=2048", "失败后画面被清空了")
        self.assertIsNone(result["afterFail"]["pending"])
        self.assertTrue(result["afterFail"]["indicator"])
        # The failed tier is remembered, so the retry does not re-request it.
        self.assertEqual(result["probesOnRetry"], 0, "失败的档位被反复重试")
        self.assertGreaterEqual(result["toasts"], 1, "降级应当告知用户")

    def test_the_same_tier_is_never_requested_twice(self) -> None:
        """Throttling is the first guard; this is the backstop.

        Every ``applyViewerTransform`` schedules a check, and zoom fires that
        hundreds of times. Even if the settle timer and the hysteresis both
        misfire, an unchanged tier must not produce a second request.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            const st = v._state;
            st.items = [{ id: 7, width: 7008, height: 4672, photo_url: "/p",
                          preview_url: "/p&w=1024" }];
            const img = document.querySelector("#viewerImage");
            img._attrs.src = "/p&w=1024";
            img.offsetWidth = 1024; img.naturalWidth = 1024; img.naturalHeight = 683;
            v._resetProbes();
            st.viewerTransform.scale = 1.0;    // needed = 1024 = 首屏档，无需换源
            for (let i = 0; i < 50; i++) v.syncViewerTier();
            console.log(JSON.stringify({probes: v._probeCount()}));
            """
        )
        self.assertEqual(result["probes"], 0, "同一档位被重复请求")

    def test_after_a_swap_the_bitmap_is_never_upscaled(self) -> None:
        """The point of the whole change, asserted as an invariant.

        At every zoom level the served rendition must supply at least as many
        pixels as the display consumes -- otherwise the browser interpolates,
        which is the exact defect being fixed.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            const st = v._state;
            st.items = [{ id: 7, width: 7008, height: 4672, photo_url: "/p",
                          preview_url: "/p&w=2048" }];
            const img = document.querySelector("#viewerImage");
            const stage = 1521;               // measured: 3440px window, DPR 1
            img.offsetWidth = stage; img.offsetHeight = 1013;
            let bitmap = 2048;
            img.naturalWidth = bitmap; img.naturalHeight = Math.round(bitmap / 1.5);
            let tier = 0;
            const violations = [];
            const ceiling = [];       // beyond the source's own pixels
            const SOURCE = 7008;      // this photo's real width
            for (let scale = 1; scale <= 8; scale *= 1.18) {
              st.viewerTransform.scale = scale;
              const needed = v.viewerNeededPixels();
              const want = v.viewerPickTier(needed, tier);
              if (want !== tier) { tier = want; bitmap = v.viewerTierWidth(tier); }
              // The original tier supplies the photo's own pixels.
              const supply = tier >= 2 ? SOURCE : bitmap;
              if (supply < needed) {
                // Past the file's own resolution nothing can supply enough --
                // that is the physical ceiling, not a tier-selection failure.
                (needed > SOURCE ? ceiling : violations).push(
                  [+scale.toFixed(2), Math.round(needed), supply]);
              }
            }
            console.log(JSON.stringify({violations, ceiling, stage,
                                         dpr: window.devicePixelRatio}));
            """
        )
        self.assertEqual(result["dpr"], 1)
        # Within what the file can supply, no zoom level may upscale.
        self.assertEqual(
            result["violations"], [], "存在仍在插值的缩放档位"
        )
        # And the ceiling is genuinely reached (otherwise the check above is
        # vacuous -- it would pass simply by never scaling far enough).
        self.assertTrue(result["ceiling"], "测试没有覆盖到原图的物理上限")
        self.assertTrue(
            all(needed > 7008 for _, needed, _ in result["ceiling"]),
            "上限之外的需求不应超过原图宽度",
        )

    def test_zooming_past_the_original_reports_an_honest_ceiling(self) -> None:
        """Beyond the source resolution, interpolation is physics, not a bug.

        The old wording ("已插值 N%，细节非原始像素") reads like a defect the app
        could fix. Once the source-swap works, the only time interpolation remains
        is when the user magnifies past the file's own pixels -- so the hint must
        say so instead of implying something is broken.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            const st = v._state;
            st.items = [{ id: 7, width: 7008, height: 4672, photo_url: "/p",
                          preview_url: "/p&w=2048" }];
            const img = document.querySelector("#viewerImage");
            img.offsetWidth = 1521; img.offsetHeight = 1013;
            img.naturalWidth = 7008; img.naturalHeight = 4672;              // the original is mounted
            img._attrs.src = "/p";
            st.viewerTier = 2;

            const at = (scale) => {
              st.viewerTransform.scale = scale;
              v.renderViewerScaleHint();
              return document.querySelector("#viewerScaleHint").textContent;
            };
            console.log(JSON.stringify({
              fitting: at(1),
              oneToOne: at(7008 / 1521),
              wayPast: at((7008 / 1521) * 2),
            }));
            """
        )
        self.assertIn("整幅显示", result["fitting"])
        self.assertIn("已 1:1", result["oneToOne"])
        self.assertIn("已到原始像素上限", result["wayPast"])

    def test_the_hint_admits_it_is_waiting_for_a_sharper_source(self) -> None:
        """A swap in flight is a state the old three-state model had no name for.

        Reporting "已 1:1" while the 2048px bitmap is still mounted is exactly the
        lie that produced this bug report, so the pending state must be checked
        before the 1:1 branch.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            const st = v._state;
            st.items = [{ id: 7, width: 7008, height: 4672, photo_url: "/p",
                          preview_url: "/p&w=2048" }];
            const img = document.querySelector("#viewerImage");
            img.offsetWidth = 1521; img.naturalWidth = 2048; img.naturalHeight = 1365;
            img._attrs.src = "/p&w=2048";
            st.viewerTransform.scale = 2;
            st.viewerTierPending = 1;
            v.renderViewerScaleHint();
            const hint = document.querySelector("#viewerScaleHint").textContent;
            console.log(JSON.stringify({hint}));
            """
        )
        self.assertIn("细节加载中", result["hint"])
        self.assertNotIn("已 1:1", result["hint"])

    def test_the_original_is_not_declared_loaded_for_a_mid_tier_rendition(self) -> None:
        """The button and loadViewerOriginal must not trust a prefix match.

        The complaint under test: after "view original" the 1:1 button did not
        show the original's 1:1. The cause was that the client builds mid-tier
        URLs as ``photo_url + "&w=2048"``, and ``viewerShowingOriginal``
        compared everything *before* ``&w=`` to ``photo_url`` -- so every
        mid-tier swap looked like the source file. Consequences: (1) the
        button claimed 原图 while a 2048px bitmap was mounted, (2)
        ``loadViewerOriginal`` early-returned so the real file was never
        fetched, (3) 1:1 then landed on the 2048px bitmap's 1:1 instead of the
        7008px file's.

        Reverse proof: pointing src at the exact ``photo_url`` must flip the
        verdict back to true, so a function that always answered one way fails.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            const st = v._state;
            const p = { id: 7, width: 7008, height: 4672,
                        photo_url: "/api/photo?project_id=p&id=7&token=t&v=0",
                        preview_url: "/api/photo?project_id=p&id=7&token=t&w=2048&v=0" };
            st.items = [p];
            st.viewerTier = 1;
            const img = document.querySelector("#viewerImage");
            img.offsetWidth = 1521; img.offsetHeight = 1013;
            img.naturalWidth = 2048; img.naturalHeight = 1365;
            // The client's own mid-tier URL (photo_url + &w=2048), which is
            // what the viewer mounts the moment it swaps up on open.
            const midTierUrl = v.viewerTierUrl(p, 1);
            img._attrs.src = midTierUrl;
            const midTierClaimsOriginal = v.viewerShowingOriginal();
            // Now ask for the original: it must actually go fetch (probe).
            v._resetProbes();
            v.loadViewerOriginal();
            const probed = v._probeCount();
            const pending = st.viewerTierPending;
            // The exact source URL must be recognised as the original.
            img._attrs.src = p.photo_url;
            const exactClaimsOriginal = v.viewerShowingOriginal();
            console.log(JSON.stringify({midTierClaimsOriginal, probed, pending,
              exactClaimsOriginal, midTierUrl}));
            """
        )
        self.assertIn("&w=2048", result["midTierUrl"])
        self.assertFalse(
            result["midTierClaimsOriginal"], "中间档 URL 被误判成原图"
        )
        self.assertEqual(result["probed"], 1, "点了查看原图却没有下发原图")
        self.assertEqual(result["pending"], 3, "在途目标应为原图档")
        self.assertTrue(result["exactClaimsOriginal"], "挂牌原图后应被识别为原图")

    def test_one_to_one_on_the_original_is_pixel_exact(self) -> None:
        """At 1:1 the displayed width must equal the source pixel width.

        This is the promise the whole feature makes: one source pixel lands on
        one screen pixel. The layout width is capped at the 1:1 step and the
        remainder rides in ``transform: scale``; the proof is that
        ``width * residual`` still equals naturalWidth -- i.e. the residual is
        a genuine no-op at the 1:1 point rather than a wrapper that quietly
        re-scales.

        Reverse proof: if the residual were dropped (transform left at
        ``scale(1)``) the product would fall to naturalWidth / oneToOne, and if
        the layout were left at the fit size it would be the fit width; both
        fail the exact equality below.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            const st = v._state;
            st.items = [{ id: 7, width: 7008, height: 4672, photo_url: "/p",
                          preview_url: "/p&w=2048" }];
            const img = document.querySelector("#viewerImage");
            img.offsetWidth = 1521; img.offsetHeight = 1013;
            img.naturalWidth = 7008; img.naturalHeight = 4672;
            img._attrs.src = "/p";
            const fitW = 7008 * Math.min(1521 / 7008, 1013 / 4672, 1);
            const oneToOne = v.viewerOneToOneScale();
            const at = (scale) => {
              st.viewerTransform.scale = scale;
              st.viewerTransform.x = 0; st.viewerTransform.y = 0;
              v.applyViewerTransform();
              const residual = Number(
                (img.style.transform || "").split("scale(")[1].split(")")[0]);
              const width = parseFloat(img.style.width);
              return { scale, width, residual, shown: width * residual,
                       natural: img.naturalWidth };
            };
            st.viewerTransform.scale = 1; v.applyViewerTransform();
            const r1 = at(oneToOne);
            const below = at(oneToOne * 0.5);
            const above = at(oneToOne * 2);
            console.log(JSON.stringify({oneToOne, fitW, r1, below, above}));
            """
        )
        self.assertAlmostEqual(result["oneToOne"], 7008 / result["fitW"], places=6)
        # At the 1:1 point the source pixel is the screen pixel, exactly.
        self.assertEqual(result["r1"]["residual"], 1)
        self.assertEqual(round(result["r1"]["width"]), 7008)
        self.assertEqual(round(result["r1"]["shown"]), result["r1"]["natural"])
        self.assertEqual(round(result["r1"]["shown"]), 7008)
        # The layout is capped at the source width, and the residual really is
        # applied: displayed size must track fit x scale on both sides of 1:1.
        self.assertLessEqual(result["above"]["width"], 7008 + 1e-6)
        self.assertAlmostEqual(
            result["below"]["shown"], result["fitW"] * result["below"]["scale"], places=3
        )
        self.assertAlmostEqual(
            result["above"]["shown"], result["fitW"] * result["above"]["scale"], places=3
        )

    def test_the_hint_will_not_call_a_preview_bitmap_one_to_one(self) -> None:
        """A preview's 1:1 is not the original's 1:1 and must not be named so.

        The honesty requirement: while only a downscaled rendition is mounted,
        reaching its 1:1 satisfies "one bitmap pixel per screen pixel" but not
        "one source pixel per screen pixel", so the hint must not claim
        "已 1:1".

        Reverse proof: the same assertions are repeated with the original
        mounted, where "已 1:1" IS the honest wording -- so a hint that simply
        never said it would fail the second half.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            const st = v._state;
            st.items = [{ id: 7, width: 7008, height: 4672, photo_url: "/p",
                          preview_url: "/p&w=2048" }];
            const img = document.querySelector("#viewerImage");
            img.offsetWidth = 1521; img.offsetHeight = 1013;
            const at = (natW, natH, src, tier) => {
              img.naturalWidth = natW; img.naturalHeight = natH;
              img._attrs.src = src;
              st.viewerTier = tier;
              st.viewerTransform.scale = natW /
                (natW * Math.min(1521 / natW, 1013 / natH, 1));
              v.renderViewerScaleHint();
              const el = document.querySelector("#viewerScaleHint");
              return { text: el.textContent,
                       exact: el.classList.contains("viewer-scale-hint-exact") };
            };
            const previewOne = at(2048, 1365, "/p&w=2048", 1);
            const original = at(7008, 4672, "/p", 3);
            console.log(JSON.stringify({previewOne, original}));
            """
        )
        self.assertNotIn("已 1:1", result["previewOne"]["text"])
        self.assertIn("非原图 1:1", result["previewOne"]["text"])
        self.assertFalse(result["previewOne"]["exact"])
        self.assertIn("已 1:1", result["original"]["text"])
        self.assertTrue(result["original"]["exact"])

    def test_viewing_the_original_auto_lands_on_one_to_one(self) -> None:
        """「查看原图」到货后必须自动落到真实 1:1。

        撤销键盘 `1` 之后，「查看原图」载入原图后的自动落位是 1:1 唯一的触发入口
        （见 README「大图预览」一节）。本用例走的是**真实的 loadViewerTier 到货
        回调**：强制换到原图档 -> 预加载探针到货 -> 原图位图解码完成 -> 自动落位，
        而不是直接调用某个内部函数。

        反向证明：断言落位后的 scale 严格大于 1（7008px 的原图放在 1521px 的舞台上
        必然要求放大）。若到货回调没有落位，scale 会停在 resetViewerTransform() 之后
        的 1，assertGreater 即失败——这条断言无法靠「原地不动」蒙混过关。
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            const st = v._state;
            st.items = [{ id: 7, width: 7008, height: 4672, photo_url: "/p",
                          preview_url: "/p&w=2048" }];
            const img = document.querySelector("#viewerImage");
            img.offsetWidth = 1521; img.offsetHeight = 1013;
            img.naturalWidth = 2048; img.naturalHeight = 1365;
            img._attrs.src = "/p&w=2048";
            st.viewerTransform.scale = 1;
            st.viewerTransform.x = 0; st.viewerTransform.y = 0;
            // The user clicks 查看原图: skip hysteresis/throttle, request the top tier.
            v.loadViewerOriginal();
            const forced = { pending: st.viewerTierPending,
                             auto: st.viewerTierAutoOneToOne };
            // The pre-load probe resolves: mount the original and register the
            // <img> load callback.
            v._fireLoad(0);
            // The original bitmap has now decoded -- this is the moment 1:1 is real.
            img.naturalWidth = 7008; img.naturalHeight = 4672;
            img._attrs.src = "/p";
            v._fireElement("viewerImage", "load");
            const landed = { scale: st.viewerTransform.scale,
                             oneToOne: v.viewerOneToOneScale(),
                             x: st.viewerTransform.x,
                             y: st.viewerTransform.y,
                             width: parseFloat(img.style.width) };
            console.log(JSON.stringify({forced, landed}));
            """
        )
        self.assertEqual(result["forced"]["pending"], 3, "查看原图应请求原图档")
        self.assertTrue(
            result["forced"]["auto"], "原图档到货后应交给自动落位那段逻辑"
        )
        # Landed exactly on the 1:1 point, not left at the fit scale.
        self.assertAlmostEqual(
            result["landed"]["scale"], result["landed"]["oneToOne"], places=9
        )
        self.assertGreater(
            result["landed"]["scale"], 1.0, "到货后仍停在整幅显示，未落到 1:1"
        )
        # Destination semantics: pan is centred, not left offset by the jump.
        self.assertEqual(result["landed"]["x"], 0)
        self.assertEqual(result["landed"]["y"], 0)
        # The layout width is capped at the 1:1 step: width * residual == naturalWidth.
        self.assertEqual(round(result["landed"]["width"]), 7008)


class ViewerWheelZoomTests(unittest.TestCase):
    """Viewer scroll-zoom tracks the scroll amount and the input device.

    The complaint under test: one wheel notch zoomed too much. The step used to
    be a hardcoded ``deltaY < 0 ? 1.18 : 1/1.18`` that ignored how far the user
    scrolled and which device produced the event.

    These tests drive the real ``viewer.js`` through the same Node harness the
    tier tests use, so they fail if the calibration is wrong, if the device
    heuristic never switches, or if the two per-device sensitivities are not
    actually consulted. Where it matters an assertion is paired with a reverse
    check: a factor that ignored the sensitivity, or a classifier that always
    answered the same way, must NOT be able to pass.
    """

    # Reuse the tier harness (stub DOM + real viewer.js) without re-running its
    # tests: borrow the machinery by reference rather than by inheritance.
    SCRIPT = ViewerTierSwapTests.SCRIPT
    run_viewer = ViewerTierSwapTests.run_viewer
    setUp = ViewerTierSwapTests.setUp
    tearDown = ViewerTierSwapTests.tearDown

    @classmethod
    def setUpClass(cls) -> None:
        # The borrowed classmethod is already bound to ViewerTierSwapTests, so
        # calling it directly would stash ``harness``/``web`` on the wrong class.
        # Call the underlying function with THIS class instead.
        ViewerTierSwapTests.setUpClass.__func__(cls)

    def test_default_maps_one_reference_notch_to_six_percent(self) -> None:
        """A canonical notched-wheel step (100px) at 1.0 must be x1.06.

        Reverse proof: the old fixed step was x1.18. The final assertion pins
        "milder than before", so restoring x1.18 -- or any factor unrelated to
        the scroll distance -- fails here rather than merely being unasserted.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            v._state.settings = {};          // defaults: both sensitivities 1.0
            v.resetViewerWheelSession(0);
            const up = v.viewerWheelZoomFactor({deltaY: -100, deltaMode: 0, timeStamp: 1});
            v.resetViewerWheelSession(0);
            const down = v.viewerWheelZoomFactor({deltaY: 100, deltaMode: 0, timeStamp: 1});
            console.log(JSON.stringify({up, down, product: up * down}));
            """
        )
        self.assertAlmostEqual(result["up"], 1.06, places=6)
        self.assertAlmostEqual(result["down"], 1 / 1.06, places=6)
        # exp(a) * exp(-a) == 1: zoom in N notches then out N notches returns.
        self.assertAlmostEqual(result["product"], 1.0, places=9)
        self.assertLess(result["up"], 1.18)

    def test_sensitivity_scales_the_factor_and_is_clamped(self) -> None:
        """The slider really multiplies the step; out-of-range values clamp.

        Reverse proof: a factor independent of ``deviceSensitivity`` would make
        all three readings equal and the strict inequalities below fail.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            const ev = {deltaY: -100, deltaMode: 0, timeStamp: 1};
            const at = (sens) => {
              v._state.settings = {viewer_wheel_device: "mouse",
                                   viewer_wheel_mouse_sensitivity: sens};
              v.resetViewerWheelSession(0);
              return v.viewerWheelZoomFactor(ev);
            };
            console.log(JSON.stringify({half: at(0.5), one: at(1.0), two: at(2.0),
                                        over: at(5.0), under: at(0.1)}));
            """
        )
        self.assertAlmostEqual(result["one"], 1.06, places=6)
        self.assertAlmostEqual(result["two"], 1.06**2, places=6)
        self.assertAlmostEqual(result["half"], 1.06**0.5, places=6)
        self.assertLess(result["half"], result["one"])
        self.assertLess(result["one"], result["two"])
        # Clamp: 5.0 -> 2.0 and 0.1 -> 0.5, so the extremes equal the endpoints.
        self.assertAlmostEqual(result["over"], result["two"], places=9)
        self.assertAlmostEqual(result["under"], result["half"], places=9)

    def test_trackpad_and_mouse_sensitivities_are_independent(self) -> None:
        """Two devices, two settings -- each event uses its own sensitivity.

        Reverse proof: if the handler read a single shared sensitivity the two
        factors below would be equal.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            v._state.settings = {viewer_wheel_trackpad_sensitivity: 2.0,
                                 viewer_wheel_mouse_sensitivity: 0.5,
                                 viewer_wheel_device: "auto"};
            v.resetViewerWheelSession(0);
            const mouse = v.viewerWheelZoomFactor({deltaY: -100, deltaMode: 0, timeStamp: 1});
            v.resetViewerWheelSession(0);
            const trackpad = v.viewerWheelZoomFactor({deltaY: -2, deltaMode: 0, timeStamp: 1});
            const base = v.VIEWER_WHEEL_BASE;
            console.log(JSON.stringify({mouse, trackpad,
              mouseExpected: Math.exp(100 * base * 0.5),
              trackpadExpected: Math.exp(2 * base * 2.0)}));
            """
        )
        self.assertAlmostEqual(result["mouse"], result["mouseExpected"], places=9)
        self.assertAlmostEqual(
            result["trackpad"], result["trackpadExpected"], places=9
        )
        self.assertNotAlmostEqual(result["mouse"], result["trackpad"], places=3)

    def test_device_heuristic_switches_on_real_scroll_signatures(self) -> None:
        """Discrete large deltas -> mouse; dense tiny deltas -> trackpad.

        Reverse proof: a classifier that always answered one device would fail
        one half, and flipping only ``deltaMode`` -- the documented, device-
        correlated unit signal -- must be enough to flip the verdict.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            v.resetViewerWheelSession(0);
            const mouse = v.viewerWheelDeviceKind({deltaY: -100, deltaMode: 0, timeStamp: 1});
            // A trackpad gesture: many small, strongly varying pixel deltas.
            v.resetViewerWheelSession(0);
            const mags = [1, 4, 1.5, 5, 2, 6, 1];
            let trackpad = "";
            mags.forEach((m, i) => {
              trackpad = v.viewerWheelDeviceKind(
                {deltaY: m, deltaMode: 0, timeStamp: 10 + i * 8});
            });
            // Firefox-style line mode on a notched wheel: one line is a notch.
            v.resetViewerWheelSession(0);
            const lines = v.viewerWheelDeviceKind({deltaY: 1, deltaMode: 1, timeStamp: 1});
            console.log(JSON.stringify({mouse, trackpad, lines}));
            """
        )
        self.assertEqual(result["mouse"], "mouse")
        self.assertEqual(result["trackpad"], "trackpad")
        self.assertEqual(result["lines"], "mouse")
        self.assertNotEqual(result["mouse"], result["trackpad"])

    def test_manual_override_beats_the_heuristic(self) -> None:
        """The 输入设备 choice wins over automatic detection.

        Reverse proof: with the override ignored, the tiny trackpad-shaped
        delta would classify as trackpad and the first assertion would fail.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            v.resetViewerWheelSession(0);
            v._state.settings = {viewer_wheel_device: "mouse"};
            const forcedMouse = v.viewerWheelDevice({deltaY: -2, deltaMode: 0, timeStamp: 1});
            v.resetViewerWheelSession(0);
            v._state.settings = {viewer_wheel_device: "trackpad"};
            const forcedTrackpad = v.viewerWheelDevice({deltaY: -100, deltaMode: 0, timeStamp: 1});
            v.resetViewerWheelSession(0);
            v._state.settings = {viewer_wheel_device: "auto"};
            const autoSmall = v.viewerWheelDevice({deltaY: -2, deltaMode: 0, timeStamp: 1});
            console.log(JSON.stringify({forcedMouse, forcedTrackpad, autoSmall}));
            """
        )
        self.assertEqual(result["forcedMouse"], "mouse")
        self.assertEqual(result["forcedTrackpad"], "trackpad")
        # Auto mode on the same small event agrees with the raw heuristic.
        self.assertEqual(result["autoSmall"], "trackpad")

    def test_delta_mode_is_normalised_to_pixels(self) -> None:
        """Line/page deltas are converted to pixels before the exponent.

        Reverse proof: if ``deltaMode`` were ignored, the line-mode factor would
        equal the raw 3px factor instead of the 48px equivalent.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            v._state.settings = {viewer_wheel_device: "mouse"};
            const px = v.viewerWheelPixels({deltaY: 100, deltaMode: 0});
            const lines = v.viewerWheelPixels({deltaY: 3, deltaMode: 1});
            const pages = v.viewerWheelPixels({deltaY: 1, deltaMode: 2});
            v.resetViewerWheelSession(0);
            const lineFactor = v.viewerWheelZoomFactor({deltaY: -3, deltaMode: 1, timeStamp: 1});
            v.resetViewerWheelSession(0);
            const equivalent = v.viewerWheelZoomFactor(
              {deltaY: -3 * v.VIEWER_WHEEL_LINE_PX, deltaMode: 0, timeStamp: 1});
            v.resetViewerWheelSession(0);
            const rawThree = v.viewerWheelZoomFactor({deltaY: -3, deltaMode: 0, timeStamp: 1});
            console.log(JSON.stringify({px, lines, pages, lineFactor, equivalent, rawThree,
              linePx: v.VIEWER_WHEEL_LINE_PX, pagePx: v.VIEWER_WHEEL_PAGE_PX}));
            """
        )
        self.assertEqual(result["px"], 100)
        self.assertEqual(result["lines"], 3 * result["linePx"])
        self.assertEqual(result["pages"], result["pagePx"])
        self.assertAlmostEqual(result["lineFactor"], result["equivalent"], places=9)
        self.assertGreater(result["lineFactor"], result["rawThree"])

    def test_a_long_gap_starts_a_new_session(self) -> None:
        """A mouse scroll must not keep classifying the next trackpad gesture.

        Reverse proof: the three notched events leave a high running mouse
        score. If the session were never reopened, that score would outlast the
        gap and the small event after it would misread as a mouse; the final
        assertion pins the reset specifically.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            const notched = (t) => v.viewerWheelDeviceKind(
              {deltaY: -100, deltaMode: 0, timeStamp: t});
            v.resetViewerWheelSession(0);
            notched(1); notched(40); notched(80);       // running mouse score = 6
            // Same session, no gap: the mouse history is retained -> mouse.
            const sameSessionSmall = v.viewerWheelDeviceKind(
              {deltaY: -2, deltaMode: 0, timeStamp: 120});
            // A >250ms gap reopens the session, so the small event is judged
            // alone -> trackpad. Without the reset it would still be "mouse".
            const newSessionSmall = v.viewerWheelDeviceKind(
              {deltaY: -2, deltaMode: 0, timeStamp: 1000});
            console.log(JSON.stringify({sameSessionSmall, newSessionSmall}));
            """
        )
        self.assertEqual(result["sameSessionSmall"], "mouse")
        self.assertEqual(result["newSessionSmall"], "trackpad")

    def test_fit_highlight_marks_only_the_fit_view_state(self) -> None:
        """适应 lights only while the photo is shown whole.

        The 1:1 button was removed, so 适应 (scale 1, photo larger than the
        stage) is the only segmented-control state left to highlight. The
        criterion is shared with the scale hint, so a photo that already IS
        1:1 at scale 1 must not light 适应 either.

        Reverse proof: the three expectations differ, so a sync that always lit
        or never lit the button would fail at least one case.
        """
        result = self.run_viewer(
            """
            const v = __viewer;
            const st = v._state;
            const fit = document.querySelector("#viewerFit");
            const img = document.querySelector("#viewerImage");
            img.parentElement.clientWidth = 1521;
            img.parentElement.clientHeight = 1013;
            const active = () => fit.classList.contains("viewer-zoom-active");
            // Big photo at fit: 整幅显示 -> 适应 lit.
            img.naturalWidth = 4000; img.naturalHeight = 2667; img.offsetWidth = 1520;
            st.viewerTransform.scale = 1;
            v.renderViewerZoomState();
            const atFit = active();
            // A photo smaller than the stage IS 1:1 at scale 1 -> 适应 not lit.
            img.naturalWidth = 1000; img.naturalHeight = 667; img.offsetWidth = 1000;
            st.viewerTransform.scale = 1;
            v.renderViewerZoomState();
            const atOneToOne = active();
            // Zoomed to an intermediate倍率: 适应 is not the current state.
            img.naturalWidth = 4000; img.naturalHeight = 2667; img.offsetWidth = 1520;
            st.viewerTransform.scale = 3;
            v.renderViewerZoomState();
            const zoomed = active();
            console.log(JSON.stringify({atFit, atOneToOne, zoomed}));
            """
        )
        self.assertTrue(result["atFit"])
        self.assertFalse(result["atOneToOne"])
        self.assertFalse(result["zoomed"])


class DisplayPreviewEncodingTests(unittest.TestCase):
    """The preview encoder and its cache must actually change together.

    A chroma-subsampling fix that leaves the cache key alone is inert: every
    already-cached photo keeps being served the old bytes, and the fix appears
    to do nothing. ``display_asset.ASSET_FORMAT_TAG`` already fixed this once for
    card thumbnails; the display preview had the same latent bug.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "IMG.JPG"
        Image.new("RGB", (3000, 2000), (120, 90, 60)).save(
            self.source, "JPEG", quality=95
        )
        self.thumbnail = self.root / "IMG.thumb.jpg"
        Image.new("RGB", (512, 341), (120, 90, 60)).save(
            self.thumbnail, "JPEG"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_previews_keep_full_chroma_resolution(self) -> None:
        """4:2:0 halves the chroma plane; the viewer is for judging detail."""
        preview = ensure_display_preview(
            self.source, self.thumbnail, max_size=(2048, 2048)
        )
        with Image.open(preview) as image:
            layers = image.layer
        # Pillow reports JPEG components as (id, h, v, quantisation table id).
        # YCbCr 4:2:0 halves the chroma planes in BOTH axes -- they come back as
        # (2, 1, 1, ...) and (3, 1, 1, ...). 4:4:4 keeps every plane full size,
        # so both chroma planes must read (2, 1, 1) / (3, 1, 1) *and* the luma
        # plane (1, 1, 1). Asserting the luma plane alone would pass for 4:2:0
        # too, so the chroma planes are what actually carry the claim.
        self.assertEqual(layers[0][:3], (1, 1, 1), f"亮度平面被减半：{layers}")
        self.assertEqual(layers[1][:3], (2, 1, 1), f"色度 Cb 被减半：{layers}")
        self.assertEqual(layers[2][:3], (3, 1, 1), f"色度 Cr 被减半：{layers}")

    def test_the_encoder_tag_is_part_of_the_cache_key(self) -> None:
        """Changing encoder settings must invalidate existing cached files."""
        before = display_preview_path(self.source, self.thumbnail, (2048, 2048))
        with mock.patch.object(
            media, "DISPLAY_PREVIEW_FORMAT_TAG", "q92-s444-v2"
        ):
            after = display_preview_path(
                self.source, self.thumbnail, (2048, 2048)
            )
        self.assertNotEqual(
            before, after, "编码参数没进指纹，旧缓存会继续被命中"
        )

    def test_sibling_widths_survive_each_other(self) -> None:
        """The 2048 and 4096 renditions of one photo must coexist.

        The prune used to keep a single slot and delete whichever rendition was
        built second. Under a three-tier viewer that means every zoom-in and
        zoom-out re-decodes the source, which on this library costs a ~20MB read
        from the NAS each time.
        """
        narrow = ensure_display_preview(
            self.source, self.thumbnail, max_size=(2048, 2048)
        )
        wide = ensure_display_preview(
            self.source, self.thumbnail, max_size=(4096, 4096)
        )
        self.assertTrue(narrow.is_file(), "建完 4096 之后 2048 档被删了")
        self.assertTrue(wide.is_file())

    def test_stale_fingerprints_of_the_same_width_are_still_pruned(self) -> None:
        """Coexisting tiers must not turn the cache into a leak.

        Within one width the fingerprint is the only thing that can go stale
        (the source file changed), and that is still what gets cleaned up.
        """
        narrow = ensure_display_preview(
            self.source, self.thumbnail, max_size=(2048, 2048)
        )
        wide = ensure_display_preview(
            self.source, self.thumbnail, max_size=(4096, 4096)
        )
        # Change the source so its size/mtime fingerprint moves.
        self.source.write_bytes(self.source.read_bytes() + b"\x00")
        rebuilt = ensure_display_preview(
            self.source, self.thumbnail, max_size=(2048, 2048)
        )
        same_width = sorted(
            path.name for path in self.root.glob("*.display-w2048-*.jpg")
        )
        self.assertEqual(len(same_width), 1, f"同宽度的旧指纹没清理：{same_width}")
        self.assertNotEqual(narrow, rebuilt)
        self.assertTrue(wide.is_file(), "剪枝误伤了其它档位")


class SimilarGroupRawFoldingTests(unittest.TestCase):
    """A similarity group shows one card per exposure, RAW folded away.

    The photo grid already folds a capture's RAW into its JPEG
    (capture_variant_collapse_clause). The similar view used to hand the
    browser the whole variant list instead, which put every RAW back on
    screen next to the JPEG it belongs to. These tests pin the rule and,
    more importantly, its edges: a RAW that is the *only* copy of a shot has
    to stay, or the user would be deciding on a file they cannot see.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.photos = self.root / "photos"
        self.photos.mkdir()
        self.config = ConfigStore(self.root / "config.json")
        self.config.data["default_cache_root"] = str(self.root / "cache")
        self.config.save()
        self.manager = ProjectManager(self.config)
        self.project = self.manager.open(str(self.photos))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def application(self) -> http_api.ApplicationContext:
        scanner = Scanner(self.config, self.manager)
        return http_api.ApplicationContext(
            self.config,
            self.manager,
            scanner,
            SimilarityGroupCache(),
            "test-token",
            Path("web"),
        )

    def link(
        self, conn: Any, left: int, right: int, recommended: int, kind: str
    ) -> None:
        conn.execute(
            """INSERT INTO similar_pairs(
                 a_id,b_id,score,kind,recommended_id,face_safe
               ) VALUES(?,?,?,?,?,?)""",
            (left, right, 0.95, kind, recommended, 0),
        )

    def detail(self) -> dict[str, Any]:
        """The single similarity group, as the detail pane receives it."""
        application = self.application()
        listing = application.photo_queries.similar_groups(
            {"project_id": [self.project.project_id]}
        )
        self.assertEqual(listing["total"], 1)
        return application.photo_queries.similar_group(
            {
                "project_id": [self.project.project_id],
                "group_id": [listing["items"][0]["id"]],
            }
        )

    def test_a_raw_without_a_jpeg_sibling_stays_on_screen(self) -> None:
        """A RAW-only camera must not lose its photos from the review.

        Folding is a statement about *variants*: the RAW is hidden because a
        JPEG shows the same exposure. With no JPEG there is nothing to fold
        into, so the capture keeps its card -- hiding it would let a decision
        be taken on a file that was never displayed.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            solo_raw = insert_photo(conn, "SOLO_5001.CR3", size=20_000_000)
            mate = insert_photo(conn, "MATE_5001.JPG")
            rebuild_capture_variants(conn)
            self.assertNotIn(
                solo_raw,
                {
                    int(row["photo_id"])
                    for row in conn.execute(
                        "SELECT photo_id FROM capture_variant_members"
                    )
                },
            )
            self.link(conn, solo_raw, mate, solo_raw, "similar")
            conn.commit()

        detail = self.detail()
        self.assertEqual(detail["count"], 2)
        self.assertEqual(
            {int(item["id"]) for item in detail["members"]}, {solo_raw, mate}
        )
        raw_card = next(
            item for item in detail["members"] if int(item["id"]) == solo_raw
        )
        self.assertEqual(raw_card["format_category"], "raw")
        self.assertIsNotNone(raw_card["quality_score"])
        # No sibling, so nothing to announce.
        self.assertEqual(raw_card["variant_extensions"], [])

    def test_an_unreadable_jpeg_never_takes_the_card_from_a_raw(self) -> None:
        """The substitute has to be readable, not merely non-RAW.

        When the JPEG of an exposure is corrupt the capture's representative
        *is* the RAW, and a RAW that opens fine is a far better card than the
        broken JPEG sitting next to it in the variant list.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            broken_jpg = insert_photo(conn, "BROKEN_5002.JPG", error="boom")
            raw = insert_photo(conn, "BROKEN_5002.CR3", size=20_000_000)
            mate = insert_photo(conn, "MATE_5002.JPG")
            rebuild_capture_variants(conn)
            # The readable RAW wins the representative slot precisely because
            # the JPEG is unreadable -- that is the case under test.
            representative = {
                int(row["representative_id"])
                for row in conn.execute(
                    """SELECT representative_id FROM capture_variant_members
                        WHERE photo_id=?""",
                    (raw,),
                )
            }
            self.assertEqual(representative, {raw})
            self.link(conn, raw, mate, raw, "similar")
            conn.commit()

        detail = self.detail()
        self.assertEqual(detail["count"], 2)
        self.assertIn(raw, {int(item["id"]) for item in detail["members"]})
        self.assertNotIn(
            broken_jpg, {int(item["id"]) for item in detail["members"]}
        )
        raw_card = next(
            item for item in detail["members"] if int(item["id"]) == raw
        )
        self.assertEqual(raw_card["format_category"], "raw")
        self.assertIsNotNone(raw_card["quality_score"])
        # The RAW is still announced as the capture's card.
        self.assertEqual(raw_card["variant_extensions"], ["CR3", "JPG"])

    def test_a_raw_member_renders_as_its_jpeg_and_keeps_the_recommendation(
        self,
    ) -> None:
        """`recommended_id` addresses members, so a RAW can hold it.

        The recommendation ranks members by face/quality/path only -- it has
        no reason to prefer a JPEG. When the winner happens to be a RAW, the
        card shown for it is the JPEG, and the frontend recognises the
        recommendation by comparing `similarity_source_id` (the member) with
        `recommended_id`, so the badge still lands on the right card.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "AAA_5003.JPG")
            raw = insert_photo(conn, "AAA_5003.CR3", size=20_000_000)
            mate = insert_photo(conn, "ZZZ_5003.JPG")
            rebuild_capture_variants(conn)
            # 'aaa_5003.cr3' sorts before 'zzz_5003.jpg' and both score the
            # same, so the RAW takes the recommendation.
            self.link(conn, raw, mate, raw, "similar")
            conn.commit()

        detail = self.detail()
        self.assertEqual(detail["recommended_id"], raw)
        self.assertEqual(detail["count"], 2)
        self.assertEqual(
            {int(item["id"]) for item in detail["members"]}, {jpg, mate}
        )
        card = next(
            item for item in detail["members"] if int(item["id"]) == jpg
        )
        # The card is the JPEG, but it still answers to its own member id, so
        # `(similarity_source_id || id) === recommended_id` holds for it.
        self.assertEqual(card["similarity_source_id"], raw)
        self.assertEqual(card["format_category"], "jpeg")

    def test_a_group_never_goes_empty_when_two_members_share_one_capture(
        self,
    ) -> None:
        """Self-similar endpoints collapse to one card, never to none.

        A stale edge can link a RAW to its own JPEG. Both are members, so the
        group clears the two-member minimum, but they are one exposure and
        resolve to one card. The group is still returned -- the minimum is a
        property of the *members*, and re-applying it after folding would make
        the group vanish from a list it is legitimately on.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "PAIR_5004.JPG")
            raw = insert_photo(conn, "PAIR_5004.CR3", size=20_000_000)
            rebuild_capture_variants(conn)
            self.link(conn, jpg, raw, raw, "similar")
            conn.commit()

        detail = self.detail()
        self.assertEqual(detail["capture_count"], 2)
        self.assertEqual(detail["count"], 1)
        self.assertEqual([int(item["id"]) for item in detail["members"]], [jpg])
        self.assertEqual(
            detail["members"][0]["similarity_source_id"], detail["recommended_id"]
        )

    def test_exact_groups_keep_every_file(self) -> None:
        """Byte-identical groups are a different question and stay as they are.

        `kind="exact"` answers "are these the same file?", where every copy is
        the point -- a RAW copy of a RAW is a real duplicate to delete. That
        branch never expanded, and it must not start folding.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "DUP_5005.JPG", sha256="same-bytes")
            insert_photo(
                conn, "DUP_5005.CR3", size=20_000_000, sha256="raw-bytes"
            )
            copy = insert_photo(
                conn, "Copies/DUP_5005_COPY.JPG", sha256="same-bytes"
            )
            rebuild_capture_variants(conn)
            self.link(conn, jpg, copy, jpg, "exact")
            conn.commit()

        detail = self.detail()
        self.assertEqual(detail["kind"], "exact")
        self.assertEqual(detail["count"], 2)
        self.assertEqual(
            {int(item["id"]) for item in detail["members"]}, {jpg, copy}
        )

    def test_the_sidebar_covers_carry_no_raw_either(self) -> None:
        """The folder stack is the same picture, one size down.

        The recommendation ranks members, and nothing in that ranking prefers
        a JPEG, so the cover a group leads with can well be a RAW member.
        Leaving the covers on raw member rows would put the file the detail
        view just removed straight back on screen in the list beside it.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            insert_photo(conn, "AAA_5006.JPG")
            raw = insert_photo(conn, "AAA_5006.CR3", size=20_000_000)
            mate = insert_photo(conn, "ZZZ_5006.JPG")
            rebuild_capture_variants(conn)
            # 'aaa_5006.cr3' sorts first, so the RAW member leads the group.
            self.link(conn, raw, mate, raw, "similar")
            conn.commit()

        application = self.application()
        listing = application.photo_queries.similar_groups(
            {"project_id": [self.project.project_id]}
        )
        item = listing["items"][0]
        self.assertEqual(item["recommended_id"], raw)
        self.assertNotIn(
            "raw", {cover["format_category"] for cover in item["covers"]}
        )
        self.assertNotEqual(item["recommended"]["format_category"], "raw")
        # The sidebar thumbnail is the JPEG card itself, not the RAW member it
        # was recommended from: the folder stack then requests a thumbnail the
        # browser can actually paint, and shows the same picture the detail
        # pane will open.
        self.assertEqual(
            item["recommended"]["id"],
            int(
                self.application().photo_queries.similar_group(
                    {
                        "project_id": [self.project.project_id],
                        "group_id": [item["id"]],
                    }
                )["members"][0]["id"]
            ),
        )
        self.assertEqual(item["recommended"]["format_category"], "jpeg")
        # ...and the RAW behind the card is still advertised.
        self.assertIn(
            ["CR3", "JPG"],
            [cover["variant_extensions"] for cover in item["covers"]],
        )

    def test_the_sidebar_never_stacks_one_exposure_twice(self) -> None:
        """Two members of one capture must not both reach the folder stack.

        `cover_ids` is a ranking of *members* truncated to four, and the RAW
        plus the JPEG of a single exposure are two members of the same shot.
        Rank them high enough and both land in the top four, both resolve to
        the same JPEG card, and the folder stacks one picture twice -- which
        reads as "this group holds more photos than it does" and wastes one of
        the four slots.

        Reverse proof: the assertion is on the ids, so a stack that still
        carries both members fails here rather than merely looking plausible.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            first = insert_photo(conn, "AAA_6001.JPG")
            first_raw = insert_photo(
                conn, "AAA_6001.CR3", size=20_000_000
            )
            second = insert_photo(conn, "BBB_6001.JPG")
            third = insert_photo(conn, "CCC_6001.JPG")
            fourth = insert_photo(conn, "DDD_6001.JPG")
            rebuild_capture_variants(conn)
            # A chain so all five members form ONE group: the stale RAW<->JPEG
            # edge is what puts both halves of the capture in the ranking.
            self.link(conn, first, first_raw, first_raw, "similar")
            self.link(conn, first, second, first_raw, "similar")
            self.link(conn, second, third, first_raw, "similar")
            self.link(conn, third, fourth, first_raw, "similar")
            conn.commit()

        application = self.application()
        listing = application.photo_queries.similar_groups(
            {"project_id": [self.project.project_id]}
        )
        self.assertEqual(listing["total"], 1)
        item = listing["items"][0]
        # The precondition: both halves of the capture really are members, and
        # both really are ranked into the four cover slots. Without this the
        # assertions below could pass on a group that never had the problem.
        self.assertEqual(item["capture_count"], 5)
        self.assertEqual(item["count"], 4)
        cover_ids = [int(cover["id"]) for cover in item["covers"]]
        self.assertEqual(len(cover_ids), len(set(cover_ids)))
        # Five members, four of them distinct exposures, so the stack holds
        # three: the pair collapses to one and the fourth-ranked member
        # (DDD) is the one that falls outside the four cover slots.
        self.assertEqual(len(item["covers"]), 3)
        self.assertIn(first, cover_ids)
        self.assertNotIn(first_raw, cover_ids)
        # The three stacked cards are three different pictures.
        self.assertEqual(sorted(cover_ids), sorted([first, second, third]))
        # The stack is a ranking, not the whole group: it holds the top four
        # members, and the pair collapsing to one card is what lets the
        # third-ranked exposure in. Every stacked card is one the detail pane
        # lists -- the stack never shows a photo the detail view would not.
        detail = application.photo_queries.similar_group(
            {
                "project_id": [self.project.project_id],
                "group_id": [item["id"]],
            }
        )
        member_ids = {int(member["id"]) for member in detail["members"]}
        self.assertTrue(set(cover_ids) <= member_ids, member_ids)
        # The detail pane still lists all four exposures; only the stack is
        # capped, so the group does not lose a photo to the dedup.
        self.assertEqual(sorted(member_ids), sorted([first, second, third, fourth]))

    def test_a_group_whose_members_share_one_capture_still_leads_with_a_cover(
        self,
    ) -> None:
        """The degenerate group keeps a cover, so the folder is never blank.

        When every member of a group is another format of the same shot --
        cleared by a minimum of two members, but one exposure -- the stack has
        a single card. That is the floor, not a failure: `similarFolder`
        indexes its stack unconditionally, so an empty `covers` list would
        render a blank folder with no error anywhere.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "ONLY_6002.JPG")
            raw = insert_photo(conn, "ONLY_6002.CR3", size=20_000_000)
            rebuild_capture_variants(conn)
            self.link(conn, jpg, raw, raw, "similar")
            conn.commit()

        item = self.application().photo_queries.similar_groups(
            {"project_id": [self.project.project_id]}
        )["items"][0]
        self.assertEqual(item["count"], 1)
        self.assertEqual(item["capture_count"], 2)
        self.assertEqual([int(cover["id"]) for cover in item["covers"]], [jpg])

    def test_cover_dedup_never_exceeds_the_four_slot_stack(self) -> None:
        """Folding only removes rows, so the four-cover cap still holds.

        `cover_ids` is truncated to four upstream; the dedup runs on top of
        that list rather than re-slicing a longer one, so a group with more
        exposures than slots still stacks at most four.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            names = [f"MANY_600{index}.JPG" for index in range(3, 10)]
            photos = [insert_photo(conn, name) for name in names]
            # Every member its own exposure: no folding is possible at all.
            rebuild_capture_variants(conn)
            for left, right in zip(photos, photos[1:]):
                self.link(conn, left, right, photos[0], "similar")
            conn.commit()

        item = self.application().photo_queries.similar_groups(
            {"project_id": [self.project.project_id]}
        )["items"][0]
        self.assertEqual(item["capture_count"], 7)
        self.assertEqual(item["count"], 7)
        self.assertEqual(len(item["covers"]), 4)
        self.assertEqual(
            sorted(int(cover["id"]) for cover in item["covers"]),
            sorted(photos[:4]),
        )


class SimilarSidebarCaptionTests(unittest.TestCase):
    """The folder caption describes what the group actually holds.

    ``count`` is the number of cards and ``capture_count`` the number of
    files. They part company once a similar group folds a RAW into its JPEG,
    and a group of two files from one exposure then shows a single card --
    where "1 张相似照片" claims a comparison the group does not contain.

    These tests run the real ``web/js/similar.js`` through Node rather than
    matching its text: the wording is the requirement, so asserting that a
    string appears in the source would pass just as happily when the branch
    that uses it is dead.
    """

    SCRIPT = """
    // Load similar.js the way the browser does -- no module system, so every
    // top-level declaration shares one scope -- and export the caption.
    const fs = require("fs");
    const path = process.argv[1];
    const source = fs.readFileSync(path, "utf8");
    global.esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
    global.state = { similar: { selectedId: "" } };
    const src = source + `
;module.exports = { similarFolderCaption, similarFolder };
`;
    const module_ = { exports: {} };
    new Function("module", "exports", "require", src)(
      module_, module_.exports, require,
    );
    global.__similar = module_.exports;
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("需要 node 才能执行 similar.js 的文案逻辑")
        cls.web = Path(__file__).parents[1] / "web" / "js" / "similar.js"
        cls.harness = Path(tempfile.mkdtemp()) / "similar-harness.js"
        cls.harness.write_text(cls.SCRIPT, encoding="utf-8")

    def run_similar(self, body: str) -> dict[str, Any]:
        """Execute ``body`` with ``__similar`` in scope; return its JSON result."""
        script = f"require({str(self.harness)!r});\n{body}\n"
        completed = subprocess.run(
            [self.node, "-e", script, str(self.web)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if completed.returncode != 0:
            self.fail(f"harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    @staticmethod
    def payload(count: int, capture_count: int, kind: str = "similar") -> str:
        """A minimal group shaped like the sidebar's."""
        return json.dumps(
            {
                "id": "sg-1",
                "count": count,
                "capture_count": capture_count,
                "kind": kind,
                "status": "untouched",
                "decided_count": 0,
                "recommended_id": 1,
                "recommended": {"id": 1, "relative_path": "shot/AAA_1.JPG"},
                "covers": [
                    {"id": 1, "thumb_url": "/api/thumb?id=1", "format_category": "jpeg"}
                ],
            },
            ensure_ascii=False,
        )

    def test_a_group_folded_to_one_card_is_not_called_one_similar_photo(
        self,
    ) -> None:
        """count==1 must not produce "N 张相似照片".

        Reverse proof: with the branch removed the caption is the old
        template's output verbatim, so the first assertion fails rather than
        the test merely going quiet.
        """
        result = self.run_similar(
            f"""
            const c = __similar.similarFolderCaption;
            console.log(JSON.stringify({{
              one: c({self.payload(1, 2)}),
              two: c({self.payload(2, 2)}),
              five: c({self.payload(5, 6)}),
            }}));
            """
        )
        self.assertNotIn("相似照片", result["one"])
        self.assertEqual(result["one"], "同一张照片的 2 个文件")
        # The count of files is what makes the line informative.
        self.assertIn("2", result["one"])
        # Multi-card groups keep the wording users already know.
        self.assertEqual(result["two"], "2 张相似照片")
        self.assertEqual(result["five"], "5 张相似照片")

    def test_exact_groups_keep_their_own_wording(self) -> None:
        """Byte-identical copies really are N photos; nothing changes there.

        A one-card exact group is not reachable -- the minimum that creates a
        group is two members and exact groups never fold -- but the branch is
        pinned anyway so a reordered condition cannot quietly re-route it.
        """
        result = self.run_similar(
            f"""
            const c = __similar.similarFolderCaption;
            console.log(JSON.stringify({{
              exactTwo: c({self.payload(2, 2, "exact")}),
              exactOne: c({self.payload(1, 1, "exact")}),
            }}));
            """
        )
        self.assertEqual(result["exactTwo"], "完全重复")
        self.assertEqual(result["exactOne"], "完全重复")

    def test_a_degenerate_payload_falls_back_to_the_plain_wording(self) -> None:
        """capture_count below 2 cannot happen; it must not print anyway.

        A group needs two members to exist, so `count==1` with a single file
        is not a state the server can produce. If it ever did, "同一张照片的
        1 个文件" would be a worse lie than the plain caption.
        """
        result = self.run_similar(
            f"""
            const c = __similar.similarFolderCaption;
            console.log(JSON.stringify({{
              oneFile: c({self.payload(1, 1)}),
              missing: c({self.payload(1, 0)}),
            }}));
            """
        )
        self.assertEqual(result["oneFile"], "1 张相似照片")
        self.assertEqual(result["missing"], "1 张相似照片")

    def test_the_folder_template_actually_renders_the_caption(self) -> None:
        """The function is wired in, not merely defined.

        Reverse proof: a caption that is correct but unused still passes the
        three tests above, so the rendered markup is what proves the change
        reached the screen. The card count beside the stack is deliberately
        left alone -- it counts cards, and one card is the truth.
        """
        result = self.run_similar(
            f"""
            const group = {self.payload(1, 2)};
            const html = __similar.similarFolder(group);
            console.log(JSON.stringify({{
              html,
              hasCaption: html.includes("同一张照片的 2 个文件"),
              hasOldWording: html.includes("1 张相似照片"),
            }}));
            """
        )
        self.assertTrue(result["hasCaption"], result["html"])
        self.assertFalse(result["hasOldWording"], result["html"])
        # The stack chip still reports the card count.
        self.assertIn("<i>1 张</i>", result["html"])


if __name__ == "__main__":
    unittest.main()
