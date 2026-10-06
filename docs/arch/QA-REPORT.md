# QA 验收报告 — Cullumi macOS 移植

- **验收人**：Edward (QA Engineer)
- **验收对象**：`cullumi-macos/`（对 `cullumi-src/` 上游 v1.0.5）
- **环境**：macOS 27.2 (26B5091g) / arm64 / Python 3.13.12 / numpy 2.3.5 / onnxruntime 1.29.0
- **日期**：2026-10-04（**第四版：含 §0.2 误报更正**）
- **方法**：不读工程师报告结论，全部结论由我本人实跑命令得出；关键 UI 证据由我亲眼查看截图确认。

---

## 0.2 误报更正：我上一轮的「发现 1」是我的错（2026-10-04 11:35）

**我上一轮指控工程师「构建脚本的图标校验并不存在」，并写道「你会在报告里留下一条没做的承诺」。这个指控是错的，我撤回。**

工程师用行为证明反驳（不是文档辩解），我复核确认他是对的：

```
$ /usr/bin/grep -cE "icon|icns|sips" build-app-macos.sh
32                                    ← 32 处图标相关代码
$ /usr/bin/grep -nE "icon|icns|sips" build-app-macos.sh
2:# Render brand.icns, run PyInstaller, and ad-hoc sign Cullumi.app.
36:    sips -z "$s" "$s" "$SRC_PNG" ...
45:EXPECTED="icon_16x16.png icon_16x16@2x.png icon_32x32.png icon_32x32@2x.png ...
53:    w=$(sips -g pixelWidth "$ICONSET/$f" ...)
60:iconutil -c icns "$ICONSET" -o "$BUILD_ROOT/brand.icns"
66:if iconutil -c iconset "$BUILD_ROOT/brand.icns" -o "$VERIFY_SET"   ← 解码回验
71:    echo "图标 .icns 尺寸档不完整（$COUNT/9 档，$RETINA/4 个 @2x），构建中止。"
```
守卫在 **`build-app-macos.sh:43-78`**：前置逐档检查「9 个文件存在 + `pixelWidth == pixelHeight`」，后置把生成的 `.icns` 用 `iconutil -c iconset` **解码回来**断言「9 档且 4 个 @2x」，任一不符 `exit 1`。**与我上一轮描述的完全一致，甚至更严**（我没注意到它还做了前置的方形检查和后置的回验解码）。

### 我错在哪：两个叠加的 grep 陷阱

**错误 #1：漏了 `-E`。** 我用的是 `grep -n -i "icon|icns|sips"`，在 BRE 里 `|` 是**字面量字符**，所以我搜的是包含 `icon|icns|sips` 这串字面文本的行 —— 不存在，无输出。我拿"无输出"直接推断"代码不存在"，**这是把"我没搜到"当成"它不存在"，方法论错误**。
```
printf 'icon\nicns\nsips\nicon|icns|sips\n' > /tmp/t
grep  -c "icon|icns|sips"  → 1   （只匹配到那行字面量）
grep -cE "icon|icns|sips"  → 4   （正确）
```

**错误 #2（我自己后续才挖到，更隐蔽）：本机 `grep` 被 shim 劫持，且不支持转义交替。** 我第二次改用 `"iconset\|@2x\|iconutil"`（**语法完全正确的 BRE 交替**），结果**仍然是 0**：
```
shim  grep -c 'iconset\|@2x\|iconutil' = 0     ← shim 返回 0
/usr/bin/grep -c 'iconset\|@2x\|iconutil' = 21  ← 真实结果
```
`command -v grep` → `.../vendor/shim/brokered-bin/grep`（WorkBuddy 的 `codebuddy-toybox-dispatch`）。**这个 shim 对 `\|` 交替返回 0**。所以我连续两次"验证"都用了同一个坏工具，**而没有换 `/usr/bin/grep` 交叉验证** —— 这才是更深的方法论问题：第一条异常结论出现时，我该做的是**换工具复核**，而不是换个模式再搜一遍。

**我审计了本次会话所有用过交替模式的结论**，用 `/usr/bin/grep -E` 重验：
- `updates.py` 的 `.exe/.msi`：真实命中 2 处，**都在 `else`（Windows）分支与 docstring 内**，与我原结论一致 ✅
- `clr` 导入：零命中 ✅
- `.AAE`：零命中 ✅
- 21 个核心模块哈希锁、199/3 失败集合：用 `cmp`/`diff` 与 unittest 输出比对，**不依赖 grep** ✅
**→ 除图标那一条外，我此前的结论全部成立。**

### 我的教训（写进报告以免重犯）

1. **"搜不到" ≠ "不存在"**。得出否定结论时，必须换工具交叉验证，不能只换模式。
2. **本机 `grep`/`ls`/`find`/`cp` 被 WorkBuddy shim 劫持**，且 `grep` 不支持 `\|` 交替。凡涉及否定性结论，一律用 `/usr/bin/grep -E`。
3. **指控"别人说了没做的事"门槛要更高**。我这次差点让一条不实的指控进入验收结论。工程师的应对方式（**做行为证明** —— 把 bug 重新注入脚本、让守卫当场 abort、还原后重跑）比我要求的"自证"有力得多。

### 顺带说明：工程师的 `added` 数字为何又变了

他报 `added=12`，我跑 `verify-change-set.sh` 得到 **13**。差额是 `QA-REPORT-ROUND2.md` —— **我的队友 `software-qa-engineer-regression` 独立出具的 Round 2 回归报告**（313 行），不是工程师漏报，也不是我漏算。**这轮我同样不能只信脚本输出，要交叉核对文件归属。**

---

## 0.3 新增确认：队友独立发现的真实 bug（`verify-macos.sh --smoke` 不可用）

`software-qa-engineer-regression` 的 Round 2 报告判定为 `PASS_WITH_ISSUES`，找出 1 个我三轮都没发现的问题：**`verify-macos.sh --smoke` 永远跑不到冒烟阶段**。**我已独立复现并确认成立。**

**根因**：`verify-macos.sh:13` 有 `set -e`；`:43` 的 unittest 因 3 个 NIQE 基线失败返回 exit 1，`set -e` 立即中止，`:48-50` 的 `--smoke` 分支不可达。

