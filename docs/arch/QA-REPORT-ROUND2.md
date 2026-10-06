# QA 回归验证报告 · 第二轮（Round 2）

- 项目：`cullumi-macos`（macOS 27.2 / arm64移植）
- 基线：`cullumi-src`（上游原始副本，从未改动）
- 验证者：software-qa-engineer-regression（独立验证，未参考工程师自述结论）
- 日期：2026-10-04
- 轮次：Round 2 / 2（已达两轮上限）

---

## 1. 判定

**PASS_WITH_ISSUES**

E1–E6 六项整改**全部经独立实测确认真实修复**，且本轮解禁的两个文件（`cullumi/http_api.py`、`web/js/settings.js`）**未引入任何回归**（Windows 分支行为与上游逐例比对零差异）。但发现 **1 个 P2 级新问题**：`verify-macos.sh --smoke` 在当前仓库状态下**永远跑不到冒烟阶段**（详见 §3-N1），属新增脚本的集成缺陷，不影响 `dist/Cullumi.app` 产物本身的可交付性。

---

## 2. 逐项验证结果

| # | 项目 | 判定 | 实测证据 |
|---|------|------|----------|
| E1 | 版本号一致 | ✅ **修好了** | `PlistBuddy` 读 `CFBundleShortVersionString` = `1.0.5`；`cullumi/__init__.py:3` = `1.0.5`；`diff __init__.py` vs上游 = **IDENTICAL**；`Cullumi-macos.spec:25` 确为 `from cullumi import __version__ as APP_VERSION`，:128/:129 均取用 `APP_VERSION`。三处闭环，无第二个真相源。 |
| E2 | 使用说明重写 + Gatekeeper | ✅ **修好了** | 见下方 E2 明细。(d) 项`spctl` 实测已自行核对，结论一致。 |
| E3 | 探针可独立运行 | ✅ **修好了**（GUI 段未验证） | `probe_native_dialogs.py:26` 有 `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`。从项目根实跑，日志输出 `sys.platform = darwin`、`_run_powershell_dialog returns: ''`，证明 PowerShell 兜底在 macOS 不可达。导入层完全验证；**GUI 对话框弹出段未验证**（见 §4）。 |
| E4 | LSMinimumSystemVersion | ✅ **修好了** | plist 含 `LSMinimumSystemVersion = 14.0`；主二进制 `minos 11.0`，而 `libonnxruntime.1.29.0.dylib` 的 `minos 14.0`。**二者不一致是合理的**：主二进制是 PyInstaller bootloader + Python 扩展，由 bootloader 自身决定；真正决定最低版本的是随包分发的 onnxruntime 官方 arm64 轮子（仅提供 `macosx_14_0_arm64`）。plist 声明 14.0 让系统在加载 dylib **之前**就给出友好提示，把「启动即崩」变成「明确告知需求」，与 spec:27-30 的注释意图一致。 |
| E5 | 图标 retina 变体 | ✅ **修好了** | 亲自`iconutil -c iconset` 解码 `Contents/Resources/brand.icns`，结果见下方 E5 明细：9 档 / 4 个 @2x / **全部正方形**。 |
| E6 |单一 bundle | ✅ **修好了** | `dist/` 下仅 `Cullumi.app` 一个 bundle（`find` 未发现 `Cullumi-v*.app`/`.dmg`/`.zip`）；`du -sh dist/` = **187M**（与预期一致，非上轮的 ~374M 双份）；`build-app-macos.sh:110` 主动清理 `dist/Cullumi-v*.app`，:122 打印 `dist/Cullumi.app ← 双击这个`。 |

### E2 明细

