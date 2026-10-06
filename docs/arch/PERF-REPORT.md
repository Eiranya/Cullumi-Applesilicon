# Cullumi macOS 移植版 · 性能剖析报告

- 剖析对象：`/tmp/cullumi-perf2`（工作区 `/Users/inori95/WorkBuddy/编程任务/cullumi-macos/` 的隔离只读副本，含 team-lead 的 CoreML GPU 眨眼改动）
- 样本：**全部为合成样本，未读取任何真实照片**
- 所有数字均为**本机实测**生成；标注「推测」的条目为基于实测的推断
- 未运行的项在「未覆盖部分」明确列出

---

## 1. 摘要：最大的 3 个机会点

| # | 机会点 | 实测证据（同一 290 张样本库） | 预期收益 |
|---|--------|------------------------------|----------|
| **1** | **分析并行进程被硬性封顶为 2**（`analysis_worker.py:305` `parallel_worker_count()`） | 2 worker = 12.13s → 6 worker = **7.63s（1.59×）**；且每 worker 实测峰值仅 ~137MB，而内存预算按 1.5GB/进程计算，`budget//limit` 恰好=2，是双重封顶 | 扫描环节 **1.5–2.4×** |
| **2** | **每个 worker 每 50 张就重启**（`max_tasks_per_worker=50`），每次重启 ~0.38s | 首个任务 412ms vs 后续任务 34ms；仅把上限调到 1000，2 worker 12.13s → **8.88s（−27%）** | 中等库 20–30%，大库更多 |
| **3** | **默认 `fast_analysis=False` → 实际只跑 1 个 worker**（`config.py:389`） | 默认 1 worker 18.47s vs 开 fast 2 worker 12.13s（**1.52×**） | 开箱即用 **1.5×** |

> 三者叠加实测最好组合（6 worker + 上限 1000）：**5.95s**，相对开箱默认 **18.47s = 3.10×**。

---

## 2. 环境与样本

### 2.1 环境（实测）

| 项 | 值 |
|----|----|
| 平台 | macOS-27.2-arm64-arm-64bit-Mach-O |
| CPU | Apple M5，`os.cpu_count()` = **10** |
| 物理内存 | 16.0 GB（17179869184 bytes） |
| Python | 3.13.12 |
| numpy / Pillow / pillow-heif / rawpy | 2.3.5 / 12.2.0 / 1.7.0 / 0.25.1 |
| onnxruntime | 1.29.0，providers = `[CoreMLExecutionProvider, AzureExecutionProvider, CPUExecutionProvider]` |
| `default_worker_memory_limit()` | 1610612736 = **1.5 GB** |
| `parallel_worker_count()` | **2** |

### 2.2 合成样本（`/tmp/cullumi-samples/`）

| 目录 | 内容 | 用途 |
|------|------|------|
| `lab/` | 9 张：jpg/png/heic × (1.2MP / 12MP / 48MP) | 单张解码+分析成本 |
| `library/` | **290 张**：255 jpg + 20 png + 15 heic，含 10 组 4 连拍 | 扫描/分组/查询基准 |
| `blinklib/` | 48 张（12 组连拍，含 emoji 人脸） | 眨眼路径全流程 |
| `blink/` | 4 个 Apple Color Emoji 人脸 | 眨眼 CPU/CoreML 对比 |

样本生成方式：Pillow 合成结构场景（渐变+色块+微噪声），JPEG 用 draft 友好的常规编码；HEIC 用 pillow-heif。
**说明**：`lab/heic/large.heic`（48MP）解码失败（pillow-heif 报「Decoder plugin generated …」），已在所有统计中排除。

---

## 3. 各维度实测数据

### 3.1 扫描吞吐（290 张 `library`，NIQE 开）

阶段拆分为对该方法的耗时包装（`_scan_database`=解码与分析，`rebuild_similarity`=分组，`analyze_blinks`=眨眼）。

**阶段占比（fast=1、2 worker、眨眼开）：**

| 阶段 | 秒 | 占比 |
|------|----|------|
| discovering | ~0.005 | 0.0% |
| motion pairing（prepare 其余） | ~0.03 | 0.2% |
| **analyzing（解码+指标+NIQE）** | **16.77** | **86.4%** |
| hashing（完全重复） | 0.002 | 0.0% |
| grouping（相似组） | 0.07 | 0.4% |
| **blink_detection** | **2.48** | **12.8%** |
| reclassify | 0.01 | 0.1% |
| 合计墙钟 | 19.40 | 100% |