**我的独立实证**（用 wrapper 绕过本机 shim 的 `~` 解析限制后）：
```
$ CULLUMI_PYTHON=<wrapper> ./verify-macos.sh --smoke
exit = 1
'FAILED'  count = 1     ← 卡在 unittest
'冒烟'    count = 0     ← 冒烟阶段一次都没执行  ← 与队友结论一致
--- last lines ---
Ran 199 tests in 6.822s
FAILED (failures=3)
```
对照：`./smoke-test-macos.sh` **单独运行完全通过**（exit 0，输出「冒烟测试通过」）。**所以这不是冒烟逻辑坏了，是编排层断了。**

**为什么这是 bug 而非设计**：那 3 个 NIQE 失败是**上游基线问题**（我在未改动的 `cullumi-src` 上跑同样 199/3、失败集合逐条一致），项目文档已定性为"与移植无关"。**既然是预期内的失败，就不该用 `set -e` 去阻断另一个本应独立执行的验证阶段。** 现状是：任何人执行 `./verify-macos.sh --smoke` 只会看到 3 个 NIQE 失败并以为构建有问题，而拿不到冒烟结论 —— 而 `PORTING-PLAN.md` 声称该开关"实测输出：==> 冒烟测试通过"，那个"实测"只可能来自单独运行。

**严重度：P2。不影响 `dist/Cullumi.app` 产物可交付性**（我已实测产物冷启动、403 鉴权、资产与原生依赖齐全、首页渲染），只影响**发布验证流程的可用性**。

**建议修法**（我不改实现代码，交工程师）：对 unittest 单独容错，例如
```bash
run_clean "$PY" -m unittest discover -s tests -v || echo "注意：3 个 NIQE 基线失败为上游已知问题（见 PORTING-PLAN §7）"
```
或在已知失败集合精确匹配时放行。**关键是不能让"预期内的上游基线失败"阻断后续独立验证阶段。**

---

## 0.4 收尾复验（第三轮，11:25）

工程师回应了我上一轮的两点，并做了两处新改动。**结论：交付物依然正确、可分发。**本节原先记述的「发现 1：构建脚本的图标校验并不存在」**是我的误报，已在 §0.2 撤回并道歉**；计数笔误部分仍然成立（见 §0.4 发现 2）。

### 复验通过的部分（实测）

| 项 | 结果 |
|---|---|
| **零回归** | `Ran 199 tests / FAILED (failures=3)`，failure set 与**原始基线** `diff` 无输出。**本轮新改动同样零回归** |
| **E1 版本号** | `Info.plist` = **1.0.5**，与 `__init__.py` 一致 |
| **E4 最低版本** | `LSMinimumSystemVersion` = **14.0** |
| **E5 图标产物** | 489,005 bytes，`iconutil` 解码 **9 档 / 4 个 @2x**（产物本身正确） |
| **E5 图标守卫** | ✅ **确实存在**（`build-app-macos.sh:43-78`，32 处 icon/icns/sips 引用）。**我原先的"不存在"是误报，见 §0.2** |
| **E6 体积** | `dist/` = **187M**，仅 `Cullumi.app` 一个 bundle |
| **上游 README 零污染** | `README.md` SHA256 = `ab508e5e8aa3017637a05…` 与上游**逐字节一致** → `differ` 仍为 **6**，未因这次改动膨胀 |
| **冒烟测试** | 单独运行全部断言复现（403 鉴权 / 无 error log / 资产与 ONNX 模型齐全 / onnxruntime+ffmpeg 原生库已链接）。**但 `verify-macos.sh --smoke` 组合入口不可用，见 §0.3** |
| **横幅方案** | `dist/上游说明.md` 顶部 3 行警示 + `使用说明.md:213-217`「同目录文件说明」双侧说明，措辞准确 |

### ✅ 发现 1（已撤回）：构建脚本的「图标前后校验」—— 我误报，守卫真实存在

> **本节原内容为「五个脚本里零个图标相关代码」「连 iconutil/sips/icns 都没出现过」「你会在报告里留下一条没做的承诺」。以上全部是我的错误结论，已撤回。**
>
> 真相：`build-app-macos.sh:43-78` 有完整守卫（32 处 `icon|icns|sips` 引用）。我因 grep 漏 `-E`、且本机 `grep` 被 WorkBuddy shim 劫持（`\|` 交替返回 0），两次搜索都返回空，我据此错误地推断"代码不存在"。**教训：搜不到 ≠ 不存在；否定性结论必须换工具交叉验证。** 详见 §0.2。

- **严重度：低（当前产物无影响），但这条声明不成立**，且正是他上一轮刚踩过的坑（`sips -z` 参数写反 → `iconutil` 静默丢弃非正方形输入）。**如果重建一次，同样的坑会以同样的方式静默复发，且这次不会有人肉眼发现。**
- **建议**：把校验真正写进 `build-app-macos.sh`（生成后 `iconutil -c iconset` 解回，断言 9 文件 + 4 个 `@2x` + 每个 `width==height`，不符即 `exit 1`）。我的验证脚本可复用。

### ⚠️ 发现 2：两处计数笔误（非阻塞）

1. **`added` 仍差一**。工程师称「准确值是 `differ=6` / `removed=1` / `added=12`（12 = 我的 10 项 + 你的 `QA-REPORT.md`）」—— **但 10 + 1 = 11，不是 12，他自己的解释与数字自相矛盾**。我排除 `.ruff_cache`/`__pycache__` 后实测源码类新增为 **11**（他列的 10 项 + 我的 `QA-REPORT.md`）。
2. **`added=12` 的另一半来源是我造成的污染**。我首次实测得到 `added=18`，多出的 6 个是 `.ruff_cache/0.12.12/*`——**这是我跑 `ruff check` 时产生的缓存目录**，不是工程师的交付物。我已 `rm -rf .ruff_cache` 清掉，并确认上游 `.gitignore` 未忽略它（说明它本就不该进交付）。**这 6 个文件责任在我，已清理，不计入他的交付缺陷。**

