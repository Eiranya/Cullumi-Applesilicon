# Cullumi for macOS

Cullumi 是一款仅在本机运行的 macOS 照片筛选应用。它会递归扫描照片目录，生成不裁切的缩略图，检查画质，寻找完全重复照片和相似连拍，最后由用户决定保留或隔离哪些照片。

当前版本：v1.0.6

本应用由上游 Windows 版（`Yuumi0221/Cullumi`）移植而来，只替换了平台层，照片筛选核心逻辑与前端界面保持一致。

---

## 系统要求

| 项目 | 要求 |
|---|---|
| 操作系统 | **macOS 14 或更高版本**（Sonoma 及以上） |
| 处理器 | **Apple Silicon（arm64）**，M1/M2/M3/M4/M5 |
| 磁盘空间 | 约 190 MB |

不支持 Intel Mac。最低版本由 onnxruntime 的 arm64 官方轮子决定，该组件是眨眼检测所必需的。

> **关于最低版本的说明**：应用包内已声明 `LSMinimumSystemVersion = 14.0`，该值依据 onnxruntime 官方仅提供 `macosx_14_0_arm64` 轮子这一事实推导得出。但**本项目未在 macOS 14 以下的机器上实测过**该限制的实际拦截行为。若你在更早的系统上打开后出现异常，请先确认系统版本，或反馈该情况。

## 打开应用

在 `dist/` 目录中**双击 `Cullumi.app`** 即可。

### 如果双击打不开（重要）

本应用使用 **ad-hoc 签名**（本地自签名），**没有经过 Apple 公证**。因为没有开发者证书，macOS 的 Gatekeeper 会拦截它。这是预期行为，不代表文件损坏。

按以下任一方式解决：

**方式一：右键打开（推荐）**

1. 在 Finder 中右键点击 `Cullumi.app`
2. 选择「打开」
3. 在弹出的对话框中点击「打开」

**方式二：解除隔离标记**

在终端中执行（把路径换成实际位置）：

```bash
xattr -dr com.apple.quarantine "/path/to/Cullumi.app"
```

之后即可正常双击打开。

**方式三：系统设置放行**

如果前两种无效，打开「系统设置 → 隐私与安全性」，向下滚动找到被阻止的提示，点击「仍要打开」。

> 需要注意：以上三步是首次执行。之后系统会记住你的选择，后续启动不再询问。

### 为什么采用 ad-hoc 签名

因为构建环境没有可用的 Apple Developer ID 证书。如果需要把应用分发给其他人，必须先购买开发者证书、改用 Developer ID 签名并完成 Apple 公证（notarization），否则对方的 Gatekeeper 同样会拦截。

## 使用

首次打开后：

1. 点击「选择源文件夹」并选择照片目录。也可以点「从文件夹导入」新建项目。
2. 等待照片发现、解码分析、重复确认、相似分组和眨眼检测完成。扫描可以取消，再次扫描时会复用未变化照片的结果。
3. 在「照片库」的「查看」菜单中组合决定状态、分析结果与照片格式筛选，并可按建议、名称、大小或拍摄日期递增/递减排序；也可以从「智能建议、待决定、已保留、已移除」进入对应照片。「一键采纳」会按当前页面与设置批量标记尚未决定的照片，不会立即移动文件；相似连拍可按组采纳推荐保留与可考虑移除的照片。
4. 点击照片可以放大查看。使用 `W/↑` 保留、`S/↓` 移除、`A/←` 查看上一张、`D/→` 查看下一张。动态照片会显示 `LIVE` 标识，放大后可以播放、拖动时间轴、选择封面，并根据新封面重新分析照片质量。
5. 点击「隔离已标记移除」并检查完整清单。确认后，相关文件会移入原照片目录下的 `_照片筛选隔离`，之后仍可从隔离历史恢复。

### 数据存放位置

| 内容 | 路径 |
|---|---|
| 配置、项目数据库、缩略图、预览与动态视频缓存 | 项目缓存目录（导入时选定） |
| 应用配置与降级日志 | `~/Library/Application Support/Cullumi/` |
| 隔离文件 | 原照片目录下的 `_照片筛选隔离/` |
| 应用内更新下载的安装包 | `~/Downloads/` |

需要保留决定和隔离历史时，不要直接删除项目缓存目录。更换项目存储位置时，Cullumi 会先复制并校验数据库，成功后才切换到新位置。

## 支持格式与使用限制

### 图片格式

