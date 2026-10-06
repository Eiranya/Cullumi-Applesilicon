from __future__ import annotations

import copy
import csv
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest import mock

from cullumi import http_api, project_store
from cullumi.capture_variants import (
    active_variant_rows,
    format_category_counts,
    rebuild_capture_variants,
    variant_metadata,
    variant_metadata_from_rows,
)
from cullumi.classification import project_photo_counts
from cullumi.config import BUILTIN_PROFILES, ConfigStore
from cullumi.project_store import ProjectManager, connect_db
from cullumi.scanner import Scanner
from cullumi.similarity import SimilarityGroupCache
from cullumi.workflows import (
    apply_quarantine,
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
        self.assertEqual(listing["items"][0]["count"], 4)
        detail = application.photo_queries.similar_group(
            {
                "project_id": [self.project.project_id],
                "group_id": [listing["items"][0]["id"]],
            }
        )
        self.assertEqual(detail["capture_count"], 2)
        self.assertEqual(detail["count"], 4)
        self.assertEqual(
            {int(item["id"]) for item in detail["members"]},
            {first_jpg, first_raw, second_jpg, second_raw},
        )
        sources = {
            int(item["id"]): int(item["similarity_source_id"])
            for item in detail["members"]
        }
        self.assertEqual(sources[first_raw], first_jpg)
        self.assertEqual(sources[second_raw], second_jpg)

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
        self.assertEqual(raw_search["total"], 1)
        self.assertEqual(len(raw_search["items"]), 1)
        self.assertEqual(raw_search["items"][0]["count"], 3)
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
        """Similar groups have their own expand UI; members must survive."""
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
        self.assertEqual(detail["count"], 4)
        self.assertEqual(len({int(item["id"]) for item in detail["members"]}), 4)
        self.assertTrue(
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

    def test_folding_does_not_change_photo_or_format_totals(self) -> None:
        """The filter belongs in the listing, not in the counts.

        `project_photo_counts` and `format_category_counts` describe what is
        on disk. If folding leaked into them, quarantining or exporting would
        silently skip the folded file.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            insert_photo(conn, "IMG_4001.JPG", decision="keep")
            insert_photo(
                conn, "raw/IMG_4001.ARW", decision="remove", size=20_000_000
            )
            insert_photo(conn, "IMG_4002.PNG")
            rebuild_capture_variants(conn)
            conn.commit()
            counts = project_photo_counts(conn)
            formats = {
                entry["id"]: entry["count"]
                for entry in format_category_counts(conn)
            }

        self.assertEqual(counts["total"], 3)
        self.assertEqual(counts["library_counts"]["readable"], 3)
        self.assertEqual(counts["decisions"], {"keep": 1, "remove": 1})
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


class CaptureVariantViewerTests(unittest.TestCase):
    """The viewer's format switch over a folded capture-variant group.

    Folding hides the RAW from the library listing, so the browser holds no id
    and no payload for it. The viewer therefore asks for the whole group
    separately and switches between *complete* payloads -- a switch that only
    swapped `img.src` would show the JPEG's filename and score next to the
    RAW's pixels.
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

    def variants(self, photo_id: int) -> dict[str, Any]:
        """Query the group exactly as the viewer's format switch does."""
        return self.application().photo_queries.capture_variants(
            {
                "project_id": [self.project.project_id],
                "id": [str(photo_id)],
            }
        )

    def pair(self) -> tuple[int, int]:
        """A RAW + JPEG pair of one exposure, rebuilt and committed."""
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(
                conn, "IMG_7001.JPG", taken="2026:10:02 17:59:44", size=2_000_000
            )
            raw = insert_photo(
                conn,
                "raw/IMG_7001.ARW",
                taken="2026:10:02 17:59:44",
                size=20_000_000,
            )
            rebuild_capture_variants(conn)
            conn.commit()
        return jpg, raw

    # ------------------------------------------------------------------
    # A. The data the switch needs
    # ------------------------------------------------------------------

    def test_switching_off_the_card_reaches_the_folded_raw(self) -> None:
        """The whole point: the RAW is unreachable from the listing alone."""
        jpg, raw = self.pair()
        library = self.application().photo_queries.photos(
            {
                "project_id": [self.project.project_id],
                "decisions": ["all"],
                "ai_states": ["all"],
                "formats": ["all"],
            }
        )
        # The listing still folds to one card -- this change adds the viewer,
        # it does not unfold the grid.
        self.assertEqual([int(item["id"]) for item in library["items"]], [jpg])

        items = self.variants(raw)["items"]
        self.assertEqual({int(item["id"]) for item in items}, {jpg, raw})
        by_id = {int(item["id"]): item for item in items}
        # Each format carries its OWN identity, not the representative's.
        self.assertEqual(by_id[raw]["relative_path"], "raw/IMG_7001.ARW")
        self.assertEqual(by_id[jpg]["relative_path"], "IMG_7001.JPG")
        self.assertEqual(by_id[raw]["format_category"], "raw")
        self.assertEqual(by_id[jpg]["format_category"], "jpeg")
        self.assertNotEqual(
            by_id[raw]["photo_url"],
            by_id[jpg]["photo_url"],
            "两个格式必须有不同的 photo_url，否则切换只是换了标签",
        )
        self.assertNotEqual(by_id[raw]["size"], by_id[jpg]["size"])
        # Both sides report the same group, which is what the badge renders.
        for item in items:
            self.assertEqual(item["variant_extensions"], ["ARW", "JPG"])

    def test_group_is_reachable_from_either_member(self) -> None:
        jpg, raw = self.pair()
        from_raw = {int(i["id"]) for i in self.variants(raw)["items"]}
        from_jpg = {int(i["id"]) for i in self.variants(jpg)["items"]}
        self.assertEqual(from_raw, from_jpg)
        self.assertEqual(from_raw, {jpg, raw})

    def test_a_photo_outside_any_group_resolves_to_itself(self) -> None:
        """No group is not an error: the switch simply has nothing to offer."""
        with closing(connect_db(self.project.db_path)) as conn:
            lone = insert_photo(conn, "LONE0002.JPG")
            rebuild_capture_variants(conn)
            conn.commit()

        items = self.variants(lone)["items"]
        self.assertEqual([int(item["id"]) for item in items], [lone])
        self.assertEqual(items[0]["variant_extensions"], [])

    def test_unavailable_photo_is_rejected_rather_than_returned_empty(self) -> None:
        with self.assertRaises(ValueError):
            self.variants(999_999)

    def test_group_of_three_offers_every_format(self) -> None:
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_7002.JPG", taken="2026:10:02 17:59:44")
            raw = insert_photo(
                conn, "raw/IMG_7002.ARW", taken="2026:10:02 17:59:44",
                size=20_000_000,
            )
            nef = insert_photo(
                conn, "raw/IMG_7002.NEF", taken="2026:10:02 17:59:44",
                size=21_000_000,
            )
            rebuild_capture_variants(conn)
            conn.commit()

        items = self.variants(jpg)["items"]
        self.assertEqual({int(i["id"]) for i in items}, {jpg, raw, nef})
        self.assertEqual(items[0]["variant_extensions"], ["ARW", "NEF", "JPG"])

    # ------------------------------------------------------------------
    # B. Degradation when the RAW has no analysis of its own
    # ------------------------------------------------------------------

    def test_unanalysed_raw_degrades_to_absent_fields_not_zeros(self) -> None:
        """A RAW the decoder never measured must not read as "0 分 · 0 B".

        `photo_payload` sets `quality_score` to None when the row carries an
        error, and leaves width/height/size at 0 for a file that was never
        measured. The viewer's meta line drops those segments entirely
        (`viewerMetaText` in web/js/viewer.js); what this test pins is that
        the backend hands it nulls and zeros rather than a fabricated number,
        which is the precondition for that degradation.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_7003.JPG", taken="2026:10:02 17:59:44")
            raw = insert_photo(
                conn,
                "raw/IMG_7003.ARW",
                taken="2026:10:02 17:59:44",
                size=20_000_000,
                error="无法解码 RAW",
                width=0,
                height=0,
            )
            rebuild_capture_variants(conn)
            conn.commit()

        by_id = {int(i["id"]): i for i in self.variants(jpg)["items"]}
        degraded = by_id[raw]
        self.assertIsNone(degraded["quality_score"])
        self.assertEqual(degraded["width"], 0)
        self.assertEqual(degraded["height"], 0)
        self.assertEqual(degraded["reason"], "")
        # The JPEG beside it is fully measured, so the difference is real
        # rather than an artefact of the fixture.
        self.assertIsNotNone(by_id[jpg]["quality_score"])
        self.assertGreater(by_id[jpg]["width"], 0)

    def test_switching_formats_refreshes_the_decision_state_per_format(self) -> None:
        """The keep/remove highlight must follow the format on screen.

        Deciding on one format syncs to the whole group, so the RAW is decided
        too -- but with sync disabled the two diverge, and the viewer has to
        show the selected format's own state rather than the card's.
        """
        with closing(connect_db(self.project.db_path)) as conn:
            jpg = insert_photo(conn, "IMG_7004.JPG", taken="2026:10:02 17:59:44")
            raw = insert_photo(
                conn, "raw/IMG_7004.ARW", taken="2026:10:02 17:59:44",
                size=20_000_000,
            )
            rebuild_capture_variants(conn)
            conn.commit()

        set_photo_decision(self.project, jpg, "keep", False)
        items = {int(i["id"]): i for i in self.variants(jpg)["items"]}
        self.assertEqual(items[jpg]["decision"], "keep")
        # Sync off: the RAW is untouched, which is exactly the divergence the
        # per-format highlight has to reflect.
        self.assertEqual(items[raw]["decision"], "")

    def test_a_decision_on_the_folded_raw_reaches_the_card(self) -> None:
        """Deciding from the RAW side must sync back onto the library card."""
        jpg, raw = self.pair()
        result = set_photo_decision(self.project, raw, "remove", True)
        self.assertEqual({int(row["id"]) for row in result.rows}, {jpg, raw})
        by_id = {int(i["id"]): i for i in self.variants(jpg)["items"]}
        self.assertEqual(by_id[jpg]["decision"], "remove")
        self.assertEqual(by_id[raw]["decision"], "remove")

    # ------------------------------------------------------------------
    # C. The folding contract itself must be untouched
    # ------------------------------------------------------------------

    def test_the_group_endpoint_does_not_unfold_the_library(self) -> None:
        """Fetching the group must not leak extra cards into the grid."""
        jpg, raw = self.pair()
        with closing(connect_db(self.project.db_path)) as conn:
            bystander = insert_photo(conn, "IMG_7005.JPG")
            conn.commit()

        before = self.application().photo_queries.photos(
            {
                "project_id": [self.project.project_id],
                "decisions": ["all"],
                "ai_states": ["all"],
                "formats": ["all"],
            }
        )
        self.variants(jpg)
        after = self.application().photo_queries.photos(
            {
                "project_id": [self.project.project_id],
                "decisions": ["all"],
                "ai_states": ["all"],
                "formats": ["all"],
            }
        )
        self.assertEqual(
            [int(i["id"]) for i in before["items"]],
            [int(i["id"]) for i in after["items"]],
        )
        self.assertEqual(after["total"], before["total"])
        self.assertEqual(
            {int(i["id"]) for i in after["items"]}, {jpg, bystander}
        )
        self.assertNotIn(raw, {int(i["id"]) for i in after["items"]})

    def test_route_is_registered_for_the_viewer(self) -> None:
        """The viewer addresses this by URL; an unregistered route is a 404."""
        self.assertEqual(
            http_api.GET_ROUTES.get("/api/photo/variants"), "api_photo_variants"
        )
        self.assertTrue(
            hasattr(http_api.Handler, "api_photo_variants"),
            "http_api.Handler 缺少 api_photo_variants",
        )

    def test_handler_returns_the_group_it_delegates_to(self) -> None:
        """The HTTP layer must pass the parsed query through untouched."""
        jpg, raw = self.pair()
        handler = object.__new__(http_api.Handler)
        handler._send_json = mock.Mock()
        handler._query = mock.Mock(
            return_value={
                "project_id": [self.project.project_id],
                "id": [str(raw)],
            }
        )
        with mock.patch.object(http_api, "APPLICATION", self.application()):
            handler.api_photo_variants()
        payload = handler._send_json.call_args.args[0]
        self.assertEqual({int(i["id"]) for i in payload["items"]}, {jpg, raw})

    # ------------------------------------------------------------------
    # Reverse proof: the switch must be capable of actually switching
    # ------------------------------------------------------------------

    def test_the_two_formats_really_do_differ_on_screen(self) -> None:
        """Guards against a constant.

        If the payloads were interchangeable the tests above would still pass
        while the feature did nothing. Here the RAW's URL, name and size are
        read back through the very call the viewer makes, and each must differ
        from the JPEG's.
        """
        jpg, raw = self.pair()
        items = self.variants(jpg)["items"]
        jpeg_payload = next(i for i in items if int(i["id"]) == jpg)
        raw_payload = next(i for i in items if int(i["id"]) == raw)

        self.assertEqual(
            [i["photo_url"] for i in items],
            list(dict.fromkeys(i["photo_url"] for i in items)),
            "同一组内 photo_url 必须互不相同",
        )
        self.assertRegex(jpeg_payload["photo_url"], rf"[?&]id={jpg}(&|$)")
        self.assertRegex(raw_payload["photo_url"], rf"[?&]id={raw}(&|$)")
        self.assertNotEqual(
            jpeg_payload["relative_path"].rsplit("/", 1)[-1],
            raw_payload["relative_path"].rsplit("/", 1)[-1],
        )
        self.assertNotEqual(jpeg_payload["size"], raw_payload["size"])
        # And the raw is displayable: /api/photo serves a JPEG rendition of it.
        self.assertTrue(raw_payload["photo_url"].startswith("/api/photo?"))


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


if __name__ == "__main__":
    unittest.main()