**worker 数量扫描（`library`，眨眼关，NIQE 开，默认每进程上限 50）：**

| workers | 墙钟(s) | 加速比 | 父进程 CPU(s) | 子进程 CPU(s) | 进程 spawn 次数 | 合并峰值内存(MB) |
|--------:|--------:|-------:|--------------:|--------------:|----------------:|-----------------:|
| 1 | 18.47 | 1.00× | 0.34 | 17.11 | 6 | 200 |
| 2 | 12.13 | 1.52× | 0.40 | 20.81 | 6 | 333 |
| 4 | 9.90 | 1.87× | 0.50 | 25.47 | 8 | 604 |
| 6 | 7.63 | 2.42× | 0.40 | 27.66 | 7 | 851 |

**提高每进程任务上限后（`max_tasks_per_worker=1000`）：**

| workers | 墙钟(s) | 相对同 worker 数(上限50) | 子进程 CPU(s) | spawn 次数 | 合并峰值内存(MB) |
|--------:|--------:|-------------------------:|--------------:|-----------:|-----------------:|
| 2 | 8.88 | −27% | 14.69 | 2 | 345 |
| 4 | 7.46 | −25% | 21.83 | 4 | 619 |
| **6** | **5.95** | **−22%** | 24.21 | 6 | 906 |
| 8 | 6.60 | ↓ 回退 | 29.06 | 8 | 1145 |

- 父进程 CPU 全程仅 **0.3–1.1s** → **父进程（DB/进度/派发）不是瓶颈**，并发上限不在 Amdahl 上，而在 worker 数与重启。
- 6→8 worker 出现回退（4P+6E 上超额订阅），**6 为甜点**。

**单张分析成本（进程内直调 `media.analyze_photo`，中位数）：**

| 文件 | MP | 完整(ms) | 关闭 NIQE(ms) |
|------|----:|---------:|--------------:|
| jpg/small | 1.23 | 34.5 | 18.6 |
| jpg/medium | 12.0 | 48.1 | 30.9 |
| jpg/large | 48.0 | 96.3 | 80.3 |
| png/small | 1.23 | 77.2 | 57.9 |
| png/medium | 12.0 | 375.6 | 328.6 |
| png/large | 48.0 | 1316.6 | 1293.0 |
| heic/small | 1.23 | 194.6 | 147.9 |
| heic/medium | 12.0 | **1654.9** | 1215.5 |

- JPEG 走 `draft("RGB",(512,512))`，**48MP JPEG 仅 96ms**；PNG/HEIC 无缩放解码 → **12MP HEIC 1.65s，是 48MP JPEG 的 17 倍**。
- `library` 上按格式汇总：jpg 255 张 ×33.7ms ≈ 8.6s、heic 15 张 ×240.9ms ≈ 3.6s、png 20 张 ×87.1ms ≈ 1.7s → **HEIC 占 5% 数量却吃掉 ~26% 分析时间**。

**NIQE 独立基准（120 张 512 预览，3 次）：**
`initialize_niqe` = 13.9ms；单张 **p50 15.5ms / p95 19.0ms / mean 15.8ms**（每 1000 张 ≈ 15.8s）。

**增量/复扫（canonical `benchmark_scan`，结果摘要一致，`single_parallel_results_equal=true`）：**

| 场景 | fast=False(1w) | fast=True(2w) |
|------|---------------:|--------------:|
| 首次扫描 | 18.67s | 15.06s |
| 未变化复扫 | 0.025s | 0.053s |
| 单张 NIQE 刷新 | 0.053s | 0.099s |
| 单张失效重算 | 0.111s | 0.221s |

- 增量扫描极快（未变化复扫 < 60ms）；**单进程与双进程结果 SHA-256 摘要完全一致**（正确性无损）。

### 3.2 界面响应速度（静态分析；无法 headless 真跑 WKWebView）