- 常见图片支持 JPG、JPEG、PNG、WebP、TIFF、TIF 和 BMP。
- HEIC、HEIF、HEICS、HEIFS 与 HIF 由 pillow-heif 解码。
- RAW 支持 DNG、CR2、CR3、NEF、ARW、RAF、ORF、RW2 与 PEF，由 rawpy 和 LibRaw 解码。
- 同目录、同名的 RAW 与非 RAW 图片会作为拍摄变体关联，但仍分别显示和管理。视觉相似度只使用其中一个代表文件，相似组展开时会同时显示代表照片的关联格式；字节级完全重复仍检查所有文件。
- 「相同照片同时决定」默认开启，手动保留、移除或清除决定时会同步关联格式；照片卡片和查看器会显示关联格式。该设置可以随时关闭，已有冲突决定不会被自动改写。
- RAW、HEIC、HEIF 与 TIFF 首次放大时会生成最长边不超过 2560 像素的 JPEG 预览缓存，原文件不会因此改写。
- 普通视频不会单独加入照片库，只会在组成受支持的动态照片时使用。

### 动态照片

- iPhone Live Photo 支持同一目录中同名的 HEIC、HEIF 或 JPEG 与 MOV 配对。在 iPhone 或「照片」App 中导出的 Live Photo 天然符合该形式，可直接识别。
- Android Motion Photo 支持带标准 Motion Photo XMP 的 JPEG 内嵌视频。
- 动态部分首次播放时会生成保留声音的 WebM 缓存，因此项目缓存会占用额外空间。
- 照片与配对 MOV 会作为同一项处理，隔离和恢复时不会拆开。
- 设置中可以选择不修改原图、每次修改前提醒或始终修改原图。原图封面修改支持 JPEG、HEIC 与 HEIF 动态照片，修改前会在项目缓存的 `source-backups` 目录保留备份。配对或内嵌的视频内容不会重新编码。
- 动态视频帧的分辨率可能低于原始静态照片。将视频帧写入原图后，静态图片会采用该帧的分辨率。

> 关于 `.AAE` 文件：Apple 导出的 Live Photo 可能附带 `.AAE` 伴生文件。Cullumi 不读取它，会把它计入「未支持的扩展名」。这不影响识别，动态照片靠 HEIC 与 MOV 的同名配对来识别。

### 分析范围

- 自动分析会检查清晰度、曝光、对比度等技术指标，不会判断构图、表情偏好或照片的纪念价值。所有决定仍由用户确认。
- 眨眼检测只处理非完全重复的相似连拍候选，只会调整组内推荐顺序，不会自动标记照片为移除。
- 小脸、侧脸、遮挡和低光照片可能无法可靠判断。只有非推荐照片中可靠检测到闭眼时才会显示「眨眼」，其余情况不显示状态。
- 关闭后重新启用眨眼检测不会自动开始扫描。现有结果失效时，设置页会显示「需要重新扫描」。

### 应用内更新

设置页可以检查 GitHub 上的新版本。macOS 版会自动识别 `.dmg` 与 `.zip` 格式的发布附件。

由于本移植版采用 ad-hoc 签名，**不执行自动安装**：新版本会下载到 `~/Downloads/`，由你手动替换应用（保留下载的包 → 拖入「应用程序」或覆盖 `Cullumi.app`）。照片与项目数据不受影响。

如果上游只发布了 Windows 附件，界面会提示前往发布页查看。

## 网络与隐私

- Cullumi 只监听 `127.0.0.1`，接口使用每次启动随机生成的会话令牌。
- 应用不收集、不上传任何数据。照片处理全部在本机完成。
- 仅「检查更新」会访问 GitHub API（`api.github.com`），其余功能不联网。
- 若 WKWebView 意外无法启动，应用会自动改用系统浏览器打开界面，原因记录在 `~/Library/Application Support/Cullumi/webview-error.log`。

---

## 开发者信息

### 环境要求

- macOS 14+（arm64）
- Python 3.13
- Xcode CommandLineTools

### 从源码运行

```bash
cd cullumi-macos

# 1. 安装依赖（首次必做）
./setup-macos.sh

# 2. 启动
/Users/nori95/.workbuddy/binaries/python/envs/default/bin/python3 app.py
```

`setup-macos.sh` 会依次安装 7 项直接依赖、PyObjC（pywebview 的 WKWebView 后端所需）、`pywebview` 本身及其无条件导入的 `bottle` / `proxy_tools`，最后安装 PyInstaller 与 Ruff。

> `proxy_tools==0.1.0` 的 sdist 存在打包缺陷，`pip install` 必然报 `EEXIST`。脚本通过直接下载并解包安装来绕过，因此**不要**用 `pip install -r requirements-macos.txt` 一次性安装（会在 pywebview 的传递依赖上失败）。

### 运行检查

```bash
./verify-macos.sh
```

等价的 Windows 版本是 `verify.ps1`，负责运行 Ruff 与全部 Python 测试。眨眼模型、NIQE 参数与许可证校验已包含在测试中。

