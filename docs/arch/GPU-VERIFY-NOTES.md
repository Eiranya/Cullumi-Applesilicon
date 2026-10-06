# GPU 精度验证方案：合成人脸可行性（team-lead 前置探测）

> 目的：用户选择「用合成样本，不碰真实照片」来验证 CoreML vs CPU 的精度差异。
> 前提是合成人脸必须能走通 Cullumi 的眨眼检测流水线（YuNet 人脸检测 → OCEC 眨眼分类）。
> 本文件记录探测结论，供 engineer-core / QA 直接复用。

## 1. 产品阈值（实测读出）

`cullumi/config.py` 的 `BUILTIN_PROFILES`，三档模式（conservative / balanced / aggressive）
**眨眼阈值完全相同**：

| 参数 | 值 |
|---|---|
| `face_confidence_min` | **0.85** |
| `open_confidence_min` | 0.8 |
| `closed_confidence_min` | 0.8 |
| `min_eye_distance_px` | 12 |

即：人脸检测得分必须 ≥ 0.85 才会进入眨眼分类环节。

## 2. 失败的尝试（避免重复踩坑）

### 2.1 手绘示意图脸 → 峰值 0.287 / 0.069（**不可用**）

用 Pillow 基本图元画正面脸（椭圆脸型 + 眼白/虹膜/瞳孔 + 眉毛 + 三角鼻 + 椭圆嘴）：

| 样本 | 峰值 | 0.3 阈值 | 0.5 阈值 | 0.85 阈值 |
|---|---|---|---|---|
| 睁眼版 | 0.2866 | 0 | 0 | 0 |
| 闭眼版 | 0.0689 | 0 | 0 | 0 |

**峰值只有 0.29，远低于 0.85 阈值 → 根本走不到眨眼分类。**
脚本：`/tmp/dnd_poc/probe_yunet_sweep.py`（含阈值扫描 0.02→0.85）

### 2.2 Apple Color Emoji 的部分表情 → 仍不够

| 样本 | 峰值 | 0.85 阈值 |
|---|---|---|
| 😀 `emoji_grinning` | 0.1808 | 0 |
| 😐 `emoji_neutral` | 0.4964 | 0 |

表情符号（抽象面部）同样过不了。

## 3. ★ 可用的合成人脸（**推荐采用**）

| 样本 | 峰值 | 0.85 阈值检出 | 备注 |
|---|---|---|---|
| 🧑 `emoji_person` | **0.9128** | **1** | 最佳 |
| 👨 `emoji_man` | 0.9119 | 1 | 可用 |
| 👩 `emoji_woman` | 0.9041 | 1 | 可用 |
| 自绘写实版（睁眼） | 0.8963 | 1 | 可用 |
| 自绘写实版（闭眼） | 0.7945 | **0** | 闭眼版掉到阈值下 |

**结论：Apple Color Emoji 的完整人脸 emoji（🧑👨👩，U+1F9D1 / U+1F468 / U+1F469）能稳定通过 0.85 阈值**，
可用于驱动完整的眨眼检测流水线。渲染方式（实测有效）：

```python
from PIL import Image, ImageDraw, ImageFont
font = ImageFont.truetype("/System/Library/Fonts/Apple Color Emoji.ttc", 160)
img = Image.new("RGBA", (400, 400), (255, 255, 255, 255))
d = ImageDraw.Draw(img)
d.text((120, 120), "\U0001F9D1", font=font, embedded_color=True)
img.convert("RGB").save("face.png")
```
注意：**Apple Color Emoji 只在原生尺寸（160px）渲染**，传其他 size 会失败或渲染成空白/方框。

脚本：`/tmp/dnd_poc/probe_face_gen.py`（含 emoji 与自绘写实版两条路径，输出峰值排名）

## 4. 方案建议（给 QA / engineer-core）

### 4.1 端到端判定对比（**可行，推荐**）
用 emoji 人脸构造样本集：
1. 基线：🧑👨👩 原图（睁眼状态）
2. 变体：在 emoji 眼睛区域用椭圆肤色块覆盖并画一条眼睑线，模拟闭眼
   （注意：自绘写实版闭眼后峰值掉到 0.79 → **emoji 上做闭眼修改也可能跌破 0.85**，
   需要实测确认修改后的峰值；若跌破则该样本只用于「人脸检测层」对比，不用于「眨眼判定」对比）
