#!/bin/bash
# Print the change set against the read-only upstream tree, and FAIL if any
# hard invariant is broken.
#
# Usage: ./verify-change-set.sh [path-to-upstream]
#
# ---------------------------------------------------------------------------
# 硬底线（任何情况下不得放宽）：
#   1. tests/ 与 models/ 除 AUTHORIZED_FROZEN 白名单外必须与上游 IDENTICAL
#      —— 它们是判定零回归的依据本身；白名单每项都必须写明理由
#   2. differ 集合必须精确等于 EXPECTED_DIFFER（见下方清单，每项附授权理由）
#   3. added  集合必须精确等于 EXPECTED_ADDED —— 白名单之外的新文件一律拒绝
#   4. README.md 与上游逐字节一致
# 注意：本脚本自己也算在 added 里，这是预期的。
#
# 为什么 differ 清单要写死在脚本里而不是数个数：清单本身是白名单，
# 每加一项都必须有人说明理由；数值计数则会随队友增删文件而漂移（本项目
# 已漂过 8 → 10 → 12 → 13），文档成了新的不一致来源。
#
# 为什么 added 也要白名单：此前只 "打印" 新增清单而不校验，任何临时脚本或
# 调试模块都能躺在树里通过（QA 用新增一个 .py 实证过 EXIT=0）。
# ---------------------------------------------------------------------------
#
# 为什么第 1 条必须是 exit 1 而不是提示：
# QA 的一次 canary 注入曾留在 tests/test_fs_utils.py 未被清理，而本脚本当时
# 只把 tests/ 标为 "differs (confirm it is on the unfrozen list)" 并照常报
# differ=6 —— 看起来一切正常，实际基线已被污染。"冻结目录干净"不能只存在
# 于每个人的记性里，必须被脚本强制保证。
#
# 为什么计数不手写：手工维护的总数会随队友新增文件而漂移（本项目已漂过
# 8 → 10 → 12 → 13），文档本身就成了新的不一致来源。

set -e

cd "$(dirname "$0")"

UPSTREAM="${1:-../cullumi-src}"
if [ ! -d "$UPSTREAM" ]; then
    echo "找不到上游目录：$UPSTREAM" >&2
    exit 1
fi