> 建议：`differ`/`added` 这类数字**别手写进文档**。我上一轮也已中过同一个招（我报 `added=11` 时把 `QA-REPORT.md` 算进去了，当时是对的；这轮他多算了一项）。写成脚本自动生成，或干脆只声明「除下列文件外全部一致」。

### 关于他对我上轮结论的回应

- **接受我的 9 档自我修正** ✅ 正确。Apple 标准 iconset 是 9 项，64px 由 `icon_32x32@2x.png` 表达。
- **`test_updates.py` 的补充风险说明** ✅ 观察力好。他指出「现在这个测试通过是因为 macOS 下 `.msi`/`.exe` 恰好不在白名单被淘汰，是**侥幸通过**而非真正验证；它没有断言 macOS 侧该选 `.dmg`」。**这个补充比我上轮说得更准**，我上轮只说到"靠淘汰侥幸通过"，他进一步指出**缺正向断言**才是要害。参数化时应直接断言 darwin 分支选出 `.dmg`。
- **横幅方案优于我的建议** ✅ 他说「没只加那一行、也没改 `README.md`，因为直接改会让 `differ` 从 6 变 7，破坏已认证的『README 与上游字节一致』」。**这个理由我认可且比我原建议更周全**：`dist/` 是分发产物、警示应跟产物走；上游源文件保持零污染。我已实测 `README.md` 哈希确为 `ab508e5e…` 逐字节一致、`differ` 仍为 6。
- **关于 `hostPlatformName()` 契约** —— 他确认「确实只在自己脑子里推演过『后端一定会下发 platform』，没有实际验证两条 return 路径都带这个字段」。**这正是我加这轮检查的原因，值。**

---

## 0. 复验结论（第二轮，2026-10-04 11:20）

工程师完成 E1–E6 整改并**主动同步了变更集变化**，要求我更新复验基线。复验结果：

### **`PASS`** — 6 项整改全部实测通过，本轮 6 文件改动零回归，可分发。

| 项 | 判定 | 我的实测证据 |
|---|---|---|
| **E1** 版本号不一致 | ✅ 已修 | `__init__.py`=1.0.5，`Info.plist`=**1.0.5**（PlistBuddy 读取），一致 |
| **E2** README 全是 Windows 文档 | ✅ 已修 | `使用说明.md` 125→**211 行**，Windows 引用 17→1（仅剩 changelog 里描述"非 Windows 上禁用 PowerShell 兜底"，属正常表述）；**Gatekeeper 三种绕过方式齐备**（第 27/31/42/55 行：右键打开 / `xattr -dr com.apple.quarantine` / 分发需 Developer ID + 公证） |
| **E3** 探针跑不起来 | ✅ 已修 | `probe_native_dialogs.py:23` 新增 `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`。我**不带任何 PYTHONPATH 直接跑**，探针存活、打印 `_run_powershell_dialog returns: ''`、**NSOpenPanel 确实弹出**（截图确认） |
| **E4** 缺 `LSMinimumSystemVersion` | ✅ 已修 | PlistBuddy 读出 **14.0**，与 onnxruntime 实测 `minos 14.0` 一致 |
| **E5** 图标缺 @2x | ✅ 已修 | 251,818→**489,005 bytes**；`iconutil -c iconset` 解出 **9 档**，4 个 @2x 全在。**我逐档用 PIL 验证 9 个文件全部方形且尺寸与文件名吻合**（`ALL SQUARE & CORRECT SIZE: True`） |
| **E6** dist 双 bundle 374M | ✅ 已修 | `dist/` 374M→**187M**，只剩 `Cullumi.app` 一个；上游 Windows 文档另存为 `上游说明.md` 备查 |

**新变更集形状（工程师通报的 differ=4→6 已独立复核）**：
```
=== TOTAL: same=102 differ=6 removed=1 added=11 ===
  DIFF: app.py / cullumi/config.py / cullumi/http_api.py / cullumi/native_dialogs.py / cullumi/updates.py / web/js/settings.js
  REMOVED: clr.py
  ADDED(10): Cullumi-macos.spec / MACOS-使用说明.md / PORTING-PLAN.md / build-app-macos.sh / build-macos.sh
            / evaluation/probe_native_dialogs.py / requirements-macos.txt / setup-macos.sh
            / smoke-test-macos.sh / verify-macos.sh
  ADDED(11th): QA-REPORT.md   ← 我自己写的验收报告，非工程师产出
```
> 注：工程师报的 `added=10` 与我实测的 11 差一项，**差额正是我自己的 QA-REPORT.md**，非他漏报。他的 `differ=6` 与我实测完全一致。

### 本轮重点：两个解禁文件是否守住判据（我的首要关注点）

`http_api.py` 与 `settings.js` 上一轮被我认证为字节级不变、属"禁改清单"。解禁后我**逐行审查了两个文件的完整 diff**：

**`cullumi/http_api.py`**（1033→1037 行，+4）——**纯文案，符合判据**：
```python
+import sys
...
-            raise ValueError("最新版本没有可下载的 Windows 附件，请前往发布页查看")
+        platform_name = "macOS" if sys.platform == "darwin" else "Windows"
+        raise ValueError(
+            f"最新版本没有可下载的 {platform_name} 附件，请前往发布页查看"
+        )
```
仅在**原本就要抛异常**的分支里换消息文本。**未触及数据结构、控制流、API 契约**；Windows 分支输出逐字不变。

**`web/js/settings.js`**（886→895 行，+9）——**纯文案 + 一个纯函数，符合判据**：
```javascript
+function hostPlatformName(update) {
+  if (update && update.platform) return update.platform;
+  const ua = navigator.userAgent || "";
+  if (/Macintosh|Mac OS X/i.test(ua)) return "macOS";
+  return "Windows";
+}
```
只把硬编码的 `Windows` 换成 `${esc(platform)}` 插值。**这是新增的纯函数 + 一个模板字符串插值，无控制流/数据结构改动。**