| 项 | 实测/静态结论 |
|----|----------------|
| 资源规模 | 8 个 JS（3323 行）+ 7 个 CSS（3991 行），无打包；index.html 29.6KB（渲染后 GET / 返回 49.3KB） |
| 分页 | `LIBRARY_PAGE_SIZE=120` / `SIMILAR_GROUP_PAGE_SIZE=120`（`runtime.js:13`） |
| 加载方式 | `IntersectionObserver`（rootMargin 600px）触发无限滚动 `loadLibraryPage(false)`，用 `insertAdjacentHTML("beforeend", …)` **追加**卡片，`state.items.push(...)` **累积**（`gallery.js:242–252, 770–776`） |
| DOM 增长 | 无虚拟化/回收：浏览到第 N 页即有 ~120·N 个 `<article class="photo-card">` + `<img>` 常驻 |
| 点击成本 | `openViewer` 用 `state.items.findIndex` O(n)；`updateCardDecision` 用 `querySelectorAll('[data-photo-id=…]')` O(n)（`gallery.js:413,444`） |
| 滚动成本 | 每次 scroll 触发 rAF 内多次 `getBoundingClientRect`（`gallery.js:4–27,756`） |
| `/api/photos` 响应 | 实测 179KB/页（4×120 页共 716,890 bytes，`benchmark_large_library`） |
| 缩略图接口 | `_send_file` 直接流式返回磁盘文件 + `Cache-Control: private, max-age=3600`，**不重新编码**；每次请求仅 `connect_db + SELECT`（见 3.3） |
| 首屏请求数 | 1×HTML + 7×CSS + 8×JS + icon = ~17 个静态请求（均 `max-age=3600`） |

**结论**：小库无碍；**数千张时 gallery DOM 与 `state.items` 无界增长**是最主要的 UI 风险（纯静态推断，未在浏览器实测）。

### 3.3 内存与启动时间（实测）

**启动（冷）：**

| 阶段 | ms |
|------|----:|
| `import cullumi.analysis_worker` | 170.6 |
| `import onnxruntime` | 59.5 |
| `import cullumi.scanner` | 52.5 |
| `import app`（其余） | 55.4 |
| **import 合计** | **338.0** |
| `ThreadingHTTPServer` 创建 | 9.8 |
| **模块导入 → HTTP ready** | **350.1** |
| `GET /`（渲染首页） | 18.1 |
| `GET /api/bootstrap`（冷/热） | 0.95 / 0.95 |
| import 后 RSS | 75.2 MB |

- `PhotoAnalysisPool()` 构造时**不预启动子进程**（启动后活跃子进程 = 0，懒启动）→ 启动不被 worker 拖累。
- `GET /` 花 18.1ms 而 bootstrap 仅 0.95ms：**每次请求都重读 index.html 并重新 `ET.parse(icons.svg)`**（`http_api.rendered_index`）。

**运行期内存：**

| 配置 | 父进程峰值 | 全部 worker 峰值 | 合并峰值 | 单 worker 峰值 |
|------|-----------:|-----------------:|---------:|---------------:|
| 1 worker（眨眼关） | 56.8 MB | 146.3 MB | 200 MB | ~137 MB |
| 2 worker | 57.3 MB | 275.8 MB | 333 MB | ~137 MB |
| 4 worker | 57.6 MB | 559.4 MB | 604 MB | ~137 MB |
| 6 worker | 57.7 MB | 847.9 MB | 906 MB | ~137 MB |
| 8 worker | 58.5 MB | 1086.4 MB | 1145 MB | ~137 MB |
| 眨眼开（父进程含 ONNX+CoreML） | 118–135 MB | — | — | — |

- **每 worker 实测峰值稳定 ~137MB**，而预算函数按 1.5GB/进程算 → 内存维度上**封顶过度约 11×**。
- 8 worker 合并仅 1.1GB，16GB 机器余量极大。

**每请求 DB 开销（5000 张库，各 200 次）：**

| 操作 | p50 | p95 |
|------|----:|----:|
| `connect_db + SELECT`（缩略图行，即 `/api/thumb` 路径） | 0.52 ms | 0.74 ms |
| `connect_db` 单独 | 0.067 ms | 0.10 ms |
| `SELECT`（热连接） | 0.011 ms | 0.021 ms |
| `variant_metadata(120 ids)` | 0.023 ms | 0.024 ms |

- **无 N+1**：`/api/photos` 每页固定查询数，`variant_metadata` 批量；`/api/thumb` 每请求 < 1ms 的 DB 开销（主要为新连接首次查询）。

**worker 重启成本（`worker_spawn_probe`）：**