**注意两个 macOS 特有的环境要求**（`verify-macos.sh` 已自动处理）：

- `TMPDIR` 必须指向非 `/var` 路径。macOS 的 `/var` 是指向 `/private/var` 的软链，会导致 `tempfile` 与 `Path.resolve()` 产生同一路径的两种字符串表示，引发 8 个假失败。
- 必须清空 `PYTHONPATH`。部分环境会注入 hook 了 `os.mkdir` 的 `sitecustomize.py`，在 `exist_ok=True` 时误抛 `EEXIST`。

预期结果：**199 个测试，196 通过，3 个失败**。3 个失败是 NIQE 黄金基线的跨架构浮点精度差异（基线录于 Windows x86-64），在**未修改的上游源码上运行同样会失败**，与 macOS 移植无关。

### 重新打包

```bash
./build-macos.sh
```

一条龙执行依赖安装 → 检查 → 打包。产物为 `dist/Cullumi.app`。

单独执行各步骤：

```bash
./setup-macos.sh        # 安装依赖
./verify-macos.sh       # Ruff + 全部测试
./build-app-macos.sh    # 生成图标 + PyInstaller + ad-hoc 签名
```

### 与上游的差异

`verify-change-set.sh` 逐文件核对并要求每处差异都有授权理由。当前实际差异：

**平台层**

| 文件 | 改动 |
|---|---|
| `app.py` | 启动流程按平台分支；macOS 走 `gui="cocoa"`（WKWebView），跳过 Edge WebView2 校验；新增 `configure_logging()` 写`cullumi.log`（bundle 为 `console=False`，stdout/stderr 与系统日志都拿不到） |
| `cullumi/config.py` | `app_data_dir()` 在 macOS 使用 `~/Library/Application Support/Cullumi` |
| `cullumi/native_dialogs.py` | 非 Windows 上禁用 PowerShell 兜底，改用 pywebview 的原生 `NSOpenPanel` / `NSSavePanel` |
| `cullumi/updates.py` | 发布资产后缀白名单按平台分支，macOS 优先 `.dmg` |
| `cullumi/http_api.py` | 更新提示文案按当前平台输出；新增卡片预览派生接口 |

**功能增强**（超出平台适配范围）

| 文件 | 改动 |
|---|---|
| `cullumi/face_analysis.py` | 接入 CoreML execution provider：人脸检测快约 5.3×、眼部分类快约 4.0×。执行后端折进 `input_fingerprint`，否则切换开关会让数据库留下两个后端的混合结果 |
| `cullumi/analysis_worker.py` | 分析进程池按 CPU 与内存预算重新计算规模（原公式除以内存上限，导致在所有机器上都返回 2，CPU 项从未生效） |
| `cullumi/similarity.py` | 相似组处理状态（已处理／未处理／部分处理）与组内决定查询 |
| `cullumi/photo_query_service.py` | 相似组分页与按状态筛选 |
| `cullumi/classification.py`、`cullumi/settings_service.py` | 上述功能的支撑改动 |
| `cullumi/capture_variants.py`、`cullumi/media.py` | RAW/JPEG 跨目录配对；RAW 拍摄时间从 TIFF 结构读取 |
| `cullumi/display_asset.py` | 卡片预览按「显示框 × 设备倍率」精确供图，避免浏览器二次缩放 |
| `web/`（10 个资产） | 相似组状态界面、拖放导入、卡片与徽章样式调整 |
| `tests/`（3 个文件） | worker池规模、设置默认值、跨目录配对、RAW EXIF 解析、版本号断言 |

**完全一致**：`models/` 全部模型与许可证；`tests/` 其余 18 个文件；`cullumi/` 其余模块。

> 上游的浏览���交互测试（`tests/dom/`，Playwright + Edge）在本移植不运行：快照基线录于 Edge/Windows，macOS 需用 WebKit 重录，且构建环境未安装 Node.js与浏览器。`verify-macos.sh` 会跳过并明确提示。

### 评估工具

不少于 300 张、60 组授权连拍的真实眨眼评估流程见 `evaluation/README.md`。评估工具会输出逐人脸预测、精确率、召回率、组推荐成功率以及 P50 和 P95 性能报告。

---

## 许可

本移植版沿用上游项目的许可。模型与第三方组件的许可文件随应用一并分发，位于 `Cullumi.app/Contents/Resources/models/`。

## 同目录文件说明

分发目录中只有一份本文档（`使用说明.md`）适用于 macOS。

另有一份 `上游说明.md`，那是 **上游 Windows 版的原始文档，仅供对照查阅**，其中的 `powershell`、`.venv\Scripts\`、`verify.ps1` 等内容不适用于 macOS。