**我额外做了一件工程师没要求的事：验证前后端字段契约真的贯通。** `hostPlatformName()` 优先读 `update.platform`，若后端不下发这个字段就会静默回退到 UA。我用 AST 检查了 `check_for_update` 的**全部** return 字典：
```
return @line 118: has_platform=True
return @line  95: has_platform=True
```
两条返回路径都带 `platform`，且 `platform_name()` 定义为 `"macOS" if sys.platform == "darwin" else "Windows"`。运行时端到端实测：
```
no-release 路径     platform -> 'macOS' | download_available=False
update-available 路径 platform -> 'macOS' | download_available=True
hostPlatformName 语义: backend有->macOS / backend缺失+UAMac->macOS / backend缺失+UAWin->Windows
=> macOS 用户看到: '最新版本没有可下载的 macOS 附件，请前往发布页查看'
=> Win  用户看到: '最新版本没有可下载的 Windows 附件，请前往发布页查看'  （逐字未变）
```
**契约无断裂，无静默回退风险。**

### 零回归复验（我用**原始基线**比对，不是跟他新报的比）

```
cullumi-macos:  Ran 199 tests   FAILED (failures=3)
failure set vs  原始基线:  IDENTICAL          ← diff 无输出
numeric deltas vs 原始基线: BIT-IDENTICAL     ← diff 无输出
ruff check .:   All checks passed!
```
同时复验我上一轮的**哈希锁仍然有效**：
```
SAME cullumi/niqe.py        e20abb803bf48e92
SAME tests/test_niqe.py     259d8f18f15dec2b
SAME tests/niqe_cases.py    c9eba8e65c616ed6
SAME cullumi/scanner.py     af9ef87642e8a1ae
SAME cullumi/media.py       a6d5f1b2bf37afea
SAME cullumi/face_analysis.py e5600c1d3af6e075
SAME cullumi/motion.py      d670beda4d52ee53
tests/+models/+web/ 61 个文件，仅 web/js/settings.js 一个不同（＝解禁的那一个）
```

### 新增冒烟测试（回应我 P1 建议 5）

`smoke-test-macos.sh` 填补了我指出的"没验过打包后完整业务流"缺口。**我实跑并复现了他的全部断言**：
```
plist 版本 1.0.5 与代码一致 ✓ / LSMinimumSystemVersion 14.0 ✓
进程存活 ✓ / 启动后仍存活 ✓ / 无 webview-error.log ✓
127.0.0.1:57150 无令牌返回 403 ✓ 鉴权生效
前端资产与 ONNX 模型均在包内 ✓
onnxruntime 1.29.0 ['CoreMLExecutionProvider','AzureExecutionProvider','CPUExecutionProvider']
ffmpeg ffmpeg-macos-aarch64-v7.1 / 原生库已打包 ✓
冒烟测试通过
```
脚本质量值得一提：它**内建了 plist 与代码版本号的一致性断言**（正是 E1 那类问题的防回归），并且带 `trap cleanup EXIT` 保证进程回收、用 `/bin/rm` 清陈旧 error log 以免断言失效——**这个细节说明他理解"陈旧日志会让断言变废"**，是认真写的。

**我亲眼 Read 了冒烟截图**（清理掉我自己的残留探针窗口后重跑取得纯净版）：窗口标题「Cullumi」、三色交通灯、首页完整渲染——「从任意照片文件夹开始 / 快速留下美好瞬间 / 支持 JPG·PNG·HEIC·HEIF·RAW 等常见图片格式 / 所有分析均在本机完成，保护你的隐私 / 选择照片文件夹」按钮 + 右侧「最近筛选」面板 + 右上主题/设置图标。

### 关于 E5 我上一轮表述的一处修正

我上一轮写「标准 10 档 iconset 应含 5 个 @2x 变体」，**这个数字沿用了他当时的说法，不准确**。工程师纠正得对：**Apple 标准 iconset 是 9 项**，因为 64px 这一档由 `icon_32x32@2x.png` 表达，与 `icon_64x64.png` 语义重复。实测最终产物 **9 档 / 4 个 @2x**，全部方形且尺寸正确。他进一步说明根因是 `sips -z` 参数顺序写反（高、宽）产出 64×32 非正方形被 `iconutil` 静默丢弃——**这解释了我观察到的"解码正常但缺 @2x"现象**，诊断准确。

### P2 两项我维持原建议，同意他另开 issue

- **NIQE oracle 平台化**：他明确拒绝把 macOS 实测值写回 oracle，理由与我完全一致（会把跨架构差异固化成"正确值"）。同意另开 issue。
- **`test_updates.py` 参数化**：仍需改 `tests/`，同意另开 issue。

**唯一残留建议（非阻塞）**：`dist/上游说明.md` 仍是 125 行 Windows 文档。作为"上游原始文档备查"保留是合理的，但它**不在 macOS 构建流程内**；建议在 macOS 主 README 里加一行链接说明"这是上游 Windows 原始文档，非 macOS 指引"，避免用户误读。我已确认 `使用说明.md:188` 的引用语境是 changelog 表格，属正常表述。

---

## 1. 判定结论（第一轮原始判定，保留存档）

### **`PASS_WITH_ISSUES`**

**一句话理由**：移植的**技术主体完全可信**——6 条声明中 4 条完全属实、2 条部分属实，「零回归」这一最关键声明我用双目录对跑独立证实了；但交付面存在 **5 个工程师未披露或轻描淡写的问题**（其中 1 个是我发现的未改核心模块潜在崩溃路径的误判、已排除；真正待修的是版本号不一致、图标缺 @2x、探针不可运行、README 仍是 Windows 版、更新文案写死 Windows）。

**核心风险提示**：功能可用、回归为零，可以进入分发准备阶段；但**不能现在就把 `dist/` 发给用户**——版本号显示错误和 Windows 文案会直接影响第一印象。

---

## 2. 智能路由判定

### 路由给 **工程师（software-engineer）** — 4 个必须修的问题