| 指标 | 实测 |
|------|-----:|
| `import cullumi.media`（子进程首次导入） | 179.5 ms |
| `import numpy` | 127.7 ms |
| 首任务（含 spawn+导入） | 411.7 ms |
| 后续任务（同进程） | 34.1 ms |
| **每次重启的额外成本** | **≈ 378 ms** |
| 每任务重启（6 次）平均 | 418.3 ms |

### 3.4 大库规模场景（`benchmark_large_library --quick`：2000 文件 / 2000 照片 / 500 边）

| 场景 | 时间 | 备注 |
|------|-----:|------|
| discovery（2000 文件） | 0.092 s | 取消响应 6µs |
| similarity_groups（500 组） | 0.637 s | 仅 5 条 SELECT |
| profile_estimate | 0.410 s | |
| photo_queries（4×120 页） | 0.115 s | ≈29ms/页，179KB/页，15 条 SELECT |
| similarity_api | 0.078 s | 3 条 SELECT |
| exact_duplicates（500） | 0.479 s | 503 条查询 |

**结论**：非扫描路径（分组/查询/分组API/完全重复）在 2000 规模**均 < 1s**，不构成瓶颈，且已分页、已缓存拓扑（`SimilarityGroupCache`）。

### 3.5 眨眼检测（GPU 生效后）

| 场景 | CPU p50 | CoreML p50 | 加速 | CoreML 会话创建 |
|------|--------:|-----------:|-----:|----------------:|
| emoji 512×512 全脸（`blink_bench`） | 32.10 ms | 9.78 ms | **3.23×** | 1099–1375 ms（一次性） |
| 仅人脸检测（同上） | 21.09 ms | 3.84 ms | **5.57×** | — |
| 扫描同款 512 缩略图（`blink_scan_probe`） | 15.59 ms | 8.17 ms | **1.91×** | 1099 ms |

- 实测确认 **CoreML 生效**（`accelerator_tag=coreml`，`usable=true`）；provider 数值确有漂移（CPU 0.8912/0.8024/0.8987 vs CoreML 0.8939/…/0.8989），与设计注释一致。
- 扫描内 `analyze_blinks` 阶段：`library`（无人脸，仅检测）**2.48s**；`blinklib`（48 张有人脸）**3.11s(1w) / 3.75s(2w)**。该阶段**在主进程串行**，**不随 worker 数扩展**。
- 该阶段耗时主要由 **CoreML 会话冷启动（~1.1–2.5s，每进程一次性，之后复用）** + 每张 ~8ms 组成，而非每张成本失控。

---

## 4. 综合优化清单（优先级 / 实测证据 / 预期收益 / 成本 / 风险）

| # | 优先级 | 优化项 | 实测证据 | 预期收益 | 实施成本 | 风险 |
|---|:---:|--------|----------|----------|----------|------|
| O1 | **P0** | `parallel_worker_count()` 上限 2 → 依内存/核数取 **4–6**；内存预算模型按实测 137MB/worker + 余量重算 | w2 12.13s→w6 7.63s（1.59×）；内存合并 6w=906MB | 扫描 **1.5–1.9×**（与 O2 叠加达 2.4×+） | 小（改一处公式常量） | 低：结果 SHA 一致已实测；>6 会回退，需封顶 6 |
| O2 | **P0** | `max_tasks_per_worker` 50 → **500–2000**（或按内存增长自适应） | 重启 412ms vs 34ms；w2 12.13→8.88s（−27%） | 中等库 20–30%，**大库更多**（5000 张=每 worker ~100 次重启） | 极小（改常量） | 低：单进程驻留内存 137MB；需确认长驻无累积泄漏 |
| O3 | **P1** | 默认开启/自动启用 `fast_analysis` | 默认 1 worker 18.47s vs 2 worker 12.13s | 开箱 **1.52×** | 小 | 低 |
| O4 | **P1** | HEIC/PNG 走缩略图级解码（嵌入预览/`reload_size`/降采样） | 12MP HEIC 1.65s vs 12MP JPEG 48ms；HEIC 占样本 ~26% 分析时间 | HEIC 重的 iPhone 库最多 ~25% | 中（编解码 API） | 中：解码正确性与指纹/缓存需回归 |
| O5 | **P1** | 眨眼检测并行化 / 下沉到 worker 池 | 该阶段串行 2.48–3.75s，**不随 worker 扩展**；占 13–20% | 人脸密集库最多 ~15% | 中（`FaceAnalyzer` 有全局 `_inference_lock`，需每进程会话） | 中：每进程 CoreML 会话内存 + 冷启动 |
| O6 | **P2** | gallery 虚拟化/DOM 窗口化（当前无限追加） | `gallery.js:247` 追加、`items` 累积；5000 张 ≈ 5000 卡片常驻 | 数千张时滚动更顺、内存更低 | 中 | 低-中（滚动位置/选择状态需保持） |
| O7 | **P3** | 缓存 `rendered_index`（当前每请求重读 HTML + 重解析 SVG） | `GET /` 18.1ms vs bootstrap 0.95ms | 每次开应用省 ~17ms | 极小 | 低 |
| O8 | **P3** | 复用/缓存 sqlite 连接以减少 `/api/thumb` 每请求建连 | connect_db+SELECT 0.52ms（热 SELECT 0.011ms） | 每页 120 缩略图省 ~60ms | 小 | 低-中（连接生命周期/并发） |