# The exact set of files allowed to differ from upstream. Anything else that
# differs means the port touched something it must not have.
#
# app.py                          WKWebView bootstrap; folder drag-and-drop wiring
# cullumi/analysis_worker.py      worker restart cap raised (measured 13% gain)
# cullumi/capture_variants.py     capture-variant grouping (this port's feature)
# cullumi/classification.py       quality reasons in plain words, not metric names;
#                                 library counts folded to one per capture group
#                                 so the sidebar matches the cards on screen
# cullumi/config.py               macOS app-data dir; blink_gpu_enabled default;
#                                 viewer wheel-sensitivity defaults + validation
#                                 (trackpad / mouse / input-device)
# cullumi/decision_service.py     report which photos a synced decision folded
#                                 away, so the library can show one card per
#                                 capture group instead of one per format
# cullumi/face_analysis.py        CoreML execution provider + CPU fallback
# cullumi/http_api.py             platform-aware update message; GPU setting;
#                                 optional &w= supply width on /api/photo so the
#                                 viewer loads a preview and fetches the
#                                 original only on request; expose the viewer
#                                 wheel-sensitivity defaults on /api/bootstrap
# cullumi/media.py                RAW EXIF from the TIFF IFD chain; display
#                                 preview encoded 4:4:4 (Pillow defaults to
#                                 4:2:0 for large images, halving chroma), with
#                                 the encoder settings folded into the cache
#                                 fingerprint and the stale-file prune scoped to
#                                 one width so the viewer's 2048/4096/original
#                                 tiers can coexist
# cullumi/native_dialogs.py       macOS file dialogs via pywebview
# cullumi/photo_query_service.py  expose similarity-group processing status;
#                                 per-photo preview_url alongside photo_url
# cullumi/settings_service.py     accept blink_gpu_enabled in the settings route;
#                                 validate + persist the viewer wheel sensitivities
#                                 and input-device choice
# cullumi/similarity.py           derive the group processing status
# cullumi/updates.py              .dmg whitelist; winreg guarded
# tests/test_analysis_worker.py   worker-pool test updated for the new sizing
#                                 (also listed in AUTHORIZED_FROZEN -- a change
#                                 to a frozen dir needs both entries on purpose)
# tests/test_settings_service.py  default-value assertion follows the new
#                                 fast_analysis default (also in AUTHORIZED_FROZEN)
# web/css/base.css                photo-library grid switched to a deterministic
#                                 fixed column width so the card image box is
#                                 predictable (preview-sharpness / PRD P0-1)
# web/css/home.css                drag-and-drop highlight for the empty state
# web/css/workspace.css           group processing-status badge
# web/css/viewer.css              viewer: loading indicator for the on-demand
#                                 original, the fit / view-original buttons, and
#                                 the scale-state hint line. Now also groups those
#                                 two buttons into a right-aligned segmented control
#                                 (fit active-state highlight) and styles the
#                                 wheel-sensitivity layout.
# web/css/settings.css            owner of the settings-row slider styling for the
#                                 viewer wheel sensitivities (trackpad / mouse)
#                                 and their live value readout
# web/index.html                  hardware-acceleration switch; plain-word labels;
#                                 viewer zoom controls grouped into a right-aligned
#                                 segmented control; wheel-sensitivity sliders and
#                                 input-device picker; loading indicator
# web/js/app.js                   bind the drag-and-drop affordance; F loads the
#                                 original, 0 fits the window. The `1` key that
#                                 used to toggle 1:1 was withdrawn (owner ruling);
#                                 1:1 now arrives only via 查看原图's auto-land
# web/js/gallery.js               refresh the group badge after a decision
# web/js/runtime.js               statusFilter in the shared view state; the
#                                 viewer's display-tier bookkeeping (mounted
#                                 tier, in-flight tier, debounce handle)
# web/js/session.js               GPU status on boot; drop accept/reject entry
#                                 points; restore the wheel-sensitivity sliders
#                                 and input-device picker on boot
# web/js/settings.js              platform-aware update text; GPU switch handler;
#                                 wheel-sensitivity slider + input-device handlers
# web/js/similar.js               render + live-update the group status badge
# web/js/viewer.js                viewer: true 1:1 viewing plus source swapping
#                                 that matches the pixels to the display size
#                                 (2048 / 4096 / original, hysteretic and
#                                 throttled) so magnification downsamples
#                                 instead of interpolating, with an honest
#                                 scale hint. The RAW/JPEG format switch that
#                                 used to live here was withdrawn after user
#                                 trials; the capture-variant badge is now
#                                 plain text. Wheel zoom is now proportional to
#                                 the scroll amount and the (trackpad vs. mouse)
#                                 device, with the two sensitivities persisted in
#                                 settings and the view buttons' active state
#                                 synced.
#                                 .gitignore: the preview-resolution investigation's
#                                 raw measurements (11 JSON files) are kept, the
#                                 per-item PNG renders are not -- they are
#                                 regenerable output. Narrowing that path to JSON
#                                 is why this file itself changed.
EXPECTED_DIFFER=".gitignore
app.py
cullumi/__init__.py
cullumi/analysis_worker.py
cullumi/capture_variants.py
cullumi/classification.py
cullumi/config.py
cullumi/decision_service.py
cullumi/face_analysis.py
cullumi/http_api.py
cullumi/media.py
cullumi/native_dialogs.py
cullumi/photo_query_service.py
cullumi/settings_service.py
cullumi/similarity.py
cullumi/updates.py
tests/test_analysis_worker.py
tests/test_app.py
tests/test_capture_variants.py
tests/test_core.py
tests/test_database.py
tests/test_media.py
tests/test_scanner.py
tests/test_settings_service.py
web/css/base.css
web/css/home.css
web/css/settings.css
web/css/viewer.css
web/css/workspace.css
web/index.html
web/js/app.js
web/js/gallery.js
web/js/runtime.js
web/js/session.js
web/js/settings.js
web/js/similar.js
web/js/viewer.js"

# Files under web/ deliberately unfrozen for this port. web/ as a whole is NOT
# frozen -- these were opened up one by one, and each difference is reviewed by
# hand below. Anything else differing under web/ is still a violation.
AUTHORIZED_WEB="web/css/base.css
web/css/home.css
web/css/settings.css
web/css/viewer.css
web/css/workspace.css
web/index.html
web/js/app.js
web/js/gallery.js
web/js/runtime.js
web/js/session.js
web/js/settings.js
web/js/similar.js
web/js/viewer.js"