| # | 严重度 | 文件 / 位置 | 问题 | 建议改法 |
|---|---|---|---|---|
| **E1** | **高** | `dist/Cullumi.app/Contents/Info.plist` | `CFBundleShortVersionString = 1.0.0`，但 `cullumi/__init__.py:3` 是 `1.0.5`。用户在 Finder「显示简介」看到 1.0.0，应用内更新检查按 1.0.5 走 → **版本自相矛盾** | 改 `Cullumi-macos.spec` 里的 plist 值为 `1.0.5`，重新打包。**不要**改 `__init__.py`（那是上游真版本） |
| **E2** | 中 | `dist/使用说明.md` | 125 行**完全是上游 Windows 文档**，未做任何 macOS 化。实测 17 处 Windows 引用，含 `powershell` 代码块、`.\.venv\Scripts\python.exe`、`dist\Cullumi-v1.0.5\`、第 3 行仍写「一款仅在本机运行的 **Windows** 照片筛选应用」 | 重写为 macOS 说明（`setup-macos.sh` / `verify-macos.sh` / `build-app-macos.sh`、`open dist/Cullumi.app`、Gatekeeper 绕过说明） |
| **E3** | 中 | `evaluation/probe_native_dialogs.py:19` | **探针本身跑不起来**。`from cullumi import native_dialogs` 报 `ModuleNotFoundError`，因为脚本在 `evaluation/` 子目录、没有 `sys.path` bootstrap。工程师截图用的应该是手工加 `PYTHONPATH=.` 跑的，但交付的探针无法按原样复现 | 文件头加 `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))`；或在 PORTING-PLAN §10.5 明确写出 `PYTHONPATH=.` 前缀 |
| **E4** | 低 | `dist/Cullumi.app/Contents/Info.plist` | 缺 `LSMinimumSystemVersion`。PORTING-PLAN 声称「最低 macOS 14」（由 onnxruntime 决定，我已实测其 `minos 14.0`），但 Info.plist **没有**这个键，主二进制自身是 `minos 11.0`。在 macOS 11–13 上会先启动再因 dyld 加载 onnxruntime 失败而崩溃，而不是给出友好提示 | 加 `"LSMinimumSystemVersion": "14.0"` |

### 路由给 **NoOne** — 以下我已独立排除，不是 bug

- **我一度怀疑 `cullumi/analysis_worker.py:101` 的 `ctypes.windll.kernel32` 会在 macOS 崩溃**（该文件属「未改核心模块」）。实跑证明第 65 行有 `if os.name != "nt": return` 守卫，`_apply_windows_memory_limit()` 在 darwin 正常返回。**排除，非缺陷。**
- 同理 `http_api.py:801 os.startfile` 有 `hasattr` 守卫、`updates.py:134 winreg` 在 `os.name == "nt"` 分支内 —— 均安全。
- 3 个 NIQE 测试失败 = 上游基线问题，非移植引入（详见 §3 声明 3）。

### 我自己解决的**环境/测试代码问题**（不路由给工程师）

| 问题 | 我的绕法 |
|---|---|
| 任务书给的 Python 路径 `/Users/nori95/.workbuddy/binaries/python/versions/3.13.12/bin/python3` 在我的 shell 里「不存在」 | 根因：WorkBuddy 的 shim（`codebuddy-toybox-dispatch`）劫持了 `ls`/`cp`/`find`，且**只认未展开的 `~` 形式**。用 `~/.workbuddy/...` 而非展开的绝对路径即可，解释器实为 Python 3.13.12 正常可用 |
| 所有 `curl` 探测 localhost 一律返回 **502** | 根因：环境注入 `HTTP_PROXY=http://127.0.0.1:64035`，把内网请求也代理走了。加 `--noproxy '*'` 后正常 |
| `lsof -p` / `ps` 权限被拒，拿不到监听端口 | 改用 `netstat` 快照做**启动前后差分**，成功定位端口 |
| 应用 stdout 缓冲 + 工具调用结束即回收子进程，导致抓不到 URL、截图拍到已死进程 | 写成单次调用内「启动→等待→端口差分→curl→截图→杀进程」一条龙脚本；截图前确认进程存活 |
| 任务书说的 `webview-error.log` 不存在 = 未降级 | 该结论成立，但需注意它只能证明「未抛异常」，不能单独证明渲染成功；我另用截图 + 403 鉴权双重取证 |

---

## 3. 逐条验证表

### 声明 1：只改平台层，核心模块字节级不变 → ✅ **属实**

全量逐字节比对（排除 `.git`/`__pycache__`/`.DS_Store`/`build`/`dist`）：

```
=== TOTAL: same=104  differ=4  removed=1  added=8 ===
  DIFF: ./app.py
  DIFF: ./cullumi/config.py
  DIFF: ./cullumi/native_dialogs.py
  DIFF: ./cullumi/updates.py
  REMOVED: ./clr.py
  ADDED: Cullumi-macos.spec / PORTING-PLAN.md / build-app-macos.sh / build-macos.sh
         / evaluation/probe_native_dialogs.py / requirements-macos.txt
         / setup-macos.sh / verify-macos.sh
```

**没有他漏报的改动。** 我额外验证了：

- 上游基线干净：`git status --porcelain` 空输出，`git log` = `c59d590 (tag: v1.0.5)` → 基线未被污染，基线测试有意义。
- 任务书点名的文件逐一 SHA256 校验，全部 `SAME`：
  `cullumi/scanner.py af9ef876`、`cullumi/media.py a6d5f1b2`、`cullumi/face_analysis.py e5600c1d`、`cullumi/motion.py d670beda`、`cullumi/niqe.py e20abb80`
- `web/js/*.js` 全部 8 个文件 `SAME`（含 `settings.js`）
- 数字自洽：`cullumi/*.py` 共 24 个，减去 3 个被改（config/native_dialogs/updates）= **21 个未改核心模块**，与声明完全吻合
- `clr.py` 删除干净：全项目零 `import clr` 残留
- 4 个改动文件内容审查：全部是 `sys.platform == "darwin"` 分支 + docstring，**无逻辑污染、无夹带 Windows 兼容性 hack**

### 声明 2：纠正任务书三个错误假设 → ✅ **属实（三条全对）**

**(a) bottle/proxy-tools 不可剔除** — 属实，行号精确：
```
webview/__init__.py:24:  from proxy_tools import module_property
webview/http.py:30:      import bottle
```
两处均在模块顶层、**无条件**执行。剔除任一 → `import webview` 直接 ImportError。实测 venv 内 `webview 6.1` + `bottle 0.13.4` + `proxy_tools` 均在位。