3. 对每个样本分别用 CPU EP 与 CoreML EP 跑完整 `analyzer.analyze()`，
   比较 `blink_status` / `blink_closed_ratio` / `blink_confidence` / 每张脸的
   `eye_open_probabilities`

### 4.2 张量级对比（**已由 team-lead 完成，可复现**）
已在真实模型上实测（随机输入）：
- 人脸检测 12 个输出的 max_abs：最大 `kps_32` = 0.0167、`bbox_8` = 0.0139、`cls_8` = 0.0020
- 眨眼分类输出：随机输入下两 EP 双双饱和到 1.0，**无法区分** → 必须用有区分度的输入

### 4.3 ★ 端到端判定对比已实测跑通（team-lead 完成）

脚本 `/tmp/dnd_poc/probe_parity.py`，用 emoji 人脸跑完整 `analyze()` 路径。**实测结果**：

| 样本 | 人脸置信度 | 眼距(px) | 眼部裁剪数 | 睁眼概率 (CPU) | 睁眼概率 (CoreML) | max_abs | 判定一致? |
|---|---|---|---|---|---|---|---|
| 👨 man | 0.9119 | 79.15 | 2 | 0.005977 / 0.110411 | 0.006182 / 0.110488 | **0.000205** | ✓ 都是 `closed` |
| 👩 woman | 0.90405 | 79.62 | 2 | 0.877399 / 0.889964 | 0.878483 / 0.891207 | **0.001243** | ✓ 都是 `open` |
| 🧑 person | 0.9128 | — | 2 | — | — | — | ✓ 都是 `uncertain` |

**两个重要发现**：

1. **眨眼分类的 EP 间偏差（0.0002–0.0012）比人脸检测（0.0167）小一个量级。**
2. **概率未饱和**（0.006 / 0.877 有真实区分度），**不是**随机输入那种"双双饱和到 1.0"的假一致 —— 所以这个对比是有效的。

**流水线级结果**（同一张图分别用两 EP 跑 `analyze()`）：

| 样本 | CPU status / conf | CoreML status / conf | 判定一致 |
|---|---|---|---|
| 👨 man | `closed` / 0.9119 | `closed` / **0.911709** | ✓ |
| 🧑 person | `uncertain` / 0.0 | `uncertain` / 0.0 | ✓ |

⚠️ 注意 👨 man 的 `blink_confidence`：**CPU 0.9119 vs CoreML 0.911709** —— 数值确实不同。
这直接说明**缓存指纹必须包含 EP**：否则先跑 CPU 再切 CoreML 时，
`blink_rescan_required()` 会因 `MODEL_VERSION` 未变而跳过重算，
导致库里混存两种 EP 的结果且 UI 不做区分。

### 4.4 `input_fingerprint` 实现约束（重要）
`face_analysis.py:136-152` 的 `input_fingerprint` 是 **`@staticmethod`（无 `self`）**，
无法访问实例 provider。而 `scanner.py:1540` 存在**不经过实例**的调用：
```python
fingerprint_builder = self.face_analyzer or FaceAnalyzer   # ← 类对象
fingerprint = fingerprint_builder.input_fingerprint(...)
```
→ EP 信息必须编码进**类级/模块级**可见值（如派生的 `MODEL_VERSION`），不能依赖实例属性。
**QA 验证时必须同时测「实例调用」与「类调用」两条路径**，两条都要随 EP 变化。

### 4.5 必须如实说明的局限
- 合成人脸（emoji）与真实人脸分布差距大，**只能证明「差异是否存在、量级多大」，
  不能代表真实照片上的翻转率**。
- 本次实测中 3 个 emoji 样本的判定**全部一致**，但样本量极小（3 个）且非真实照片，
  **不构成「CoreML 在真实场景下不会翻转判定」的结论**。最终报告中必须写明这一点。


## 5. 附：脚本清单（均在 `/tmp/dnd_poc/`，不在仓库内）

| 脚本 | 用途 |
|---|---|
| `probe_yunet_sweep.py` | 手绘脸的阈值扫描（证明手绘脸不可用） |
| `probe_face_gen.py` | emoji 与自绘写实版生成 + 峰值排名（找出可用素材） |
| `probe_synthetic_face.py` | 早期探测（手绘脸，失败） |
| `e2e_drop.py` / `bridge_test.py` | 拖放链路验证（见 DND-TECH-NOTES.md） |
| `probe-coreml-cullumi.py` | CPU vs CoreML 性能与张量差异实测 |
