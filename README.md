# Cullumi for macOS

Cullumi 是一款**仅在本机运行**的照片筛选应用。它递归扫描照片目录，生成不裁切的缩略图，检查画质，找出完全重复照片和相似连拍，最后由你决定保留或隔离哪些照片。自动分析只评估技术指标，所有决定仍由你确认。

当前版本：**v1.1.0**（arm64 / macOS 14+）

>本仓库是 [Yuumi0221/Cullumi](https://github.com/Yuumi0221/Cullumi) 的 macOS 移植版，功能与界面与上游保持一致，并加入 Apple Silicon 硬件加速与若干 macOS 适配。移植范围与差异见 [`MACOS-使用说明.md`](MACOS-使用说明.md)。

---

## 功能

- **不裁切缩略图与卡片预览** —— 按显示框的实际设备像素供图，浏览器不再二次缩放
- **画质分析** —— 清晰度、曝光、对比度、暗/亮部剪切、亮度分布与感知哈希
- **完全重复检测** —— 按内容哈希跨目录识别字节级重复
- **相似连拍分组** —— 感知哈希 + 结构相似度，含时间邻近与文件序号邻近判定
- **RAW/JPEG 拍摄变体** —— 同一张曝光的多种格式关联为一个变体组，照片库中只显示代表文件一张卡片，卡片上标注其余关联格式；在大图预览中可随时切换显示哪一种格式
- **眨眼检测** —— 人脸定位 + 眼部分类（CoreML 硬件加速）
- **动态照片** —— iPhone Live Photo 与Android Motion Photo，支持改封面与保留声音缓存
- **决策与隔离** —— 单张/批量决定、CSV 导入导出、隔离与历史恢复

---

## 系统要求

| 项目 | 要求 |
|---|---|
| 操作系统 | **macOS 14（Sonoma）或更高版本** |
| 处理器 | **Apple Silicon（arm64）**：M1 / M2 / M3 / M4 / M5 |
| 磁盘空间 | 约 190 MB（应用包） |

不支持 Intel Mac。最低版本来自 onnxruntime 只提供 `macosx_14_0_arm64` 轮子这一事实；该组件是眨眼检测所必需。**本项目未在 macOS 14 以下的机器上实测过拦截行为。**

---

## 安装与首次打开

下载并解压 `.app` 后，**双击 `Cullumi.app`**。

### 如果双击打不开（重要）

本应用使用 **ad-hoc 签名**（本地自签名），**未经 Apple 公证**。没有开发者证书时 Gatekeeper 会拦截它，**这是预期行为，不代表文件损坏**。三种解除方式：

```bash
# 方式一：右键点开（最简单）
# 在 Finder 中右键 Cullumi.app →「打开」→「打开」，此后不再询问

# 方式二：移除隔离属性
xattr -r -d com.apple.quarantine /Applications/Cullumi.app

# 方式三：清除 Gatekeeper 记录（会忘记所有已放行应用，不建议）
sudo spctl --master-disable
```

> **为什么用 ad-hoc 签名**：Apple 的公证需要付费的开发者证书。本项目没有证书，因此只能本地签名——代价是每台机器首次打开都需手动放行。

---

## 使用

1. **导入** —— 点击「从文件夹导入」选择照片目录。整个目录树会被递归扫描。
2. **等待扫描** —— 照片发现、解码分析、重复确认、相似分组、眨眼检测依次进行。扫描可随时取消，再次扫描会复用未变化照片的结果。
3. **筛选** —— 在「照片库」中组合决定状态、分析结果与格式筛选，可按建议、名称、大小或拍摄日期排序；相似连拍可按组采纳。**相似组可按处理状态筛选（已处理／未处理／部分处理）。**
4. **查看** —— 点击照片放大。快捷键：`W/↑` 保留、`S/↓` 移除、`A/←` 上一张、`D/→` 下一张、`F` 切换同一张曝光的显示格式（RAW／JPEG）。动态照片显示 `LIVE` 标识，可播放、拖动时间轴、选择封面。
5. **隔离** —— 点击「隔离已标记移除」并检查清单。确认后文件移入原目录下的 `_照片筛选隔离`，之后仍可从隔离历史恢复。

### 数据存放位置

| 内容 | 路径 |
|---|---|
| 配置、项目数据库、缩略图、预览缓存、日志 | `~/Library/Application Support/Cullumi/` |
| 隔离文件 | 原照片目录下的 `_照片筛选隔离/` |

**项目数据库与缓存删除后，决定与隔离历史会一并丢失。** 需要保留时请勿直接删除该目录。

---

## 网络与隐私

- 只监听 `127.0.0.1`，接口使用每次启动随机生成的会话令牌
- **不收集、不上传任何数据**，照片处理全部在本机完成
- 仅「检查更新」访问 GitHub API（`api.github.com`），其余功能不联网
- 若 WKWebView 无法启动，会自动改用系统浏览器，原因记录在 `~/Library/Application Support/Cullumi/webview-error.log`

---

## 支持格式与限制

### 图片格式

| 类型 | 扩展名 | 解码器 |
|---|---|---|
| 常见图片 | JPG、JPEG、PNG、WebP、TIFF、TIF、BMP | Pillow |
| HEIF 系| HEIC、HEIC、HEIF、HEICS、HEIFS、HIF | pillow-heif |
| RAW | DNG、CR2、CR3、NEF、ARW、RAF、ORF、RW2、PEF | rawpy / LibRaw |

**RAW/JPEG 拍摄变体配对规则**：

- RAW 的拍摄时间从文件自身的 **TIFF 结构**读取（Pillow 读不到 RAW 的 EXIF），先找 `DateTimeOriginal` 再退回 `DateTime`
- 同名、且拍摄时间一致（**相差 5 秒内**）的 RAW 与非 RAW 图片关联为一个变体组，**两者可以位于不同目录**——例如 RAW 在 `raw/` 子目录、JPEG 在项目根目录
- 照片库中一个变体组**只显示一张卡片**（代表文件），卡片上的「关联格式」徽标列出组内其余格式；展开相似组时仍可逐张查看
- **大图预览里可以切换格式**：文件名旁的关联格式徽标是可点击的，点某个格式（或按 `F` 键循环）即切换显示。切换后文件名、尺寸、大小、评分、建议与保留／移除状态都换成该格式自己的数据——RAW 的评分与 JPEG 的评分本来就不一定相同。某一格式尚未分析出结果时，对应的那一段说明整段省略，不会显示成 `0` 或 `undefined`
- 折叠随筛选条件走：只筛 RAW 时 JPEG 代表不在结果集里，此时 RAW 顶上成为那张卡片，照片不会被隐藏
- 同名但拍摄时间相差较大的（例如不同活动的同号文件）**不会**关联
- 判定分三级回退：EXIF 拍摄时间 → 文件修改时间 → 文件名主干。**时间冲突即拒绝配对**——配错会丢RAW 文件，漏配只是多显示一张缩略图
- 视觉相似度只使用组内一个代表文件；字节级完全重复仍检查所有文件

### 已知限制

| 限制 | 说明 |
|---|---|
| **RAW 配对需重扫一次** | 本版起 RAW 分析缓存版本由 `raw-preview512-v2` 升至 `v3`。首次扫描会提示「需要重新扫描」，须执行一次以补齐 RAW 拍摄时间，否则跨目录配对不生效。扫描只读文件头，不解码整张图片 |
| **非整数缩放无法像素级1:1** | 显示倍率DPR 为 1.5 这类小数时，图片框尺寸为小数，无法恰好整数倍供图，浏览器仍会做极轻微缩放 |
| **竖构图高DPR 下预览偏大** | 竖构图在 DPR=2 时预览字节比横构图多约 150%（按元素宽度过供，属预期行为） |
| **RAW 连拍召回率依赖 EXIF** | RAW 文件若无拍摄时间，相似连拍的时间邻近判定会退化为文件序号判定 |
| **模型目录** | `models/` 下的眨眼检测与 NIQE 模型已随包分发，**不支持替换为自定义模型** |
| **macOS 14 未实测** | 最低版本限制来自 onnxruntime 轮子可用性，未在真实旧系统上验证过拦截行为 |
| **ad-hoc 签名** | 每台机器首次打开都需手动放行，见上文「安装与首次打开」 |

### 动态照片

- iPhone Live Photo 支持同一目录中同名的 HEIC/HEIF/JPEG 与 MOV 配对
- Android Motion Photo 支持带标准 Motion Photo XMP 的 JPEG 内嵌视频
- 动态部分首次播放生成保留声音的 WebM 缓存，占用额外空间
- 可选择不修改原图／每次修改前提醒／始终修改原图；修改前在项目缓存的 `source-backups` 保留备份
- **将视频帧写入原图后，静态图片会采用该帧的分辨率**，可能低于原始照片

### 分析范围

自动分析检查清晰度、曝光、对比度等技术指标，**不判断构图、表情偏好或照片的纪念价值**。小脸、侧脸、遮挡与低光照片可能无法可靠判断；只有非推荐照片中可靠检测到闭眼时才显示「眨眼」。关闭后重新启用眨眼检测不会自动开始扫描。

---

## 目录结构

```
Cullumi-Applesilicon/
├── app.py                    # 入口：配置日志、启动本地服务、创建 pywebview 窗口
├── cullumi/                  # Python 后端（25 个模块）
│   ├── media.py              #   解码、缩略图、指标、感知哈希、RAW EXIF 读取
│   ├── capture_variants.py   #   RAW/JPEG 变体分组与代表选择
│   ├── similarity.py         #   相似候选、结构比较、并查集分组
│   ├── face_analysis.py      #   人脸定位、眼部分类（CoreML 加速）
│   ├── scanner.py            #   发现、增量分析、重复确认、关系重建
│   ├── project_store.py      #   SQLite 模型、迁移、缓存路径
│   └── display_asset.py      #   卡片预览派生（按框×DPR 精确供图）
├── web/                      # 前端（原生 JS/CSS，无构建工具）
├── models/                   # 眨眼检测 + NIQE 模型（已随包分发）
├── tests/                    # 单元测试
├── evaluation/
│   ├── preview-resolution/   #   卡片预览清晰度的测量工具与报告
│   └── performance-results/  #   上述测量的输出（可再生成，不入版本控制）
├── deliverables/             # 预览清晰度专题的 PRD 与架构设计
├── docs/arch/                # 历史归档（移植计划、QA 报告、性能剖析）
├── Cullumi-macos.spec        # PyInstaller 打包定义
├── build-macos.sh            # 一龙构建：依赖 → 检查 → 打包
├── setup-macos.sh            # 依赖安装
├── verify-macos.sh           # ruff + 单元测试
├── verify-change-set.sh      # 与上游差异门禁
└── smoke-test-macos.sh       # 冒烟测试
```

---

## 从源码运行

```bash
# 1. 安装依赖（首次必做）
./setup-macos.sh

# 2. 启动
CULLUMI_PYTHON=/path/to/python3 ./build-macos.sh   # 或直接 python app.py
```

**Python 环境要求**：`numpy`、`Pillow`、`pillow-heif`、`rawpy`、`pywebview==6.1`、`onnxruntime`、`imageio-ffmpeg`，以及 PyObjC 系列（pywebview 在 macOS 上通过 WKWebView 使用它们）。完整列表见 `requirements-macos.txt`。

> `pywebview` 依赖 `proxy_tools` 与 `bottle` 在**模块作用域**无条件 import，两者无法从 `requirements-macos.txt` 安装——`setup-macos.sh` 已用 `--no-deps` 分步处理，**请勿手工 `pip install -r requirements-macos.txt`**。

### 检查与打包

```bash
./verify-macos.sh     # ruff + 全量单元测试
./build-macos.sh      # 依赖 → 检查 → 打包，产物在 dist/
```

### 差异门禁

```bash
./verify-change-set.sh
```

本移植版必须能说清**每一处**与上游的差异。该脚本校验四件事：冻结目录（`tests/`、`models/`）逐字节一致、差异集合精确匹配、新增文件在白名单内、README 与上游一致。白名单项均附授权理由。

> 相对上游，新增 `smoke-test-macos.sh`、`cullumi/display_asset.py`、`requirements-macos.txt`、`docs/arch/`、构建脚本与 spec；修改 `app.py`、10 个 `cullumi/` 模块、10 个 `web/` 资产、2 个测试文件。

---

## 许可

本项目沿用上游仓库的许可条款。模型文件各有独立许可，见 `models/` 下各目录的 `LICENSE` 与 `README.md`。