---

## 5. 已实测 vs 推测

**已实测（本机直接测量）：**
- 环境常量、`parallel_worker_count()=2`、内存模型
- 所有阶段耗时、worker 数量扫描、上限扫描、内存/CPU、spawn 成本
- 单张解码成本、NIQE、启动、每请求 DB 开销、2000 规模非扫描场景
- 眨眼 CPU/CoreML p50 与加速比、CoreML 会话创建
- 结果摘要一致性（单/并行一致）

**推测（基于实测的推断，未单独端到端验证）：**
- O4「最多 ~25%」——按 HEIC 在样本中的解码占比外推，未在高 HEIC 比例大库端到端验证
- O2「大库更多」——按 5000 张外推重启次数，未实测 5000 张真扫描
- O6「数千张更顺」——静态代码推断，未在 WKWebView 中实测帧率/内存
- O5「~15%」——按眨眼阶段占比外推，未实现并行版本验证

**其它说明：**
- 单/双/多 worker 的墙钟存在 **~20% 波动**（测得同一配置 12.1–15.1s），因本机与 team-lead 的 GPU/拖放工作**并发运行**；趋势与相对差值稳健，绝对值请视为区间。

---

## 6. 未覆盖部分

1. **真实 GUI（WKWebView）**：无法 headless 启动 pywebview，界面维度仅为静态分析，未测首屏渲染耗时、滚动帧率、DOM 内存。
2. **真实 iPhone HEIC/RAW**：RAW 无合成写工具已跳过；`lab/heic/large.heic` 合成样本解码失败（非应用缺陷但样本缺口）。
3. **大库（5000–50000 张）端到端扫描**：仅 290 张实测扫描；2000 张仅覆盖非扫描场景。
4. **GPU/ANE 占用率与功耗**：未测 CoreML 的 GPU/ANE 利用率、能耗、热降频。
5. **动态照片/视频 sidecar（Live Photo）路径**：未覆盖 motion 相关解码与封面写回。
6. **上游 `benchmark_niqe.py` 在本 build 直接崩溃**（`initialize_niqe.cache_info()` 不存在），本报告改用自建 `niqe_probe.py` 测量；上游脚本需修复。
7. **测试套件未运行**（本任务为性能剖析，未涉及回归）。

---

## 7. 复现方式

```bash
# 样本（合成，零隐私）
env -u _ -u BASH_ENV -u PYTHONPATH TMPDIR=$HOME/cullumi-tmp \
  python /tmp/perf2/gen_samples.py
env -u _ -u BASH_ENV -u PYTHONPATH TMPDIR=$HOME/cullumi-tmp \
  python /tmp/perf2/gen_blinklib.py

# 测量
zsh /tmp/perf2/run_suite.sh     # 阶段/canonical/大库/NIQE
zsh /tmp/perf2/run_suite2.sh    # 眨眼/worker 扫描
zsh /tmp/perf2/run_suite3.sh    # 启动/请求开销/spawn 成本
zsh /tmp/perf2/run_suite4.sh    # 上限优化/眨眼上下文

# 原始结果 JSON 位于 /tmp/perf2/results/
```