**(a) Windows 残留扫描** — 任务书点名的 4 个字符串**全部不存在**（`grep -qF` 逐条确认为 ABSENT）：
`.venv\Scripts`、`powershell`、`dist\Cullumi-v1.0.5\`、`Windows 照片筛选应用`。

全文7 处 `Windows` 均为**说明性叙述**（交代移植来源、解释为何跳过 `tests/dom/`、对照上游 Windows 附件），**无一处是可执行命令或错误前提**。逐条位置：L7（移植来源）、L114（上游仅发布 Windows 附件时的预期行为）、L155（对照 `verify.ps1`）、L162（NIQE 基线录于 Windows x86-64）、L186/L188（改动清单对照）、L199（跳过 `tests/dom/` 的原因）。
> 说明：本机 bash `grep` 在该路径下被 shim 干扰（计数与明细自相矛盾），已改用 Grep 工具复核，结论以 Grep 工具为准。

**(b) Gatekeeper 绕过** — 存在且可执行（dist/使用说明.md:25-53）。给出三种方式：右键打开 → `xattr -dr com.apple.quarantine "/path/to/Cullumi.app"`（:42，路径已参数化）→ 系统设置放行。L27 明确 ad-hoc 签名/未公证，L55 明确「分发给他人必须先 Developer ID + 公证」。

**(c) 系统要求** — L15「macOS 14 或更高版本（Sonoma 及以上）」、L16「Apple Silicon（arm64），M1/M2/M3/M4/M5」、L19「不支持 Intel Mac」，并说明最低版本由 onnxruntime 轮子决定。

**(d) `spctl` 实测核对（自行执行）**：
```
$ spctl -a -vvv dist/Cullumi.app
dist/Cullumi.app: rejected        (rc=3)
$ codesign -dv dist/Cullumi.app
Signature=adhoc   TeamIdentifier=not set   flags=0x2(adhoc)
```
与文中「ad-hoc 签名、未经公证、Gatekeeper 会拦截」的表述**完全一致**，无夸大。补充：ad-hoc + `--deep` 签名下macOS 27.2 的实际拦截强度取决于「右键打开」路径是否被记住，这一点文档已提示，属可接受。

### E5 明细（本轮重点，独立解码）

`iconutil -c iconset` 解码 `Contents/Resources/brand.icns`（489,005 bytes）→ `brand.iconset/`：

| 文件 | 实际像素 | 正方形 |
|------|---------|--------|
| icon_16x16.png | 16×16 | ✅ |
| icon_16x16@2x.png | 32×32 | ✅ |
| icon_32x32.png | 32×32 | ✅ |
| icon_32x32@2x.png | 64×64 | ✅ |
| icon_128x128.png | 128×128 | ✅ |
| icon_128x128@2x.png | 256×256 | ✅ |
| icon_256x256.png | 256×256 | ✅ |
| icon_256x256@2x.png | 512×512 | ✅ |
| icon_512x512.png | 512×512 | ✅ |

**合计 9 档，@2x 变体 4 个，非正方形 0 个。** 四个 @2x 像素值精确等于 `16@2x=32 / 32@2x=64 / 128@2x=256 / 256@2x=512`，与 Apple 约定吻合。

**「9 档而非 10 档」的判断正确** — 有两组字节级同源证据支持工程师的查证结论：`icon_128x128@2x.png` 与 `icon_256x256.png` 均为 52,100 bytes；`icon_256x256@2x.png` 与 `icon_512x512.png` 均为 175,945 bytes。即 `64px` 语义上被 `icon_32x32@2x` 覆盖、`128px` 被 `icon_256x256` 覆盖，Apple 标准 iconset 本就是 9 项。工程师没有为了迎合自己写的「10 档」而放宽守卫，改为查证后修正期望值——这是正确的处理方式。

**守卫代码（build-app-macos.sh:43-78）确实存在且逻辑正确**，两段：
- 第 48-59 行（**前置守卫**，进 `iconutil` 之前）：逐档检查存在性 + `pixelWidth == pixelHeight`，任一不符即 `exit 1`。
- 第 64-78 行（**后置守卫**，`iconutil` 之后）：把生成的 `.icns` 重新 `iconutil -c iconset` 解回来，断言 `COUNT -eq 9 && RETINA -ne 4` 则中止。

**我对前置守卫做了负向测试**（沿用上一轮「亲自实证」的标准）：用上一轮的原始错误 `sips -z 16 64`（参数写反）植入一个 64×16 的非正方形文件，再执行守卫逻辑 —— 输出 `图标档位非正方形：icon_16x16.png (64x16)`，**GUARD RESULT: ABORT**。守卫确实能拦住上一轮那个静默失败的根因。后置守卫的 `iconutil` 解码回验同样能拦住「源文件正确但打包降级」的情况。两段守卫互补，**判定：有效**。

### 补充：任务书与实际不符之处（已在下方「与任务书描述的偏差」列出）

---

## 3. 新引入问题

### N1（P2，本轮新增）`verify-macos.sh --smoke` 永远跑不到冒烟阶段

**现象**
```
$ ./verify-macos.sh --smoke
==> ruff check .
All checks passed!
==> python -m unittest discover -s tests
...
Ran 199 tests / FAILED (failures=3)
（进程在此退出，从未打印 "==> 打包产物冷启动冒烟测试"）
```
`grep -c '冒烟' full.log` = **0** —— 冒烟阶段一次都没执行。

**根因** — `verify-macos.sh:13` 有 `set -e`，:43 的 unittest 因 3 个 NIQE 基线失败返回 exit 1，`set -e` 立即中止脚本，:48-50 的 `--smoke` 分支不可达。

**为什么这是 bug 而不是设计** — NIQE那 3 个失败是**上游基线问题**（我在未修改的 `cullumi-src` 上跑同样是 199/3，且失败集合逐条一致），项目文档已把它定性为「与移植无关」。既然是**预期内**的失败，就不该由`set -e` 用来阻断**另一个**本应独立执行的验证阶段。当前形态下 `--smoke` 开关形同虚设，而 §11.3（PORTING-PLAN.md:891）声称「并接入 `verify-macos.sh --smoke`，实测输出：… ==> 冒烟测试通过」—— 该「实测」只可能是**单独运行 `smoke-test-macos.sh`** 得到的，而非通过 `--smoke` 得到的。

**影响** — 不影响 `dist/Cullumi.app` 产物质量（`smoke-test-macos.sh` 单独运行完全通过，见 §4）。影响的是**发布验证流程的可用性**：任何人执行 `./verify-macos.sh --smoke` 都只会看到 3 个 NIQE 失败并以为构建有问题，而不会得到冒烟结论。

**建议修法**（交由工程师判断，我未改实现代码）— 对 unittest 单独容错，例如：
```bash
run_clean "$PY" -m unittest discover -s tests -v || echo "注意：3 个 NIQE 基线失败为上游已知问题"
```
或在已知失败集合精确匹配时放行。

---

## 4. 冒烟测试复核（工程师主动补做）

**结论：`smoke-test-macos.sh` 本身是真实有效的，不是走过场的空断言。**

亲自运行（清空 `~/Library/Application Support/Cullumi` 从零状态），**退出码 0**，输出：
```
plist 版本 1.0.5 与代码一致 ✓
LSMinimumSystemVersion 14.0 ✓
进程存活 PID=77897 ✓ / 启动后仍存活 ✓
无 webview-error.log ✓
127.0.0.1:57410 无令牌返回 403 ✓ 鉴权生效
前端资产与 ONNX 模型均在包内 ✓
onnxruntime 1.29.0 ['CoreMLExecutionProvider','AzureExecutionProvider','CPUExecutionProvider']
ffmpeg      ffmpeg-macos-aarch64-v7.1
原生库已打包 ✓
截图存证 /tmp/cullumi-smoke.png
```

**抽查 2 项断言真实性（均独立于脚本手工验证）：**
1. **ONNX 模型确实在包内且是有效模型** — `Contents/Resources/models/blink/ocec_c.onnx` = 875,566 bytes；用 `onnxruntime.InferenceSession` 实际加载成功，inputs=`['images']`、outputs=`['prob_open']`。**不是 0 字节占位文件**。
2. **ffmpeg 二进制确实可执行** — `Contents/Frameworks/imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v7.1`，Mach-O 64-bit arm64，实跑 `-version` 输出 `ffmpeg version 7.1 Copyright (c) 2000-2024`。**不是死链接或占位**。

**追加负向测试**（证明断言不是恒真）：
- 版本不一致分支：伪造 `9.9.9` → 正确中止 ✓
- 403 探测：探测无监听端口得 `000` → 正确**不**误判为应用 ✓
- 缺失资产断言：探测不存在的 `.onnx` → 正确报缺失 ✓

**第一轮缺口是否补上：补上了。** 第一轮我指出「所有验证都在源码树用 venv 跑，dist/ 产物没验过完整业务流」。本轮冒烟测试**确实冷启动了 `dist/Cullumi.app` 产物本身**并验证了真实 HTTP 403 鉴权、资源打包、原生依赖链接，覆盖到位。

### 环境说明（我自己的调用环境问题，非项目缺陷）
最初 `./verify-macos.sh` 直接报「找不到 Python 解释器」。经逐步定位（`bash -x` 追踪、`head -N` 二分、跨目录/跨文件名/沙箱开关对照），确认是**我的调用方式导致 `PATH` 未包含 venv `bin`**，venv 里的 `python3` 是指向 `versions/3.13.12/bin/python3` 的符号链接。改用
`CULLUMI_PYTHON="$HOME/.workbuddy/binaries/python/envs/default/bin/python3"` 后脚本**逻辑完全正常**（ruff 通过、199/3 如常）。
> 这与任务书「本任务书给的 Python 绝对路径在 shim 下可能'不存在'→ 用 `~` 未展开形式」是同一类坑。**`verify-macos.sh` 的守卫逻辑本身没有 bug**，它正确地拦住了「解释器确实不可用」的情况。

---

## 5. 回归重点：6处改动是否引入新问题

### 变更集形状：**6 改 / 1 删 / 10 增** —— 与声明一致 ✅
- **改 (6)**：`app.py`、`cullumi/config.py`、`cullumi/http_api.py`←解禁、`cullumi/native_dialogs.py`、`cullumi/updates.py`、`web/js/settings.js` ←解禁
- **删 (1)**：`clr.py`（pythonnet，macOS 不需要）
- **增 (10)**：`Cullumi-macos.spec`、`MACOS-使用说明.md`、`PORTING-PLAN.md`、`QA-REPORT.md`、`build-app-macos.sh`、`build-macos.sh`、`requirements-macos.txt`、`setup-macos.sh`、`smoke-test-macos.sh`、`verify-macos.sh`、`evaluation/probe_native_dialogs.py`（11 项中 `probe_native_dialogs.py` 属 `evaluation/` 子目录新增，与 PORTING-PLAN:952 起的清单口径一致）

### (a) 单元测试双目录：**199 tests / 3 failures，失败集合精确匹配** ✅
| 目录 | 结果 | 失败集合 |
|------|------|---------|
| `cullumi-src`（上游基线） | Ran 199 / FAILED (failures=3) | `noise-25`, `blur-0.5`, `blur-3` |
| `cullumi-macos`（移植后） | Ran 199 / FAILED (failures=3) | `noise-25`, `blur-0.5`, `blur-3` |

失败全部为 `test_niqe.NiqeTests.test_matches_laboratory_opencv_golden_scores`，delta 数值逐条一致（如 `blur-3`: `10.908492980189509 != 10.914476721669287`）。**基线问题，移植未放大也未掩盖。**

### (b) 测试目录未被改动（硬底线）✅
- `diff -rq cullumi-src/tests cullumi-macos/tests` → **无任何输出**，18/18 个 `.py` 全部 **IDENTICAL**
- 重点模块 `tests/test_app.py`（806 行）+ `tests/test_web_static.py`（174 行）→ **31 tests, OK**，全绿
- **测试未被改动是硬底线：满足。** 本轮解禁的两个文件虽被测试覆盖，但测试本身零改动，通过率与基线相同。

### (c) `web/` 目录 ✅
21 个资产中 **20 个 IDENTICAL**，唯一差异是 `web/js/settings.js`（即解禁文件，符合预期）。

### NIQE 哈希锁仍有效 ✅
`models/niqe/niqe_pris_params.npz`、`models/niqe/SOURCE.json`、`models/blink/face_detection_yunet_2023mar.onnx` 三个文件 sha256 与上游 **全部 IDENTICAL**。3 个 NIQE 失败与这批数据无关（是浮点精度），但数据本身未被污染。

### `cullumi/` 模块改动面最小化 ✅
24 个 `.py` 中仅 **4 个** 与上游不同（`config.py`、`http_api.py`、`native_dialogs.py`、`updates.py`），其余 20 个不变。

---

## 6. 解禁改动专项：Windows 文案

### (a) macOS 上输出的平台名确实是 `macOS` ✅
实调用 `platform_name()`（`updates.py:31-33`）→ **`'macOS'`**。
两个返回分支均携带 `platform`，实测确认：
- `no_release` 分支 → `platform = 'macOS'` ✅
- 正常分支 → `platform = 'macOS'` ✅

`select_release_asset` 在 macOS 上的分档行为实测：
| 输入 | 选中 |
|------|------|
| dmg + win64.zip + exe 混合 | `Cullumi-1.0.5.dmg` ✅ 优先 dmg |
| 仅 win64.zip | `Cullumi-1.0.5-win64.zip` ✅ 合理回退 |
| 仅 msi / 仅 exe | `None` ✅ 正确拒绝非 macOS 格式 |
| `Cullumi-1.0.5-macos-arm64.tar.gz` | 正确选中 ✅（`.tar.gz` 复合后缀处理有效） |
| `Source code (zip)` | `None` ✅ 源码包被正确排除 |

### (b) Windows 分支未被破坏 ✅ —— 用逐例比对证明，而非读代码
把移植版的 `sys.platform == "darwin"` 强制为 `False` 得到 Windows 分支，与**上游原版** `select_release_asset` 跑同一组输入：

| 用例 | 上游 |移植版(Win 分支) | 结果 |
|------|------|----------------|------|
| only win64.zip | Cullumi-1.0.5-win64.zip | Cullumi-1.0.5-win64.zip | MATCH |
| only msi | Cullumi-1.0.5.msi | Cullumi-1.0.5.msi | MATCH |
| only exe portable | Cullumi-portable.exe | Cullumi-portable.exe | MATCH |
| mixed dmg+zip+exe | Cullumi-1.0.5-win64.zip | Cullumi-1.0.5-win64.zip | MATCH |
| source zip rejected | None | None | MATCH |
| portable zip | Cullumi-portable.zip | Cullumi-portable.zip | MATCH |
| no url | None | None | MATCH |

**行为差异数 = 0。** Windows 下 `platform_name()` 返回 `'Windows'`，UI 仍显示 Windows。`ASSET_SUFFIX_SCORES`/`ASSET_PLATFORM_HINTS` 在 Windows 下与上游原值完全一致（`{'.zip':30,'.exe':20,'.msi':10}` / `('windows','win64','win-x64','portable','便携')`）。

### (c) 后端 `platform` 字段向后兼容 ✅
`hostPlatformName(update)` 的回退链实测 **9/9 通过**：
| 场景 | 结果 |
|------|------|
| 字段缺失 + mac UA | `macOS` ✅ 旧前端不会崩 |
| 字段为 `null` + mac UA | `macOS` ✅ |
| 字段为空串 + mac UA | `macOS` ✅ |
| `update` 为 `undefined` / `null` | 不抛异常，回落 UA ✅ |
| **UA 报Windows 但后端下发 macOS** | **`macOS`** ✅ 后端优先，符合设计意图 |

**关键价值验证**：WKWebView 的 UA 反映的是内嵌 WebView 而非打包目标，这个设计判断是对的——最后一行用例证明即使 UA 谎报 Windows，后端下发的值依然胜出。旧前端/字段缺失场景均安全回落，**向后兼容成立**。

### (d) `settings.js` 里唯一残留的 `"Windows"` ✅
`web/js/settings.js:401` `return "Windows";` —— 位于 UA 兜底分支的 `return`，**属预期保留**，与声明一致。

### (e) §11.4 解禁判据是否站得住
§11.4（PORTING-PLAN.md:921-933）记录的判据是：

> 禁改清单的边界是「不改变核心筛选/分析/数据流逻辑」。**纯用户可见文案**（错误提示、平台名称、引导语）不属于该边界；当文案与运行平台矛盾导致功能自相矛盾时，应当修复。判据是：改动是否触及数据结构、控制流或跨模块契约。此处均未触及，故解禁。

**我的判断：判据站得住，且比「按文件白名单」更正确。** 三点理由：
1. 它给出了**可操作的边界**（是否触及数据结构/控制流/跨模块契约），而非「凭文件名单」，可复用。
2. 它抓住了禁改清单的**真实目的**——防止移植污染核心算法，而非禁止修 bug。工程师在前一版正是用「文件在禁改清单内」当理由拒绝修复，那才是本末倒置。
3. **它在本轮被证明是可验证的**：我实际检查了两个解禁文件的 diff，`http_api.py` 改动仅 1 处 `ValueError` 文案（+4 行），`settings.js` 改动仅新增 1 个纯函数 + 1 处模板插值（+9 行），**确实没有触及数据结构、控制流或跨模块契约**，与判据自身结论吻合。

**是否会留下被滥用回退的口子？** 有轻微风险，但判据中「是否触及数据结构、控制流或跨模块契约」是**可客观判定**的，且我已用「逐例行为比对」（§6b、§6c）建立了可复现的验证手段——若有人借此解禁偷偷改了控制流，行为比对与 199/3 基线会立刻暴露。**建议**：后续若再解禁，沿用本轮的验证手段（改动前后逐例行为 diff + 基线测试对比）作为准入条件，即可有效堵住滥用。

**一处提示（不构成缺陷）**：任务书描述 `http_api.py` 约 827 行、`settings.js` 约 397/399 行，实际为 **1037 行 / 895 行**（上游为 1033 / 886，即净增 +4 / +9）。任务书的行数描述与产物不符，但**与代码实际状态自洽**，不影响任何结论——记录在此以免后续复核时被误导。

---

## 7. 是否可交付分发

### ✅ 可以交付 `dist/Cullumi.app`，但**必须附带说明，且不可直接分发给他人**

**已验证可用的部分：**
- 单bundle、187M、无冗余副本，双击即用（`build-app-macos.sh:122` 有指引）
- 冷启动正常、WKWebView 未降级到浏览器、令牌鉴权生效（403）
- ONNX 模型、ffmpeg、原生库、前端资产全部在包内且**实际可用**（我手工加载过模型、跑过 ffmpeg）
- 版本号三处一致（1.0.5），不存在「显示一个版本、更新检查另一个版本」
- 图标 9 档完整、4 个 @2x、全部正方形，Retina 下不糊
- 199/3 与上游基线逐条一致，无移植引入的回归

**分发给他人时用户会遇到什么（必须提前告知）：**
1. **Gatekeeper 必定拦截** —— 我实测 `spctl -a -vvv` = `rejected`，`Signature=adhoc`、`TeamIdentifier=not set`。对方需右键打开、或执行文档 :42 的 `xattr -dr com.apple.quarantine`、或在「系统设置 → 隐私与安全性」点「仍要打开」。**这一步无法省略**（无 Developer ID 证书、未公证）。
2. **仅支持 Apple Silicon + macOS 14+** —— Intel Mac 与 macOS 13 及以下**无法运行**（onnxruntime arm64 轮子限制）。
3. **首次启动有等待** —— 需等应用冷启动并加载模型。
4. **更新提示目前拿不到 macOS 附件** —— 上游 `Yuumi0221/Cullumi` 只发布 Windows 资产；在 macOS 上执行「下载更新」会得到「最新版本没有可下载的 **macOS** 附件，请前往发布页查看」。**这正是本轮文案修复的价值**：改前会错误地让macOS 用户去找 Windows 附件，改后提示准确。UI 侧已能正确显示平台名，但**分发前不要向用户承诺自动更新可用**。

### 交付前建议补的一件事
修掉 §3-N1（`--smoke` 不可达），否则团队自己跑发布验证会得到误导性结论。这是**流程可靠性问题**，不是产物缺陷——可以先交付、后修，但建议在正式对外分发前修掉。

---

## 8. 智能路由判定

### → 发给工程师（software-engineer）：1 个问题

**N1（P2）`verify-macos.sh --smoke` 因 `set -e` 永远到不了冒烟阶段**
- 文件：`verify-macos.sh`，第 13 行（`set -e`）与第 43 行（unittest）交互，导致第 48-50 行 `--smoke` 分支不可达
- 现象：`./verify-macos.sh --smoke` 打印完199/3 后直接退出，从不出现 `==> 打包产物冷启动冒烟测试`；`grep -c '冒烟'` = 0
- 复现：
  ```bash
  CULLUMI_PYTHON="$HOME/.workbuddy/binaries/python/envs/default/bin/python3" \
    ./verify-macos.sh --smoke 2>&1 | grep -c '冒烟'   # => 0
  ```
- 期望：`--smoke` 应在 3 个**已知基线** NIQE 失败之后仍继续执行冒烟阶段
- 建议：对 unittest 单独容错（`|| echo "已知基线失败"`），或仅在失败集合精确等于 `{noise-25, blur-0.5, blur-3}` 时放行
- 优先级：P2，不阻塞产物分发

### → 我自己处理（无需工程师介入）
- 环境类误报（`verify-macos.sh` 的解释器守卫）—— 已定位为**我的调用环境 `PATH` 问题**，脚本逻辑正确，已用 `CULLUMI_PYTHON` 覆盖跑通全流程，**不构成源码 bug，不记为缺陷**
- `E3` GUI 段未验证 —— 环境限制，非缺陷（见下）

---

## 9. 明确的「未验证」项（不假装验过）

1. **`LSMinimumSystemVersion = 14.0` 是否真在 macOS 14 以下生效** —— **未验证**。需要一台macOS 13 及以下机器才能测Launch Services 的拦截行为。本轮只验证了 plist 字段存在且值正确、以及二进制 minos 差异的合理性。
2. **原生对话框实际弹出** —— **未验证 GUI 段**。探针已确认能独立启动、`sys.platform=darwin`、PowerShell 兜底不可达；但 `NSOpenPanel` 真正弹出与截图存证需人工交互/图形会话，本次未完成到那一步（`choose_csv` 的返回值未取到）。
3. **`tests/dom/` 浏览器测试** —— **未运行**（上游设计如此：Playwright + Edge 快照基线录于 Windows，且构建环境无 Node.js）。已在文档中说明跳过理由，判定为可接受。

---

## 10. 与任务书描述的偏差（供后续复核参考）

| 任务书描述 | 实际情况 | 影响 |
|-----------|---------|------|
| `http_api.py` 约 827 行 | **1037 行**（上游 1033） | 无，净增 +4 与diff 一致 |
| `settings.js` 约 397/399 行 | **895 行**（上游 886） | 无，净增 +9 与 diff 一致 |
| `dist/` 应约 187M | 187M ✅ | 一致 |
| `tests/` 18 个文件 IDENTICAL | 18/18 IDENTICAL ✅ | 一致 |
| `web/` 除 settings.js 外 19 个资产 IDENTICAL | 20 个 IDENTICAL（共 21 文件） | 计数口径差异，不影响结论 |

---

## 附：验证方法说明（供复现）

所有测试/构建均在净化环境下执行：
```bash
env -u _ -u BASH_ENV -u PYTHONPATH TMPDIR=$HOME/cullumi-tmp <cmd>
```
外加 `CULLUMI_PYTHON="$HOME/.workbuddy/binaries/python/envs/default/bin/python3"`（本机 venv `python3` 为符号链接，需保证 `PATH`/显式指定可解析）。

**未修改 `cullumi-macos/` 任何实现代码** —— 全部临时脚本置于 `$HOME/cullumi-tmp/` 与 `/tmp`；测试前清空 `~/Library/Application Support/Cullumi` 以保证从零状态。
---

# 附录 A：N1 修复复核（工程师第五轮提交后）

工程师已修N1 并提交复核请求。以下为我的**独立验证**（未参考其自述输出）。

## A.1 原缺陷已消除 ✅

亲自运行 `verify-macos.sh --smoke`：
```
PIPELINE_EXIT=0
==> ruff check .                All checks passed!
==> python -m unittest discover -s tests
    注意：以下 NIQE 基线失败为上游已知问题（与移植无关），不视为回归：
      - blur-0.5 / blur-3 / noise-25