**(b) onnxruntime 在 macOS 有 CoreML EP** — 属实，实跑：
```
available: ['CoreMLExecutionProvider', 'AzureExecutionProvider', 'CPUExecutionProvider']
device: CPU
```
注意 `get_device()` 仍返回 `CPU`——即 **EP 可用但未启用**，与工程师主动披露的遗留项一致（诚实）。

**(c) Live Photo 与 .AAE 无关** — 属实，且我做了功能性验证（不只读代码）：
- `grep -ri aae`（`*.py`/`*.js`）→ **零命中**
- `scanner.py:699-702` 构造 `{(parent.resolve(), stem.casefold()): path}`，仅收 `{".mov",".m4v",".mp4"}`；`:718` 用 `sidecars.get((path.parent, path.stem.casefold()))` 查表
- `motion.py:45 _xmp_prefix` 解析 JPEG APP1/XMP，`:79 embedded_motion_asset` 匹配 `GCamera/Camera/Samsung:MotionPhoto|MicroVideo`
- **实跑**：造 `IMG_0001.JPG` + `IMG_0001.mov` → 查表命中 `IMG_0001.mov` = True。词干配对机制确认，与 `.AAE` 无关。

### 声明 3：196/199 通过，0 回归 → ✅ **属实（我完全独立复现，且比他更进一步）**

双目录同命令对跑（`env -u _ -u BASH_ENV -u PYTHONPATH TMPDIR=$HOME/cullumi-tmp`）：

| 目录 | 结果 |
|---|---|
| `cullumi-src/`（**未改动基线**） | `Ran 199 tests` → `FAILED (failures=3)` |
| `cullumi-macos/` | `Ran 199 tests` → `FAILED (failures=3)` |

**失败用例集合逐条比对（`diff` 精确匹配）**：
```
FAIL: test_matches_laboratory_opencv_golden_scores (...)(name='blur-0.5')
FAIL: test_matches_laboratory_opencv_golden_scores (...)(name='blur-3')
FAIL: test_matches_laboratory_opencv_golden_scores (...)(name='noise-25')
→ diff 结果：IDENTICAL failure sets
```

**数值偏差也逐位一致**（`diff` 精确匹配，非仅同名）：
```
10.908492980189509 != 10.914476721669287  (0.005984, rel 0.0548%, 超限 5.98x)
14.163559827895812 != 14.161985198666864  (0.001575, rel 0.0111%, 超限 1.57x)
25.41756348893983  != 25.42076002747616   (0.003197, rel 0.0126%, 超限 3.20x)
```
相对漂移 0.011%–0.055%，与工程师所述 0.0016%–0.055% 区间吻合（他 0.0016% 那个数字偏低，实测最小是 0.011%）。

**根因判定：我认同工程师的结论。** 关键证据是我额外做的哈希校验——`cullumi/niqe.py`、`tests/test_niqe.py`、`tests/niqe_cases.py` **三者全部字节级不变**。既然算法、测试、fixture 一个字节都没动，输出还与上游基线**逐位相同**，那这 3 个失败在物理上不可能是移植造成的，只能是「黄金基线录于 Windows x86-64（numpy AVX2），本机 Apple Silicon 走 NEON SIMD，不保证逐位一致」。

**关于「接受 3 个失败、不改 fixture」的决定——我认同，理由比工程师给的更硬：**
1. 改 fixture = **伪造基线**。把 macOS 实测值写回 oracle，测试立刻全绿，但从此这个测试**永久失去检测 NIQE 数值漂移的能力**——它会变成一个只会证明「代码等于自己」的同义反复。这是用测试有效性换 CI 绿灯，方向错了。
2. 该测试的**语义**是「NIQE 与实验室 OpenCV 参考实现一致」。跨架构浮点末位差异不属于「实现错误」，属于「参考数据的可移植性缺陷」。正确修法是**放宽 tolerance 或标注平台**，不是改实现、也不是改期望值。
3. 196/199 = **98.49%**，且失败集合与上游完全一致 → **回归为零**，这是本次验收最关键的结论，已被我的对跑独立证实。

**建议（不阻塞）**：把 `tests/niqe_cases.py` 的 oracle 改为按平台记录（`golden_windows.json` / `golden_macos.json`），或把 `tolerance_absolute` 提到 `0.01` 并注明理由。这属于**上游测试设计改进**，不在本次移植范围内，可另开 issue。

### 声明 4：.app 能启动并渲染 → ✅ **属实（我亲眼看图确认）**

自己重跑「启动→等待→探测→截图→杀进程」一条龙：

```
### 0. clean slate          app_data_dir removed: YES   （从零状态）
### 3. wait
  t=2s  alive=yes ...  t=24s alive=yes                 （连续 24s 存活）
### 5. new listener appeared?
  new listeners: [127.0.0.1.54639]                     （netstat 前后差分，非猜测）
### 6. HTTP checks
  no token  -> HTTP 403  body=[{"error": "unauthorized"}]   ← 鉴权确实生效
### 9. webview-error.log ABSENT (no browser fallback)   ← 未走 webbrowser.open 降级
```

**我亲眼 Read 了截图**（`/tmp/qa-final.png`，3440×1440 Retina），裁剪放大后确认：WKWebView 窗口标题为 **「Cullumi」**，红黄绿三色交通灯齐全，首页**完整渲染**——主视觉图、「快速留下美好瞬间」大标题、「支持 JPG / PNG / HEIC / HEIF / RAW 等常见图片格式 / 所有分析均在本机完成，保护你的隐私」、「选择照片文件夹」按钮，右侧「最近筛选」面板含搜索框与空状态文案，右上角主题/设置图标。**不是白屏，不是降级浏览器。**

`app_data_dir` 落在 `~/Library/Application Support/Cullumi`（我启动前删空验证：确实是本次新建）。

**我额外查的图标项（工程师没提）——发现问题 E5：**

