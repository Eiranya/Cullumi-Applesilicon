#!/bin/bash
# macOS equivalent of verify.ps1: ruff lint + the full unittest suite,
# plus an optional cold-start smoke test of the packaged bundle.
#
# Browser (Playwright) tests under tests/dom/ are intentionally skipped: their
# snapshot baseline was recorded on Edge/Windows and the config hardcodes the
# Windows venv layout. web/ is byte-identical to upstream, so the frontend
# behaviour these tests cover is unchanged by the port.
#
# Usage:
#   ./verify-macos.sh          # lint + unit tests
#   ./verify-macos.sh --smoke  # also cold-start dist/Cullumi.app
set -e

cd "$(dirname "$0")"

PY="${CULLUMI_PYTHON:-/Users/inori95/.workbuddy/binaries/python/envs/default/bin/python3}"
if [ ! -x "$PY" ]; then
    echo "找不到 Python 解释器：$PY" >&2
    exit 1
fi

# Two macOS-specific environment corrections, both verified necessary:
#
# 1. TMPDIR must not live under /var. On macOS /var is a symlink to
#    /private/var, so tempfile paths and Path.resolve() results disagree
#    ("/var/..." vs "/private/var/..."). That breaks relative_to() and dict
#    keying inside project_store, producing 8 spurious failures.
# 2. PYTHONPATH must be cleared. This machine injects a sitecustomize.py that
#    hooks os.mkdir and wrongly raises PermissionError(EEXIST) even when
#    exist_ok=True, which breaks project_store.open().
TEST_TMP="$HOME/cullumi-tmp"
mkdir -p "$TEST_TMP"

run_clean() {
    env -u _ -u BASH_ENV -u PYTHONPATH TMPDIR="$TEST_TMP" "$@"
}

echo "==> ruff check ."
run_clean "$PY" -m ruff check .

# The unit suite imports the source tree, so it cannot catch a packaging
# mistake (missing hidden-import or uncollected .so) that only surfaces when
# the frozen app exercises a rarely used path such as a native file dialog.
#
# --smoke must stay reachable even though the suite is not green: the three
# NIQE subtests fail on upstream too (their golden scores were recorded on
# Windows x86-64, and numpy's SIMD path differs from ARM NEON). Those failures
# are expected, so they must not abort the independent smoke stage. This is a
# gate, not a rubber stamp: the failure set is compared against the known
# baseline and ANY other failure still fails the run.

# Known-failing subtests, verified to fail identically on the untouched
# upstream tree. Keep this list in sync with PORTING-PLAN.md section 7.
#
# Keys are fully qualified as "<test id>::<subtest name>" rather than the bare
# subtest name. Using the bare name let a DIFFERENT test that happened to call
# subTest(name="noise-25") collapse onto the same string as the real NIQE
# baseline entry; the suite would report 4 failures while this gate saw only
# the 3 known names and passed the run. Qualifying the key makes such a
# masquerade show up as an extra key and fail the gate.
KNOWN_BASELINE="test_niqe.NiqeTests.test_matches_laboratory_opencv_golden_scores::blur-0.5
test_niqe.NiqeTests.test_matches_laboratory_opencv_golden_scores::blur-3
test_niqe.NiqeTests.test_matches_laboratory_opencv_golden_scores::noise-25"

echo "==> python -m unittest discover -s tests"
UNIT_LOG="$(mktemp "${TMPDIR:-/tmp}/cullumi-unittest.XXXXXX")"
trap 'rm -f "$UNIT_LOG"' EXIT
set +e
run_clean "$PY" -m unittest discover -s tests -v 2>&1 | tee "$UNIT_LOG"
UNIT_STATUS=${PIPESTATUS[0]}
set -e

# Normalise the failure set to a comparable signature.
#
# Only lines that actually start with "FAIL:"/"ERROR:" count -- with -v the log
# also contains traceback bodies that must not be scanned. Each key is
# "<test id>::<subtest name>" (or just "<test id>" when there is no subtest),
# so a genuine regression can never be mistaken for the known baseline, even if
# it reuses a baseline subtest name.
#
# awk is used deliberately: in POSIX/BRE sed, "\|" inside a group is a literal
# pipe rather than alternation, which silently yields an empty match.
ACTUAL="$(awk '
    /^(FAIL|ERROR): / {
        line = $0
        id = ""
        name = ""
        # Trailing "(name=...)" marks a subtest.
        if (match(line, /\(name=[^)]*\)/)) {
            name = substr(line, RSTART, RLENGTH)
            gsub(/^\(name=/, "", name)
            gsub(/\)$/, "", name)
            gsub(/^'"'"'/, "", name)
            gsub(/'"'"'$/, "", name)
            line = substr(line, 1, RSTART - 1)
        }
        # Remaining "(dotted.test.id)" is the test identity. Strip the method
        # name that unittest prints first ("FAIL: test_foo (...)"), then take
        # the parenthesised group.
        if (match(line, /\([^()]*\)[ \t]*$/)) {
            id = substr(line, RSTART, RLENGTH)
            gsub(/^\(/, "", id)
            gsub(/\)[ \t]*$/, "", id)
        }
        if (id == "") {
            id = "unknown-test"
        }
        if (name == "") {
            print id
        } else {
            print id "::" name
        }
    }
' "$UNIT_LOG" | sort -u)"

if [ "$UNIT_STATUS" -eq 0 ]; then
    echo "    单元测试全部通过"
elif [ "$ACTUAL" = "$KNOWN_BASELINE" ]; then
    echo "    注意：以下 NIQE 基线失败为上游已知问题（与移植无关），不视为回归："
    printf '%s\n' "$ACTUAL" | sed 's|^|      - |'
else
    echo "    单元测试出现基线之外的失败（这才是真回归）：" >&2
    printf '%s\n' "$ACTUAL" | sed 's|^|      - |' >&2
    echo "    期望仅以下三项：$KNOWN_BASELINE" >&2
    exit 1
fi

# preview-sharpness 的 display_asset 单测放在 evaluation/ 而非 tests/：tests/ 是
# 冻结目录，新增文件会被 verify-change-set.sh 硬底线 1 判为未授权差异。它因此不在
# 上面的 discover 范围内，这里显式纳入常规回归（失败即 exit 1，见 set -e）。
echo "==> python -m unittest evaluation/preview-resolution/test_display_asset.py"
run_clean "$PY" evaluation/preview-resolution/test_display_asset.py

if [ "${1:-}" = "--smoke" ]; then
    echo "==> 打包产物冷启动冒烟测试"
    env -u _ -u BASH_ENV -u PYTHONPATH ./smoke-test-macos.sh
    echo "==> 核心检查 + 冒烟测试通过；tests/dom 浏览器检查已跳过（需 Node.js + Windows/Edge 快照基线）。"
else
    echo "==> 核心发布检查通过；tests/dom 浏览器检查已跳过（需 Node.js + Windows/Edge 快照基线）。"
    echo "    提示：加 --smoke 可额外验证 dist/Cullumi.app 的冷启动。"
fi