工具：`scan_harness.py`（阶段+内存+CPU）、`blink_bench.py`、`blink_scan_probe.py`、`niqe_probe.py`、`startup_probe.py`、`conn_probe.py`、`worker_spawn_probe.py`、`mem_probe.py`（Mach/libproc，替代被沙箱屏蔽的 `/bin/ps`）。

---

## 8. 实施附注（team-lead 追加）

本节记录报告 §4 优化清单的实际处置，**并纠正其中一条建议的安全问题**。请以本节为准判断当前代码状态。

### 8.1 ★ 对 O1 建议的纠正：137MB 不能用，须用最坏情况 622MB

报告 O1 建议「内存预算模型按实测 137MB/worker + 余量重算」。**照此实施会造成内存超配。**

原因：137MB 是**混合库平均值**，而库中 88% 是 JPEG——JPEG 走 `draft()` 缩放解码，成本极低。
按**单张、独立进程**实测各格式峰值（`peak_mb` 为进程高水位）：

| 文件 | 尺寸 | 峰值 | 像素量级 |
|---|---:|---:|---|
| png/large.png | 77.3MB | **622.3MB** | 48MP |
| heic/medium.heic | 7.8MB | **306.6MB** | 12MP |
| png/medium.png | 19.3MB | 224.7MB | 12MP |
| heic/small.heic | 0.8MB | 95.7MB | 1.2MP |
| jpg/medium.jpg | 1.0MB | 92.9MB | 12MP |
| **jpg/large.jpg** | 4.1MB | **89.6MB** | **48MP** |

**同为 48MP，JPEG 峰值 89.6MB、PNG 峰值 622.3MB——差 7 倍。** 所以按平均值配 6 个 worker 需要
`6 × 622MB = 3.65GB`，**超出 3GB 预算**；而按 137MB 估算会算出「6 个才用 822MB」，严重低估。

**实际采用的模型**（`cullumi/analysis_worker.py`）：
```python
MEASURED_WORKER_PEAK_BYTES = 640 * 1024 * 1024   # 最坏情况，非平均
budget = min(物理内存 * 0.20, 3GB)
by_memory = budget // MEASURED_WORKER_PEAK_BYTES  # 16GB → 4
by_cpu    = cpu_count - 1
return min(MAX_PARALLEL_WORKERS, by_cpu, by_memory)
```
**本机（16GB/10 核）结果 = 4**，最坏情况 `4 × 640MB = 2.5GB`，守住 3GB 预算。

### 8.2 实测对照（290 张混合库，NIQE 开，眨眼关）

**⚠️ 本节数字经过一次更正，请以此处为准。** 初版给出的「2.85×」来自单次采样，复测后发现不可靠，原因见 8.2.1。

新旧默认的**配对重复测量**（各 7 次交错执行，同一台机器）：

| 配置 | 耗时范围 | 中位数 |
|---|---|---:|
| 旧默认（1 worker，cap50） | 9.87 – 17.26s | 13.67s |
| **新默认（4 workers，cap500）** | **6.36 – 9.42s** | **7.99s** |

**提速：逐对中位比 1.71×（范围 1.55–1.83×）。两组区间完全不重叠，方向无歧义。**

各档位单次对照（用于看趋势，**绝对值受负载影响，勿引用**）：

| 配置 | 耗时 |
|---|---:|
| 1 worker cap50（旧开箱） | 9.11s |
| 1 worker cap500 | 7.96s |
| 2 worker cap50 | 6.50s |
| 2 worker cap500 | 5.95s |
| 4 worker cap50 | 5.50s |
| 4 worker cap500（新开箱） | 5.03s |

结论仍然成立：**cap500 在每个 worker 数下都快于 cap50**（worker 重启成本），且 worker 数越多越快。

#### 8.2.1 为什么更正，以及一个方法论教训

两个独立错误叠加，都值得记录：

1. **测试工具加载了过期的代码副本。** `scan_harness.py` 硬编码了
   `sys.path.insert(0, "/tmp/cullumi-perf2")`（性能工程师的隔离副本，冻结于本次改动之前），
   而不是工作区。于是它读到的 `parallel_worker_count()` 一直是**旧值 2**。
   → 影响：**显式传 `--workers N` 的对照仍然有效**（N 由命令行强制，与模块常量无关）；
   但任何依赖代码默认值的测量**全部无效**。
   （已在 `/tmp/myh/scan_harness.py` 修正路径后重测；未改动 `/tmp/perf2/` 原件，因为本报告 §7 引用了它。）