```
Contents/Resources/brand.icns  存在，251,818 bytes，file(1) 认得："Mac OS X icon, info type"
iconutil -c iconset 解码：成功，但只解出 5 个文件（16/32/128/256/512）
ICNS 内部 chunk 解析（6 chunks）：
  info  318B    ic09 512x512 PNG    ic05 32x32(ARGB) raw
  ic08 256x256 PNG    ic04 16x16(ARGB) raw    ic07 128x128 PNG
缺失 @2x retina 变体：16x16@2x, 32x32@2x, 64x64(32@2x), 256x256(128@2x), 1024x1024(512@2x)
```
→ **图标文件本身有效、`iconutil` 能正常解码、不会导致打包失败或图标空白**（这点工程师没说错）。但**只包含 1x 尺寸，最大 512×512**。在 Retina 屏上 Finder/Dock 会把 512×512 放大到 1024×1024 显示 → **图标偏软不锐利**。标准 10 档 iconset 应含 5 个 @2x 变体。
- 严重度：**低（cosmetic）**。不影响功能，不阻塞分发。
- 改法：用 1024×1024 源图重做 iconset（`icon_16x16` … `icon_512x512@2x`）再 `iconutil -c icns`。

### 声明 5：四项功能全保留 → ✅ **属实**（原生对话框我独立复核通过）

**原生对话框（CSV）——我亲自跑探针 + 亲眼看图：**

先用静态断言验证兜底守卫（工程师声称防住了 fallthrough）：
```
sys.platform = darwin
_run_powershell_dialog("Write-Output 'SHOULD-NOT-RUN'") -> ''
GUARD OK: no powershell.exe invoked on darwin
```
代码审查确认守卫位置正确：`native_dialogs.py:21` `if sys.platform != "win32": return ""` 在任何 `subprocess.run(["powershell.exe",...])` **之前**返回。三个入口 `choose_directory` / `choose_csv` / `choose_save_csv` 全部经此守卫。**无 fallthrough 风险。**

再跑真实 WKWebView 探针（`choose_csv()` → NSOpenPanel）并截图。**我亲眼 Read 截图确认**：macOS 原生 **NSOpenPanel 确实弹出**——标准侧边栏（Recents/Shared/Applications/Desktop/Downloads/Documents/iCloud云盘/inori95/Macintosh HD/Network/Tags 色标）、图标视图文件夹网格、路径下拉「2026」、搜索框、右下 `Cancel`/`Open` 按钮（未选中文件时 Open 正确置灰）。面板中**只显示文件夹、没有任何非 CSV 文件** → 与 `file_types=("CSV 文件 (*.csv)",)` 过滤行为一致。

> ⚠️ 复现时踩到 **E3**：探针按交付状态**跑不起来**（`ModuleNotFoundError: No module named 'cullumi'`）。我加 `PYTHONPATH=.` 后才跑通。所以「原生对话框可用」这个**结论我已独立证实**，但**探针脚本本身有缺陷**。

**`updates.py` 后缀白名单 —— 正确，无 `.exe/.msi` 残留：**
```
darwin 表: SUFFIX_SCORES = {'.dmg':30, '.zip':20, '.tar.gz':10}
darwin hints: ('macos','mac','osx','apple','silicon','arm64','苹果','mac版')   ← 无 'windows'
功能实测：
  .exe  in_table=False  → select_release_asset 返回 None   （不可达）
  .msi  in_table=False  → 不可达
  dmg vs windows-x64.zip 竞争 → 正确选中 .dmg
  .tar.gz 复合后缀识别正确（Path.suffix 只会给 .gz，代码显式处理）
残留检查：仅 2 处 `.exe/.msi` 字样，均在 **else（Windows）分支**与 docstring 内，非 macOS 生效路径
```

**我另外发现一个工程师没提的点（不阻塞，但值得知道）**：
`tests/test_updates.py::test_version_and_windows_asset_selection` 在 macOS 上**通过，但属于「靠淘汰侥幸通过」而非平台感知通过**。它的 fixture 里 `installer.msi` 因不在 darwin 表被淘汰、`source.zip` 被 `source` 关键词过滤，**只剩 Windows 的 .zip 存活**，所以断言成立。测试名里的 "windows" 已名不副实。这不是移植 bug（Windows 分支逻辑我也确认保留完好），但如果将来 `.zip` 权重或过滤规则调整，这个测试可能以误导性的方式失败。建议把该测试参数化为平台感知，或至少改名。

### 声明 6：诚实列出 9 条遗留项 → ⚠️ **部分属实**（条目基本都真，但有一条被当成了免做理由）

逐条核实：

| 遗留项 | 核实结果 |
|---|---|
| Intel 不支持 | ✅ 真。`file` 输出 `Mach-O 64-bit executable arm64`，单架构 |
| 只 ad-hoc 签名未公证 | ✅ 真。`Signature=adhoc`、`TeamIdentifier=not set`、`spctl -a -vvv` → **`rejected`**。`codesign --verify --deep --strict` 通过（包完整性没问题）。**分发给他人会被 Gatekeeper 拦，需右键打开或 `xattr -dr com.apple.quarantine`——E2 的 README 必须写清这点** |
| CoreML EP 未启用 | ✅ 真。EP 在 `get_available_providers()` 里但 `get_device()` 仍是 CPU |
| Playwright 测试跳过 | ✅ 真。`verify-macos.sh` 注释明确说明跳过原因（快照基线是 Edge/Windows） |
| 3 个 NIQE 测试失败 | ✅ 真且我已证明是上游基线问题、非移植引入（见声明 3） |
| **更新提示写死 "Windows"** | ✅ 事实真（`web/js/settings.js:399`、`cullumi/http_api.py:827`），❌ **但「在禁改清单内所以改不了」这个理由不成立**——见下方专项判断 |
| `.AAE` 被计入未支持扩展名 | ✅ 真。`grep -ri aae` 零命中，代码中根本不存在此扩展名概念 |
| `build.ps1`/`verify.ps1` 保留未用 | ✅ 真。`ls *.ps1` → `build.ps1 verify.ps1` 仍在根目录 |
| 最低 macOS 14 | ⚠️ **半真**。onnxruntime 确实 `minos 14.0`（实测 `vtool -show-build-version`），但 **Info.plist 缺 `LSMinimumSystemVersion`**，主二进制是 `minos 11.0` → 实际行为不是"干净地要求 14"，而是"11–13 上启动后崩" → **E4** |