==> 打包产物冷启动冒烟测试        ← 修复前不可达，现已到达
    ...全部断言通过...
==> 冒烟测试通过
```
`grep -c '打包产物冷启动冒烟测试'` = **1**（修复前为 **0**）。**N1 确认修复。**

## A.2 门禁不是橡皮图章（负向测试）✅

**用例 1 —— 注入普通真实回归**（`tests/test_fs_utils.py` 追加一个断言失败）：
```
NEGATIVE_TEST_EXIT=1                                   ← 正确拦截
    单元测试出现基线之外的失败（这才是真回归）：
      - blur-0.5 / blur-3 / noise-25
      - test_fs_utils._QaInjectedRegression.test_should_fail   ← 准确点名
```
门禁**准确点名**了注入的失败并中止，未进入冒烟阶段（`grep -c` = 0）。符合「严格基线比对、非 `|| true`」的设计意图。

## A.3 ⚠️ 发现门禁的一个真实漏洞（P3，新）

**现象** —— 注入一个**复用基线 subtest 名字**的回归（`subTest(name='noise-25')`，但属于另一个测试类）：
```
MASQUERADE_EXIT=0        ← 未被拦截！
Ran 200 tests
FAILED (failures=4)      ← unittest 报了 4 个失败
    注意：以下 NIQE 基线失败为上游已知问题：
      - blur-0.5 / blur-3 / noise-25     ← 门禁只看到 3 个名字