# Directories that must stay byte-identical, except for the files listed below.
# These are the basis for the zero-regression claim, so loosening them requires
# an explicit, documented decision rather than convenience.
FROZEN_DIRS="tests
models"

# The only files under FROZEN_DIRS allowed to differ, with the reason.
#
# tests/test_analysis_worker.py
#   `test_parallel_capacity_obeys_cpu_and_total_memory_budget` pinned the old
#   worker-pool formula. That formula divided the memory budget by the 1.5GB
#   per-worker *ceiling*, so it returned 2 on every machine and the CPU term
#   never mattered. Re-sizing the pool is a deliberate behaviour change, taken
#   with the product owner, so this one characterisation test is unfrozen. The
#   replacement asserts the same property plus a stronger one: the pool must
#   still fit the budget when every worker holds its worst-case image.
#
# tests/test_settings_service.py
#   `test_fast_analysis_validation_and_restart_persistence` asserted the default
#   of `fast_analysis` was False. Parallel analysis is now on by default (it is
#   the bulk of a scan and the only stage that parallelises well), again a
#   deliberate product decision. The test still covers validation and restart
#   persistence; its round trip now runs in the OFF direction, which is the
#   transition the new default no longer exercises.
#
# tests/test_capture_variants.py
#   Two entries, both deliberate.
#
#   `test_strict_grouping_and_representative_selection` asserted that files with
#   the same stem in different directories never pair. That encoded a limitation
#   the owner asked to lift: RAW and JPEG of one exposure are routinely filed
#   apart, a `raw/` subdirectory next to the JPEG folder being a common layout.
#   Pairing is now keyed on the stem alone and confirmed by capture time, so the
#   suite covers both the wider behaviour and its new failure mode -- the same
#   stem across two unrelated events must NOT pair. The existing same-directory
#   assertions are unchanged.
#
#   Second: the RAW/JPEG preview format switch shipped in 1.1.0 and was
#   withdrawn after user trials (a browser cannot decode RAW, so switching only
#   ever produced a worse or blank frame). Its twelve tests went with it, and
#   `test_folding_does_not_change_photo_or_format_totals` was rewritten as
#   `test_library_counts_fold_but_format_counts_stay_per_file`: the product
#   owner reversed the earlier ruling that the counts describe the disk. The
#   sidebar now counts cards (one per capture group) while the format tallies
#   still count files, and the new tests pin both bases plus the sum identities
#   the sidebar's arithmetic depends on. New tests for the withdrawal, the
#   on-demand preview/1:1 work, and the counting basis live in the same file
#   rather than a new one.
#
#   Third: the viewer's wheel zoom is now proportional to the scroll amount and
#   to the input device (trackpad vs. mouse), so `ViewerWheelZoomTests` executes
#   the real viewer.js through the existing Node harness to pin the calibration,
#   the per-device sensitivities, the device heuristic, the manual override and
#   the fit highlight. Several assertions are reverse proofs -- the harness
#   export list had to grow to reach them.
#
# tests/test_media.py
#   `open_image` returned a hardcoded "" for RAW, so `taken` was empty for every
#   RAW file and everything time-dependent (burst grouping, sorting) silently
#   degraded to filename-sequence heuristics on RAW. Reading the TIFF IFD chain
#   is a deliberate fix; the new tests pin the parse together with its failure
#   modes (non-TIFF, truncated, out-of-range offset, tag preference).
#
# tests/test_core.py
#   Two entries. `test_profile_validation` asserted `__version__ == "1.0.5"`,
#   pinning the port to a literal upstream changes on every release; it now
#   checks the semver shape instead, which is what the original reached for and
#   still fails on a malformed value. `setUp` also resolves the temp directory
#   now. Without that, Path.resolve() turned "/tmp/..." into
#   "/private/tmp/..." inside the scanner while the project root stayed
#   unresolved, so relative_to() raised "not in the subpath" and two discovery
#   tests failed. macOS only; upstream runs on Windows where /tmp is not a
#   symlink. The rest of the file is untouched.
#
# tests/test_database.py
#   The WAL tests cleared and read _WAL_CONFIGURED_DATABASES with
#   path.resolve(), but connect_db keys that dict by the path it was handed.
#   Identical on Windows; on macOS the resolved form ("/private/tmp/...") never
#   matches the stored one ("/tmp/...") and both tests raised KeyError. The lookups
#   now use the same spelling production uses. No assertion was weakened.
#
# tests/test_scanner.py
#   setUp resolved the temp dir for the same reason as test_core: the bounded
#   dispatch test walks real paths through relative_to().
#
# tests/test_app.py
#   Two entries.
#   test_api_photo_builds_a_high_resolution_tiff_preview compared mock call
#   arguments against unresolved paths while api_photo resolves internally, so
#   the mock never matched. The expected paths are now built from a resolved
#   root; the assertion itself is unchanged.
#   The two api_photo tests now also stub _query: the handler reads the optional
#   `w` supply width off the query, and both tests pin the no-width default.
#   Only the stub was added; no assertion was weakened.
#
# models/ has no entries and is expected to stay fully identical.
AUTHORIZED_FROZEN="tests/test_analysis_worker.py
tests/test_app.py
tests/test_capture_variants.py
tests/test_core.py
tests/test_database.py
tests/test_media.py
tests/test_scanner.py
tests/test_settings_service.py"