2. **单次采样在噪声机器上不可信。** 本机环境固有负载 `load average ≈ 4.5`（10 核），
   单次扫描的墙钟波动达 **±40–50%**。初版「旧 9.94s vs 新 3.49s」正是两个不同负载时刻的采样相减，
   把 3.49s 这个偏快的样本当成了稳态值，从而把提速夸大成 2.85×。

**教训：并发/并行类改动必须用「交错 + 重复 + 取中位/最小」的方式测，且必须先确认测试工具加载的是被测代码本身。**

#### 8.2.2 关于绝对值的说明

本机当前 `load average ≈ 4.5`，基线已占掉部分核心，**会压缩并行收益**。
因此 1.71× 应视为**该负载条件下的实测值**；在空闲机器上并行度带来的相对收益应更大，
但本次未能在低负载条件下取得可靠样本，故不给出更乐观的估计。


### 8.3 逐项处置

| # | 状态 | 说明 |
|---|---|---|
| O1 | **已实施（上限取 4 而非 6）** | 见 8.1；冻结测试 `test_parallel_capacity_obeys_cpu_and_total_memory_budget` 已同步更新，并经产品所有者同意解冻（门禁新增 `AUTHORIZED_FROZEN` 白名单） |
| O2 | **已实施** | `DEFAULT_MAX_TASKS_PER_WORKER` 50 → 500。独立泄漏检查：单进程 3 轮 870 任务，峰值 RSS 122.0 → 122.6MB（+0.6MB），确认长驻不累积 |
| O3 | **已实施** | `fast_analysis` 默认值 `False` → `True`，开箱即用并行分析。冻结测试 `test_settings_service.py:186` 同步更新（第二个解冻例外，同样登记进 `AUTHORIZED_FROZEN`），其往返断言改为「关闭方向」以保持覆盖有效。**存量配置里已固化的旧值不会自动跟随**，需在 设置 → 照片分析 手动开启（或删除该键使其回落新默认） |
| O4 | **未实施，待决策** | HEIC/PNG 缩略图级解码。**这是 iPhone 用户库的最大单点**（12MP HEIC 1655ms vs 48MP JPEG 96ms），且它同时会让 8.1 表里的 622MB 峰值大幅下降——**既是性能收益也是内存收益**，还能顺带解锁更高并行度。但改动解码路径会影响质量指标与缓存指纹，需单独一轮校准 |
| O5 | 未实施 | 眨眼检测下沉到 worker 池并行（当前主进程串行，占整扫 13–20%） |
| O6 | 未实施 | gallery 虚拟化（数千张时 DOM 无界增长）。已核实 `web/js/gallery.js:242` 为 `insertAdjacentHTML("beforeend")` 追加式，全文件无 `virtual`/`recycle`/`removeChild` |
| O7 | 未实施 | 缓存 `rendered_index`（`http_api.py:149` 无缓存；每次开应用省 ~17ms，收益过小） |
| O8 | 未实施 | 复用 sqlite 连接（`http_api.py` 现有 9 处 `connect_db`；每页 120 缩略图省 ~60ms） |


### 8.4 复验说明
- 8.2 的配对数字由 team-lead 在同一台机器上**交错重复实测**得出（各 7 次）。报告正文中 2 worker 的
  绝对值（12.13s）偏高，原因是其测量期间本机并发运行了 GPU/拖放改动（报告 §5 已自行标注此干扰）。
  **趋势一致，绝对值以 8.2 为准。**
- **本报告正文 §1 摘要表中的「相对收益」为大方向估计，不作为承诺值**；
  经重复测量校正后的实际数字见 8.2（旧→新默认 **1.71×**，而非初版曾写的 2.85×）。
- 8.2 的绝对值在**当前环境负载（`load average ≈ 4.5`）**下测得，会低估空闲机器上的并行收益。
- `evaluation/benchmark_niqe.py` 崩溃（`initialize_niqe.cache_info()` 不存在）为上游缺陷，本次未修（`evaluation/` 不在本移植的授权改动清单内）。
- 9.4 节若被引用请注意：本次两处解冻（`test_analysis_worker.py`、`test_settings_service.py`）均已登记进
  `verify-change-set.sh` 的 `AUTHORIZED_FROZEN`，且该门禁的**双向自证**（未授权改动 / 清单失效条目均报错）已通过。