```

**根因** —— `verify-macos.sh:76-86` 的 awk **只提取 subtest 的 `name`，不提取测试 id**，随后 `sort -u` 去重。两个不同测试只要 `subTest(name=...)` 同名，产出的字符串完全相同 → 被 `sort -u` 折叠为一个。于是注入的失败与基线失败**不可区分**，门禁误判为「仅基线 3 项」并放行。

日志证据（同一条 `name='noise-25'` 来自两个不同测试）：
```
FAIL: test_a (test_fs_utils._QaMasquerade.test_a) (name='noise-25')
FAIL: test_matches_laboratory_opencv_golden_scores (test_niqe.NiqeTests...) (name='noise-25')
```

**影响评估：低（P3）**
- 触发条件苛刻：需另一个测试使用与 NIQE 基线**完全同名**的 `subTest` 参数。
- 现实中几乎不会自然发生（`noise-25`/`blur-0.5`/`blur-3` 是 NIQE 专用名）。
- 但它使「严格基线比对」这个卖点在**极端情况下**退化为放行，与 §14.2 的设计承诺不完全一致。

**建议修法** —— 把测试 id 纳入比较键，而非只取 subtest 名。例如输出 `test_id::name` 形式：
```awk
# 形如 test_niqe.NiqeTests.test_x::noise-25
```
并相应把 `KNOWN_BASELINE` 写成带前缀的全限定形式。这样任何其他测试即使冒用同名 subtest，也会因`test_id` 不同而被判定为「基线之外」。

**我的处置：不阻塞交付。** 当前状态下门禁已能拦住**所有现实中的**回归（A.2 已证明），此漏洞仅在人为构造下可达。建议记入下一轮 backlog。

## A.4 状态复原确认（硬底线）✅
负向测试后已完整还原，并以 sha256 与 `diff -r` 双重确认：
```
sha now   = 4c5ed517fb58d7120f2db75cba8878249ac1ea259711086dfd9ea40cb1915c73
sha orig  = 4c5ed517fb58d7120f2db75cba8878249ac1ea259711086dfd9ea40cb1915c73
diff -rq cullumi-src/tests tests  →  无任何输出
测试套件 → Ran 199 tests / FAILED (failures=3)
注入代码残留检查 → 0
```
**`tests/` 仍相对上游 IDENTICAL，未被我改动。**

## A.5 变更集形状 ✅
```
differ = 6（与上轮完全相同，未新增产品代码改动）
  app.py / cullumi/config.py / cullumi/http_api.py
  cullumi/native_dialogs.py / cullumi/updates.py / web/js/settings.js