# Files this port adds on top of upstream. Whitelisting them closes a real hole:
# without it the gate only *printed* the added list, so any stray file (a
# scratch script, a debug module) could sit in the tree and still pass. Sorted
# under LC_ALL=C.
#
# evaluation/preview-resolution
#   Read-only instrument for a support question -- "why does the photo-library
#   card preview look soft?". Measures the image pipeline stage by stage and
#   settles it with numbers instead of guesses. Ships only its own scripts, its
#   write-up and three figures; it reads photos and the project DB but writes
#   neither. Added because there was no way to answer the question from the
#   existing benchmarks, and the answer turned out to contradict the obvious
#   hypothesis (the 512px cap is NOT the cause).
#
# evaluation/performance-results
#   Raw output of the above (JSON per stage, plus every rendered variant). Not a
#   deliverable: it is listed in .gitignore for the same reason upstream ignores
#   evaluation/performance-results -- regenerable scratch. It still needs an
#   entry here because this gate diffs the working tree, and diff(1) does not
#   read .gitignore.
#
# cullumi/display_asset.py
#   Derives a card-sized display asset from the existing 512px analysis
#   thumbnail (preview-sharpness). Kept separate from the 512 thumbnail on
#   purpose: that file is the NIQE / sharpness input baked into analysis_version()
#   and the NIQE golden baseline, so it must never be rewritten.
#
# deliverables
#   The preview-sharpness PRD and architecture design land here (Q1 ruling:
#   register the directory rather than move the docs elsewhere).
#
# docs/arch
#   Process notes kept on purpose. The six reports filed there (port plan, QA
#   rounds, performance profiling, two feasibility probes) are historical
#   records, not current documentation -- their headline conclusions have been
#   superseded by code that has since landed. They are kept because two of them
#   record failed attempts and overturned assumptions that would otherwise have
#   to be rediscovered: the assumptions that proxy_tools/bottle could be
#   dropped, that a CoreML execution provider existed, and that synthetic drawn
#   faces were usable for threshold work. Re-deriving those costs hours. Each
#   file's specific stale claims are listed in docs/arch/README.md.
EXPECTED_ADDED="Cullumi-macos.spec
MACOS-使用说明.md
build-app-macos.sh
build-macos.sh
cullumi/display_asset.py
deliverables
docs
evaluation/performance-results
evaluation/preview-resolution
evaluation/probe_native_dialogs.py
requirements-macos.txt
setup-macos.sh
smoke-test-macos.sh
verify-change-set.sh
verify-macos.sh"

# Excludes: .git is VCS metadata; dist/build are outputs; __pycache__,
# .ruff_cache and .DS_Store are artefacts of the tools we run rather than
# deliverables. .ruff_cache appears as soon as the linter runs, and .DS_Store
# is written by Finder the moment anyone browses this folder -- on a
# macOS-native project that is normal use, and letting it fail the gate would
# train the reader to ignore the gate. Excluding them keeps the result stable
# across runs without weakening what the gate is actually asserting.
#
# The bytecode pattern is matched as '*.pyc' rather than by directory name.
# diff(1) matches --exclude against the basename only, so a bare
# --exclude=__pycache__ covers ./__pycache__ but not tests/__pycache__ -- and
# running the test suite creates exactly that. The gate would then fail on a
# clean tree purely because someone had run the tests, which is worse than
# useless: it teaches the reader to ignore it. .build is excluded for the same
# reason: it is scratch space for a relocated PyInstaller cache, not a
# deliverable, and .gitignore does not list it either.
OUT="$(mktemp "${TMPDIR:-/tmp}/cullumi-dq.XXXXXX")"
VIOLATIONS="$(mktemp "${TMPDIR:-/tmp}/cullumi-violations.XXXXXX")"
trap 'rm -f "$OUT" "$VIOLATIONS"' EXIT