---

## 4. 我自己新发现的问题（工程师未提）

**E5. `brand.icns` 只含 1x 尺寸，缺全部 @2x retina 变体**（低，cosmetic）
详见声明 4。最大 512×512，Retina 屏上图标会被放大显示、偏软。`iconutil -c iconset` 只能解出 5 档而非标准 10 档。

**E6. `dist/` 里有两个一模一样的 187MB app bundle**
```
Cullumi.app          187M
Cullumi-v1.0.5.app   187M   （Resources 44 项、Frameworks 45 项、主二进制同为 6579648 字节）
```
内容完全等价，属冗余。`dist/使用说明.md:125` 又只提到 `dist\Cullumi-v1.0.5\`（Windows 路径），macOS 用户不知道该开哪个。**建议只保留 `Cullumi.app` 一个**，或把带版本号的那个做成符号链接/在 README 说明。

**E7.（已排除，记账）** 我曾怀疑 `cullumi/analysis_worker.py` 的 Windows 内存限制代码会在 macOS 崩溃。**实跑证明有 `os.name != "nt"` 守卫，安全。** 同批核查的 `http_api.py:801 os.startfile`（`hasattr` 守卫）、`updates.py:134 winreg`（`os.name == "nt"` 分支内）**全部安全**。→ 结论：**「21 个核心模块字节级不变」这个决定在 macOS 上是安全的**，没有被冻结的 Windows 代码在运行期炸掉。这是对工程师架构决策的正面确认。

---

## 5. 建议补做的测试或修复（按优先级）

### P0 — 分发前必做
1. **修 E1 版本号**：`Cullumi-macos.spec` 的 `CFBundleShortVersionString` → `1.0.5`，重新打包。（1 行 + 重打包）
2. **修 E2 README**：把 `dist/使用说明.md` 改写为 macOS 版，**必须包含 Gatekeeper 绕过说明**（ad-hoc 签名 → 右键打开 / `xattr -dr com.apple.quarantine Cullumi.app`），否则用户第一次双击就打不开。顺手解决 E6 的 bundle 歧义。

### P1 — 质量与可复现
3. **修 E3 探针**：`probe_native_dialogs.py` 加 `sys.path` bootstrap，让交付的验证脚本能按原样复现。验证脚本跑不起来，等于没有验证脚本。
4. **改 E4**：`Info.plist` 加 `LSMinimumSystemVersion = 14.0`，让 11–13 用户得到友好拒绝而非崩溃。
5. **补一条我没跑到的测试**：`.app` 的**冷启动冒烟测试**。目前所有验证都在源码树用 venv 跑，`dist/` 里的打包产物只验了「能起来 + 首页渲染 + 403 鉴权」，**没有验证过打包后的完整业务流**（比如打包环境缺某个 hidden-import 导致点「选择照片文件夹」才崩）。建议在 `verify-macos.sh` 加一步：以 `--smoke` 参数启动 `.app` 并打几个关键 API 端点。

### P2 — 改进项（不阻塞，可另开 issue）
6. **图标 @2x**（E5）：1024×1024 源重做 10 档 iconset。
7. **NIQE oracle 平台化**（我的建议，与工程师的「不动测试」不冲突——他当时是对的，但长期该修）：按平台存 golden，或把 `tolerance_absolute` 放宽到 `0.01` 并注明跨架构浮点差异。**不要**把 macOS 实测值写回 oracle。
8. **`test_updates.py` 参数化**：让它真正平台感知，而不是靠淘汰侥幸通过（见声明 5 末）。

---

## 6. 声明可信度总评

工程师的**技术判断全部经得起独立验证**——特别是「零回归」这条，我没有找到任何反证，反而用双目录对跑 + 数值逐位比对 + NIQE 三文件哈希不变三重锁死了结论。三条对任务书错误假设的纠正也全部精确到行号且属实。

他的不足集中在**交付收尾**，而非移植质量：版本号、README、探针可运行性、图标完整度这 4 项属于「打包交付」环节，恰好是 PyInstaller 移植最容易静默失败、也最容易被「跑通一次就以为好了」掩盖的部分。**他在报告里主动列了 9 条遗留项这一点值得肯定**（CoreML 未启用、ad-hoc 签名这些都主动交代了），但「更新提示写死 Windows 属于禁改清单所以不改」是其中唯一一处把约束当挡箭牌——详见下方专项判断。

### 专项判断：「更新提示写死 Windows」该不该改？

**我的结论：应该改，而且这是唯一一条我不认可其处理方式的遗留项。**

工程师的理由是「`web/js/settings.js` 在禁改清单内」。但这个理由有两处站不住：

1. **「禁改清单」的目的是保护核心逻辑不被移植污染，不是禁止修 bug。** 这次移植的总目标包含用户明确要求的「保留应用内自动更新」。现在自动更新功能**技术上可用、后缀白名单也改对了**，但一旦触发「有新版但没有可直接下载的附件」，用户会看到「**没有可直接下载的 Windows 附件**」——在一台 macOS 机器上。这不是文案瑕疵，是**功能输出与运行平台矛盾**，用户会据此以为装错了系统或下载坏了。

2. **改动量极小，风险可控。** 两处、各一句话：
   - `cullumi/http_api.py:827`：`"最新版本没有可下载的 Windows 附件，请前往发布页查看"` → 改成平台中立或按 `sys.platform` 分支
   - `web/js/settings.js:399`：模板字符串里的 `Windows 附件` → 同样处理

   这**不触碰任何核心逻辑**（一句用户可见提示语），不改变 API 契约，不影响 Windows 分支行为，与「禁改清单」保护的东西毫无交集。若真要零改动 `web/js`，至少 `http_api.py:827` 这一处（Python 平台层，本次已在改）应该顺手改掉。

**判断这不是「借口」，而是「优先级判断失误」**——他把这个小改动归入了「遗留项」而没有做。成本 2 行，收益是消除一个用户可见的平台矛盾。**建议本轮补上。**