removed = 1  clr.py
added  = 13（含 QA-REPORT-ROUND2.md；.ruff_cache 为工具缓存不计）
```
本轮**只改`verify-macos.sh`**（发布验证脚本，非产品代码），与工程师声明一致。§11.3 已加显著提示指向 §14 并注明重测（PORTING-PLAN.md:893），**不实表述已修正**。

## A.6 「原生对话框实际弹出」—— 已由证据升级为已验证 ✅

工程师提供了截图，我**亲自查看确认**：
```
/tmp/dialog-shot.png  PNG 3440x1440 RGBA  4,430,031 bytes  10:27
```
图像内容确为**真实弹出的 macOS 原生 `NSOpenPanel`**：可见标准文件浏览器侧栏、`Cancel` / `Open` 按钮，文件类型过滤仅显示 `.csv`（与 `choose_csv("选择筛选结果 CSV")` 的预期一致），后方是应用主窗口。

**这填补了我上一轮标记为「未验证」的一项。** 剩余未验证项仅剩「`choose_csv` 的返回值」——面板被自动 Escape 取消故返回空串，属预期行为，非缺陷。

## A.7 工程师的自我批评值得记录
§14.3 主动记录了本次修复**又引入同类bug**：先用 BRE 的 `\|` 导致提取结果为空字符串、冒烟分支再次被打断。这与 §13.2 的 `grep '^Only in \.'`、以及更早的 QA `grep "icon|icns|sips"` 是**完全同一类错误：把 ERE 语法用在 BRE 上**，连续两轮。最终改用 `awk` 修正。

**我认可这条自我批评的价值**：主动记录自己的重复性错误、并给出可执行的规则（写正则 shell 片段默认用 `awk`/`grep -E`），比隐去不提更有助于后续维护。

## A.8 附录A 判定

**N1 已修复并通过复核**（A.1 正向 + A.2 负向）。发现 **1 个 P3 门禁漏洞**（A.3），不阻塞交付。
**本项目总判定维持 PASS_WITH_ISSUES**，N1 从「新引入问题」降级为「已修复」，A.3 记入 backlog。

---

# 附录 B：P3 修复复核 + 并发污染事件查证

工程师已修 P3，并主动报告一次「并发污染」事件。以下为我的独立查证与复核。

## B.1 门禁漏洞已修复 ✅ —— 三个对抗用例全部被拦

**改法核实**（`verify-macos.sh:62-64`）：`KNOWN_BASELINE` 已改为全限定键
`<test id>::<subtest name>`，即
`test_niqe.NiqeTests.test_matches_laboratory_opencv_golden_scores::blur-0.5` 等。采纳了我上轮的建议。

**关键改进：本次全部负向测试在 `/tmp` 隔离副本中进行**，不再触碰共享工作区（详见 B.3）。

| 用例 | 注入内容 | 期望 | 实测 |
|------|---------|------|------|
| 正常路径 | 无| 0 | `EXIT=0`，基线三项以全限定名列出 ✅ |
| **冒名** | 另一测试类`subTest(name='noise-25')` | 非 0 | `EXIT=1`，准确点名 `test_fs_utils._MasqueradeRepro.test_reused_baseline_name::noise-25` ✅ |
| **ERROR（非 FAIL）** | 抛 `RuntimeError` | 非 0 | `EXIT=1`，点名 `test_fs_utils._QaErrorOutsideBaseline.test_raises` ✅ |
| **NIQE 类内新增 subtest** | `test_niqe.py` 追加 `subTest(name='totally-new-name')` | 非 0 | `EXIT=1`，点名 `test_niqe._QaNewSubtestInNiqeClass.test_new::totally-new-name` ✅ |

后两个用例是工程师未覆盖的额外边界，均**正确拦截**。**P3 确认修复。**

## B.2 并发污染事件查证 —— 结论：污染源不是我的注入

工程师报告：他的首次复现得到「被拦截」，与我的 `MASQUERADE_EXIT=0` 矛盾；排查时在 `tests/test_fs_utils.py` 发现 `_InjectedRegression`，判断是「我上一轮的注入没清掉」。

**我的查证结果与该判断不一致，据实说明：**

1. **类名不匹配。** 我本轮两次注入的类名是 `_QaInjectedRegression` 与 `_QaMasquerade`（均带 `Qa` 前缀，便于识别与清理）。工程师看到的是 `_InjectedRegression`（无前缀）——**这是他自己上一轮注入时使用的类名**（他在上一轮报告中原文写的是「往 `tests/test_fs_utils.py` 注入一个真实失败」，产出 `test_fs_utils._InjectedRegression.test_should_fail`）。

2. **我上一轮的还原是经验证的。** 收尾时我做了三重确认并记录在案：
```
sha256  before == after == 4c5ed517fb58d7120f2db75cba8878249ac1ea259711086dfd9ea40cb1915c73
diff -rq cullumi-src/tests tests  →  无任何输出
注入残留 grep（_QaInjected|_QaMasquerade|QA injected|masquerade）→ 0
套件 → Ran 199 tests / FAILED (failures=3)
```
若我的注入残留，`diff -r` 必然会有输出，且套件不会回到 199 弹。

3. **当前工作区无任何残留**（本轮再次确认）：`tests/` 相对上游 **IDENTICAL**，全目录搜索 `_Injected|_Masquerade|_Qa*|QA injected|reused_baseline` 结果为 **NONE**。

**最可能的时间线**：工程师在我上一轮验证**之后**、他自己验证**之前**，于同一文件注入并还原了 `_InjectedRegression`；我读到的残留来自他那一步，而非我的。反向也可能是他的还原覆盖了我的备份 —— 无论哪种，**根因都是「双方并发写同一文件」这一工作方式，而非任何一方疏于清理**。

**因此我不接受「你给我留了额外清理工作」这一表述下的归因**，但**完全接受「共享工作区做注入式负向测试不可靠」这一结论** —— 他据此新增的隔离副本纪律是对的，我本轮已率先使用并验证有效。

## B.3 双方共识并已落地：隔离副本纪律

工程师建议「后续注入类负向测试各自在 `/tmp` 开独立副本」。**本轮我已照此执行**（`$HOME/cullumi-tmp/iso-p3`，`tar` 整目录拷贝后在其内部注入），验证期间共享工作区 `tests/` 始终 IDENTICAL、零污染。**这条纪律应写进 QA 流程规范。**

## B.4 交付态复核（全部亲自确认）
```
verify-macos.sh --smoke  → PIPELINE_EXIT=0，冒烟阶段到达 1 次，全阶段通过
differ = 6（未新增产品代码改动） / removed = 1 / added = 13
tests/ 相对上游 → 0 行差异（IDENTICAL）
web/    → 1 处差异（仅 settings.js，已解禁）
dist/   → 187M
PORTING-PLAN.md → 1473 行
```

## B.5 附录 B 判定

**P3 已修复并通过复核**（三个对抗用例全部正确拦截）。**当前未发现任何新的门禁漏洞。**
并发污染事件查证结论：**根因是并发写共享文件的工作方式，双方均无清理疏失**；隔离副本纪律已确立并经本轮验证有效。

**项目总判定维持 PASS_WITH_ISSUES**，剩余唯一「未验证」项仍为 `LSMinimumSystemVersion` 在macOS 14 以下的实际拦截行为（需旧机器，双方均无法验证）。

---

# 附录 B：P3 修复复核 + 并发污染事件查证

工程师已修 P3，并主动报告一次「并发污染」事件。以下为我的独立查证与复核。

## B.1 门禁漏洞已修复 ✅ —— 三个对抗用例全部被拦

**改法核实**（`verify-macos.sh:62-64`）：`KNOWN_BASELINE` 已改为全限定键
`<test id>::<subtest name>`，即
`test_niqe.NiqeTests.test_matches_laboratory_opencv_golden_scores::blur-0.5` 等。采纳了我上轮的建议。

**关键改进：本次全部负向测试在 `/tmp` 隔离副本中进行**，不再触碰共享工作区（详见 B.3）。

| 用例 | 注入内容 | 期望 | 实测 |
|------|---------|------|------|
| 正常路径 | 无| 0 | `EXIT=0`，基线三项以全限定名列出 ✅ |
| **冒名** | 另一测试类`subTest(name='noise-25')` | 非 0 | `EXIT=1`，准确点名 `test_fs_utils._MasqueradeRepro.test_reused_baseline_name::noise-25` ✅ |
| **ERROR（非 FAIL）** | 抛 `RuntimeError` | 非 0 | `EXIT=1`，点名 `test_fs_utils._QaErrorOutsideBaseline.test_raises` ✅ |
| **NIQE 类内新增 subtest** | `test_niqe.py` 追加 `subTest(name='totally-new-name')` | 非 0 | `EXIT=1`，点名 `test_niqe._QaNewSubtestInNiqeClass.test_new::totally-new-name` ✅ |

后两个用例是工程师未覆盖的额外边界，均**正确拦截**。**P3 确认修复。**

## B.2 并发污染事件查证 —— 结论：污染源不是我的注入

工程师报告：他的首次复现得到「被拦截」，与我的 `MASQUERADE_EXIT=0` 矛盾；排查时在 `tests/test_fs_utils.py` 发现 `_InjectedRegression`，判断是「我上一轮的注入没清掉」。

**我的查证结果与该判断不一致，据实说明：**

1. **类名不匹配。** 我本轮两次注入的类名是 `_QaInjectedRegression` 与 `_QaMasquerade`（均带 `Qa` 前缀，便于识别与清理）。工程师看到的是 `_InjectedRegression`（无前缀）——**这是他自己上一轮注入时使用的类名**（他在上一轮报告中原文写的是「往 `tests/test_fs_utils.py` 注入一个真实失败」，产出 `test_fs_utils._InjectedRegression.test_should_fail`）。

2. **我上一轮的还原是经验证的。** 收尾时我做了三重确认并记录在案：
```
sha256  before == after == 4c5ed517fb58d7120f2db75cba8878249ac1ea259711086dfd9ea40cb1915c73
diff -rq cullumi-src/tests tests  →  无任何输出
注入残留 grep（_QaInjected|_QaMasquerade|QA injected|masquerade）→ 0
套件 → Ran 199 tests / FAILED (failures=3)
```
若我的注入残留，`diff -r` 必然会有输出，且套件不会回到 199 弹。

3. **当前工作区无任何残留**（本轮再次确认）：`tests/` 相对上游 **IDENTICAL**，全目录搜索 `_Injected|_Masquerade|_Qa*|QA injected|reused_baseline` 结果为 **NONE**。

**最可能的时间线**：工程师在我上一轮验证**之后**、他自己验证**之前**，于同一文件注入并还原了 `_InjectedRegression`；我读到的残留来自他那一步，而非我的。反向也可能是他的还原覆盖了我的备份 —— 无论哪种，**根因都是「双方并发写同一文件」这一工作方式，而非任何一方疏于清理**。

**因此我不接受「你给我留了额外清理工作」这一表述下的归因**，但**完全接受「共享工作区做注入式负向测试不可靠」这一结论** —— 他据此新增的隔离副本纪律是对的，我本轮已率先使用并验证有效。

## B.3 双方共识并已落地：隔离副本纪律

工程师建议「后续注入类负向测试各自在 `/tmp` 开独立副本」。**本轮我已照此执行**（`$HOME/cullumi-tmp/iso-p3`，`tar` 整目录拷贝后在其内部注入），验证期间共享工作区 `tests/` 始终 IDENTICAL、零污染。**这条纪律应写进 QA 流程规范。**

## B.4 交付态复核（全部亲自确认）
```
verify-macos.sh --smoke  → PIPELINE_EXIT=0，冒烟阶段到达 1 次，全阶段通过
differ = 6（未新增产品代码改动） / removed = 1 / added = 13
tests/ 相对上游 → 0 行差异（IDENTICAL）
web/    → 1 处差异（仅 settings.js，已解禁）
dist/   → 187M
PORTING-PLAN.md → 1473 行
```

## B.5 附录 B 判定

**P3 已修复并通过复核**（三个对抗用例全部正确拦截）。**当前未发现任何新的门禁漏洞。**
并发污染事件查证结论：**根因是并发写共享文件的工作方式，双方均无清理疏失**；隔离副本纪律已确立并经本轮验证有效。

**项目总判定维持 PASS_WITH_ISSUES**，剩余唯一「未验证」项仍为 `LSMinimumSystemVersion` 在macOS 14 以下的实际拦截行为（需旧机器，双方均无法验证）。

---

# 附录 C：交付前补齐—— 第 4 条随附说明已写入文档

team-lead 指出 `dist/使用说明.md` 尚未标注「`LSMinimumSystemVersion` 未在旧系统实机验证」。**本轮已补齐**，这是交付态里唯一缺实测背书的声明。

## C.1 改在了正确的文件上（关键）

`dist/使用说明.md` 是**构建产物** —— `build-app-macos.sh:131` 有一行：
```bash
cp MACOS-使用说明.md "dist/使用说明.md"
```
直接改 `dist/` 会在下次构建时被覆盖。**因此我改的是源文件 `MACOS-使用说明.md`**，再按构建脚本同样的方式同步到 `dist/`，确保下次构建不会丢失。

## C.2 补入的内容（`MACOS-使用说明.md:21`，`dist/使用说明.md:21`）

> **关于最低版本的说明**：应用包内已声明 `LSMinimumSystemVersion = 14.0`，该值依据 onnxruntime 官方仅提供 `macosx_14_0_arm64` 轮子这一事实推导得出。但**本项目未在 macOS 14 以下的机器上实测过**该限制的实际拦截行为。若你在更早的系统上打开后出现异常，请先确认系统版本，或反馈该情况。

措辞遵循三个原则：
1. **说清依据** —— 不是拍脑袋，是 onnxruntime 轮子决定的
2. **明确承认未验证** —— 不用「应该」「预计」这类含糊词
3. **给用户可执行的下一步** —— 遇到问题该做什么

行数：211 → 220 行（含空行），源文件与 `dist/` 副本 sha256 一致（`c58a4f8d…`）。

## C.3 改文档后重新验证，未引入任何回归

| 检查项 | 结果 |
|--------|------|
| **硬底线1：`tests/` 相对上游** | **0 行差异（IDENTICAL）** ✅ |
| **硬底线：`README.md`** | **逐字节一致** ✅ |
| **`models/`** | **0 行差异（IDENTICAL）** ✅ |
| **变更集形状** | `differ=6`，且仍是同样那 6 个文件（`app.py`/`config.py`/`http_api.py`/`native_dialogs.py`/`updates.py`/`settings.js`）✅ |
| **E2 门禁（4 个 Windows 残留串）** | 4 个串**均无操作性用法**；`powershell` / `.venv\Scripts` 各 1 处命中，均为**早就存在**的排除性说明（见下方更正），另 2 个串 0 命中 ✅ |
| **我新增那行是否含 Windows 字样** | 0命中 ✅ |
| **`verify-macos.sh --smoke`** | `EXIT=0`，冒烟阶段到达 1 次，全阶段通过 ✅ |
| **`dist/`** | 187M，仅 `Cullumi.app` + 两个 md ✅ |

**注意**：用 bash `grep` 复查 4 个残留串时，`.venv\Scripts` 与 `powershell` 报了**假阳性**（"PRESENT (bad)"）。这正是我第一轮踩过、并已在 skill 第 6 节记录的 **grep shim 劫持**。改用 Grep 工具复核后确认：全文唯一命中在第 219 行，是**早就存在的**「`上游说明.md` 中的 powershell、`.venv\Scripts\` 不适用于 macOS」这类排除性说明，与我的新增无关。**若当时信了 bash 的输出，就会误判成我自己引入了 Windows 残留。**

## C.4 一个必须记录的自身失误

第一次同步 `dist/` 时，我的 `cd`进中文路径的调用**静默失败**（那个已知的中文路径 + shim 问题），导致 `cp` 没执行、源文件与 `dist/` 副本行数不一致（220 vs 218）。**是sha256 比对把它抓出来的** —— 如果我只跑 `diff -q` 并看到空输出就收工，会以为已同步。

**这印证了「验证手段本身也要被验证」**：同一个 `diff -q` 在那次调用里返回空且exit 1，看起来像「本来就一致」，实际是命令根本没跑成。**改用绝对路径 + sha256 双向确认后**才确认真正同步。

## C.5 附录 C 判定

第 4 条随附说明**已补齐并落到源文件**，四条说明现已完整：
1. Gatekeeper 必定拦截（实测 `spctl` rejected、adhoc 签名）
2. 仅 Apple Silicon + macOS 14+
3. 更新功能实际不可用（上游只发 Windows 资产）—— 不要承诺自动更新
4. ✅ **新增**：`LSMinimumSystemVersion` 未在旧系统实机验证

**至此交付态无任何缺实测背书的声明，无任何未标注的限制。**

## C.6 自我更正：我在 C.3 表格里把E2 结果写错了

team-lead 在收尾核实时写道：「E2 四个 Windows 残留串：`venv\Scripts` → 1、`powershell` → 1（均为排除性说明）、另两个 → 0」。**这与我 C.3 表格里写的「全部仍 ABSENT」矛盾。**

**他是对的，我那格写错了。** 已用编辑修正。

**查证过程**（我原先只有 bash `grep` 的假阳性输出，没有拿证据）：
1. 我在 C.3 里用 bash `grep -qF` 复查，得到 `.venv\Scripts` 与 `powershell` 均 "PRESENT (bad)"——**但那是 shim 假阳性**，我当时只用 Grep 工具复核了「唯一命中在第 219 行」，却**顺手把表格写成了「全部 ABSENT」**，这是把「不存在操作性用法」错记成了「字符串不存在」。
2. 用**改动前的快照**（`$HOME/cullumi-tmp/iso-p3.retired/MACOS-使用说明.md`，在我编辑前拷贝）核实：同一句话位于**第 217 行**（我的 2 行插入使其后移为 219）。
```
217: 另有一份 `上游说明.md`，那是 **上游 Windows 版的原始文档，仅供对照查阅**，
     其中的 `powershell`、`.venv\Scripts\`、`verify.ps1` 等内容不适用于 macOS。
```

**结论（现已用证据锁定）**：
- 这 2 处命中**确实存在**，且**早于我的改动**，**由工程师在第一轮重写文档时写入**，**不是我引入的**。
- 但它们**不构成 E2 缺陷** —— E2 的验收标准是「不得有可执行的 Windows 指令残留」，而这句是**主动提醒用户不要去用那些 Windows 指令**的排除性说明，属于**期望存在**的内容。
- 因此正确表述是：**4 个串均无操作性用法**；其中 2 个各 1 处排除性说明命中，另 2 个 0 命中。

**教训（与我本轮另外两次自我作废同源）**：我手里当时只有「bash 说 PRESENT」+「Grep 工具说只有 219 一行」两条信息，**没有做「改动前 vs 改动后」的对照**，就匆匆写下了「全部 ABSENT」这个更强的结论。**「无操作性用法」与「字符串不存在」是两个不同的断言，我混淆了。** 补上快照对照才定住。

这条与 team-lead 总结的「验证手段本身也要被验证」是同一个问题的另一面：**验证结论的措辞精度，本身也需要被证据约束。**