diff -rq "$UPSTREAM" . \
    --exclude=.git --exclude=dist --exclude=build \
    --exclude='*.pyc' --exclude=.ruff_cache --exclude=.DS_Store \
    --exclude=.build --exclude=__pycache__ \
    > "$OUT" 2>/dev/null || true

DIFFER=$(grep -c 'differ$' "$OUT" || true)

# `diff -rq` reports extra files as "Only in <dir>: <name>", so the path has to
# be rebuilt from both halves. Keeping the directory matters: collapsing to the
# bare file name would merge same-named files in different directories, so the
# added/removed lists could not be compared against a whitelist reliably.
#
# LC_ALL=C on every sort below: the default collation may ignore '.' and '/',
# which would make the expected-set comparison depend on the machine's locale.
relpaths() {
    # $1 = directory prefix to strip ("" for our side, the upstream root for theirs)
    LC_ALL=C awk -v root="$1" '
        /^Only in / {
            line = $0
            sub(/^Only in /, "", line)
            idx = index(line, ": ")
            if (idx == 0) next
            dir = substr(line, 1, idx - 1)
            name = substr(line, idx + 2)
            if (root != "") {
                if (dir == root) dir = ""
                else if (index(dir, root "/") == 1) dir = substr(dir, length(root) + 2)
            }
            sub(/^\.\//, "", dir)
            if (dir == "." || dir == "") print name
            else print dir "/" name
        }
    '
}

# Match the two sides by their prefix rather than by a bare dot: `Only in .`
# as a fixed string also matches "Only in ../cullumi-src", which is how a
# removed file used to end up classified as added.
REMOVED_LIST="$(grep -E "^Only in $(printf '%s' "$UPSTREAM" | sed 's/[.[\*^$]/\\&/g')(:|/)" "$OUT" \
    | relpaths "$UPSTREAM" | LC_ALL=C sort || true)"
REMOVED=$(printf '%s\n' "$REMOVED_LIST" | grep -c . || true)
ADDED_LIST="$(grep -E '^Only in \.(/|:)' "$OUT" | relpaths "" | LC_ALL=C sort || true)"
# A move/rename shows up on both sides; drop any added path that was removed.
ADDED_LIST="$(printf '%s\n' "$ADDED_LIST" \
    | grep . \
    | grep -vxF "$REMOVED_LIST" 2>/dev/null || true)"
ADDED=$(printf '%s\n' "$ADDED_LIST" | grep -c . || true)

ACTUAL_DIFFER="$(grep 'differ$' "$OUT" | sed 's/^Files //; s| and .*||' \
    | sed "s|^$UPSTREAM/||" | LC_ALL=C sort || true)"

echo "=== change set (vs $UPSTREAM) ==="
echo "  differ  = $DIFFER"
echo "  removed = $REMOVED"
echo "  added   = $ADDED"
echo
echo "--- modified ($DIFFER) ---"
printf '%s\n' "$ACTUAL_DIFFER" | grep . | sed 's|^|  |'
echo "--- removed ($REMOVED) ---"
printf '%s\n' "$REMOVED_LIST" | grep . | sort | sed 's|^|  |'
echo "--- added ($ADDED) ---"
printf '%s\n' "$ADDED_LIST" | grep . | sort | sed 's|^|  |'

# ---------------------------------------------------------------------------
# 硬底线 1：tests/ 与 models/ 除 AUTHORIZED_FROZEN 外必须与上游逐字节一致
# ---------------------------------------------------------------------------
# 该目录此前要求整体逐字节一致。放宽为「白名单式」而不是取消：只有清单里
# 列出的文件允许有差异，其余任何差异仍然 exit 1，且清单里的文件若实际没有
# 差异也会报错（防止清单腐化成空头授权）。强度体现在最后一条断言：未授权
# 的差异一个都不放过。
echo
echo "--- 硬底线 1：冻结目录只允许白名单内文件有差异 ---"
FROZEN_PATHS=""
for d in $FROZEN_DIRS; do
    DIR_DIFF="$(diff -rq "$UPSTREAM/$d" "$d" 2>/dev/null || true)"
    if [ -z "$DIR_DIFF" ]; then
        echo "  $d/ IDENTICAL"
        continue
    fi
    echo "  $d/ 存在差异："
    printf '%s\n' "$DIR_DIFF" | sed "s|^$UPSTREAM/||" | sed 's|^|      |'
    PATHS="$(printf '%s\n' "$DIR_DIFF" | grep . \
        | sed 's/.* and \(.*\) differ$/\1/' | sed 's/[[:space:]]*$//')"
    FROZEN_PATHS="$(printf '%s\n%s\n' "$FROZEN_PATHS" "$PATHS" | grep . || true)"
done

# Materialise the actual set so the staleness check can use it as a pattern
# file. Verified behaviour: with an EMPTY pattern file, `grep -vxF -f` prints
# every input line, which is exactly what the staleness check needs when nothing
# differs (all authorised entries are then correctly reported as stale).
FROZEN_ACTUAL="$(mktemp "${TMPDIR:-/tmp}/cullumi-frozen.XXXXXX")"
AUTHORIZED_FILE="$(mktemp "${TMPDIR:-/tmp}/cullumi-auth.XXXXXX")"
trap 'rm -f "$OUT" "$VIOLATIONS" "$FROZEN_ACTUAL" "$AUTHORIZED_FILE"' EXIT
printf '%s\n' "$FROZEN_PATHS" | grep . | LC_ALL=C sort -u > "$FROZEN_ACTUAL" || true
printf '%s\n' "$AUTHORIZED_FROZEN" | grep . > "$AUTHORIZED_FILE" || true

UNEXPECTED_FROZEN="$(printf '%s\n' "$FROZEN_PATHS" | grep . | LC_ALL=C sort -u \
    | grep -vxF -f "$AUTHORIZED_FILE" || true)"
if [ -n "$UNEXPECTED_FROZEN" ]; then
    echo "  冻结目录含未授权差异  ← 违反硬底线 1" | tee -a "$VIOLATIONS"
    printf '%s\n' "$UNEXPECTED_FROZEN" | sed 's|^|      |' | tee -a "$VIOLATIONS"
elif [ -n "$FROZEN_PATHS" ]; then
    echo "  冻结目录的差异均在 AUTHORIZED_FROZEN 内"
fi

# A stale entry would silently pre-authorise a later change to that file, so an
# authorised path that no longer differs is itself a violation. This runs even
# when nothing under the frozen dirs differs -- guarding it behind the block
# above once left the check dead in precisely that case.
MISSING_FROZEN="$(grep -vxF -f "$FROZEN_ACTUAL" "$AUTHORIZED_FILE" || true)"
if [ -n "$MISSING_FROZEN" ]; then
    echo "  AUTHORIZED_FROZEN 含失效条目（该文件已与上游一致，应移出清单）—— 违反硬底线 1" \
        | tee -a "$VIOLATIONS"
    printf '%s\n' "$MISSING_FROZEN" | sed 's|^|      |' | tee -a "$VIOLATIONS"
fi

# web/ 是软提示：仅 AUTHORIZED_WEB 中列出的文件被授权解冻，其余差异仍算违规。
echo "--- web/ 软校验（仅授权清单内的文件可解冻）---"
if diff -r "$UPSTREAM/web" web >/dev/null 2>&1; then
    echo "  web/ IDENTICAL"
else
    WEB_DIFF="$(diff -rq "$UPSTREAM/web" web 2>/dev/null \
        | sed "s|^$UPSTREAM/||" || true)"
    echo "  web/ 存在差异："
    printf '%s\n' "$WEB_DIFF" | grep . | sed 's|^|      |'
    # Compare the *path* exactly rather than grepping for a substring: a
    # substring match would let "web/js/settings.js.bak" ride along on the
    # authorization for "web/js/settings.js".
    WEB_PATHS="$(printf '%s\n' "$WEB_DIFF" | grep . \
        | sed 's/.* and \(.*\) differ$/\1/' | sed 's/[[:space:]]*$//')"
    UNEXPECTED_WEB="$(printf '%s\n' "$WEB_PATHS" \
        | grep -vxF "$AUTHORIZED_WEB" || true)"
    if [ -n "$UNEXPECTED_WEB" ]; then
        echo "  web/ 含预期外差异（不在 AUTHORIZED_WEB 授权清单内）：" | tee -a "$VIOLATIONS"
        printf '%s\n' "$UNEXPECTED_WEB" | sed 's|^|      |' | tee -a "$VIOLATIONS"
    fi
fi

# ---------------------------------------------------------------------------
# 硬底线 2：differ 集合必须精确等于 EXPECTED_DIFFER
# ---------------------------------------------------------------------------
echo
echo "--- 硬底线 2：differ 集合必须精确匹配 ---"
if [ "$ACTUAL_DIFFER" = "$EXPECTED_DIFFER" ]; then
    echo "  differ 集合与预期一致"
else
    echo "  differ 集合与预期不符  ← 违反硬底线 2" | tee -a "$VIOLATIONS"
    EXTRA="$(printf '%s\n' "$ACTUAL_DIFFER" | grep -vxF "$EXPECTED_DIFFER" || true)"
    MISSING="$(printf '%s\n' "$EXPECTED_DIFFER" | grep -vxF "$ACTUAL_DIFFER" || true)"
    if [ -n "$EXTRA" ]; then
        echo "      多出（不应被改动）：" | tee -a "$VIOLATIONS"
        printf '%s\n' "$EXTRA" | sed 's|^|        |' | tee -a "$VIOLATIONS"
    fi
    if [ -n "$MISSING" ]; then
        echo "      缺少（应被改动却没改）：" | tee -a "$VIOLATIONS"
        printf '%s\n' "$MISSING" | sed 's|^|        |' | tee -a "$VIOLATIONS"
    fi
fi

# ---------------------------------------------------------------------------
# 硬底线 3：added 集合必须精确等于 EXPECTED_ADDED
# ---------------------------------------------------------------------------
# 没有这一条时，门禁只把 added 打印出来而不校验，任何多余文件（临时脚本、
# 调试模块）都能躺在树里通过。QA 用 `printf '#x' >> cullumi/theme.py` 实证过
# 这个缺口：新文件出现且 EXIT=0。
echo
echo "--- 硬底线 3：added 集合必须精确匹配 ---"
if [ "$ADDED_LIST" = "$EXPECTED_ADDED" ]; then
    echo "  added 集合与预期一致"
else
    echo "  added 集合与预期不符  ← 违反硬底线 3" | tee -a "$VIOLATIONS"
    EXTRA_ADDED="$(printf '%s\n' "$ADDED_LIST" | grep -vxF "$EXPECTED_ADDED" || true)"
    MISSING_ADDED="$(printf '%s\n' "$EXPECTED_ADDED" | grep -vxF "$ADDED_LIST" || true)"
    if [ -n "$EXTRA_ADDED" ]; then
        echo "      多出（未经授权的新增文件）：" | tee -a "$VIOLATIONS"
        printf '%s\n' "$EXTRA_ADDED" | sed 's|^|        |' | tee -a "$VIOLATIONS"
    fi
    if [ -n "$MISSING_ADDED" ]; then
        echo "      缺少（清单里有但树里没有）：" | tee -a "$VIOLATIONS"
        printf '%s\n' "$MISSING_ADDED" | sed 's|^|        |' | tee -a "$VIOLATIONS"
    fi
fi

# ---------------------------------------------------------------------------
# 硬底线 4：README.md 与上游逐字节一致
# ---------------------------------------------------------------------------
echo
echo "--- 硬底线 4：README.md 逐字节一致 ---"
if diff -q "$UPSTREAM/README.md" README.md >/dev/null 2>&1; then
    echo "  README.md IDENTICAL"
else
    echo "  README.md DIFFERS  ← 违反硬底线 4" | tee -a "$VIOLATIONS"
fi

echo
if [ -s "$VIOLATIONS" ]; then
    echo "❌ 硬底线校验失败。冻结目录 / differ 集合 / added 集合 / README 之一被破坏。" >&2
    echo "   这些是判定「零回归」的依据本身，不可带着继续交付。" >&2
    echo "   冻结目录的改动必须同时登记进 AUTHORIZED_FROZEN 与 EXPECTED_DIFFER，" >&2
    echo "   并在注释里写明理由；其余常量同理。" >&2
    exit 1
fi

echo "✅ 硬底线校验通过：冻结目录除 AUTHORIZED_FROZEN 外逐字节一致、differ 与 added 集合精确匹配、README.md 一致。"
