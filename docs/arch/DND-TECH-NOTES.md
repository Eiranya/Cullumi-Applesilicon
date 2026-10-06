# 拖放功能（Task #2）技术验证记录

> 由 team-lead 在派单前完成的前置验证。结论可直接作为实现依据，不必重复验证。

## 1. pywebview 6.1 的 macOS 拖放内核已存在（无需 hack）

`webview/platforms/cocoa.py:409-438` 定义了 `BrowserView.WebKitHost(WebKit.WKWebView)` 的子类方法：

```python
class WebKitHost(WebKit.WKWebView):
    def performDragOperation_(self, sender):
        if sender.draggingSource() is None and _dnd_state['num_listeners'] > 0:
            pboard = sender.draggingPasteboard()
            urls = pboard.readObjectsForClasses_options_([AppKit.NSURL], {NSURLFileURLsOnly: True}) or []
            files = [
                (os.path.basename(os.path.dirname(fp)) if os.path.isdir(fp)
                 else os.path.basename(fp), fp)
                for url in urls
                for fp in [urllib.parse.unquote(url.filePathURL().absoluteString().replace('file://', ''))]
                if os.path.isdir(fp) or os.path.isfile(fp)
            ]
            _dnd_state['paths'] += files
        return super().performDragOperation_(sender)
```

## 2. 激活门控（**最关键的一点**）

`cocoa.py:411` 的条件是 `sender.draggingSource() is None and _dnd_state['num_listeners'] > 0`。

而 `_dnd_state['num_listeners']` **只由 Python 侧 DOM API 递增**：

- `webview/dom/element.py:396-397` → `Element.on(event, callback)` 中 `if event == 'drop': _dnd_state['num_listeners'] += 1`
- `webview/dom/element.py:422-423` → `Element.off()` 对应递减

**推论（决定性）**：纯前端 `document.addEventListener('drop', ...)` **无法**激活此路径。即便激活了，浏览器沙箱下 `event.dataTransfer.files` 只提供 `file.name`（裸文件名），**拿不到真实文件系统路径**。因此必须在 Python 侧通过 `window.dom` 注册 drop 监听。

## 3. 路径回传链路

`webview/util.py:287-305`（`pywebviewEventHandler` 分支）：

```python
if event['type'] == 'drop':
    files = event['dataTransfer'].get('files', [])
    for file in files:
        path = [item for item in _dnd_state['paths']
                if urllib.parse.unquote(item[0]) == file['name']]
        if len(path) == 0:
            continue
        file['pywebviewFullPath'] = urllib.parse.unquote(path[0][1])
        _dnd_state['paths'].remove(path[0])
for handler in element._event_handlers.get(event['type'], []):
    Thread(target=handler, args=(event,)).start()
```

即：JS 侧用 `file.name` 与 `_dnd_state['paths']` 里的 `js_name` 做匹配，命中后把真实路径挂到 `file['pywebviewFullPath']`，**然后从队列中移除该项**。回调在独立线程中执行，回调里可通过 `event['dataTransfer']['files'][i]['pywebviewFullPath']` 取路径。

## 4. 已实测验证的事实

### 4.1 门控可被激活（实测通过）
脚本：`/tmp/dnd_poc/probe_gate.py`（在真实 GUI 会话中用 `webview.start(gui="cocoa")` 跑）

结果：
```json
{
  "dom_get_element": true,
  "on_drop_registered": true,
  "num_listeners_before": 0,
  "num_listeners_after": 1,
  "webkithost_has_perform_drag": true
}
```
即 `window.dom.get_element("#zone").on("drop", cb)` 确实能把计数器从 0 提到 1。

### 4.2 粘贴板路径解析链路正确（实测通过）
脚本：`/tmp/dnd_poc/probe_pasteboard.py`

用真实目录（含中文与空格）写入 `NSPasteboard`，复现 pywebview 的读取与匹配逻辑：
```json
{
  "folder_resolved": ".../我的照片 文件夹/",
  "file_resolved": ".../单张照片.jpg",
  "handler tuples": [
    ["我的照片 文件夹", ".../我的照片 文件夹/"],
    ["单张照片.jpg", ".../单张照片.jpg"]
  ],
  "JS-name -> path resolution": {
    "我的照片 文件夹": ".../我的照片 文件夹/",
    "单张照片.jpg": ".../单张照片.jpg"
  }
}
```
中文、空格路径均可正确传递。

## 5. ⚠️ 已实测发现的缺陷（实现时必须处理）

**文件夹路径带尾随斜杠**：`NSPasteboard` 读出的目录 URL 经 `filePathURL().absoluteString()` → `unquote()` 后是 `.../我的照片 文件夹/`（**结尾有 `/`**），而文件路径不带。

pywebview 用 `os.path.dirname` + `os.path.basename` 两层拼出 `js_name` 正是为了规避这个（两层取完与 JS 的 `file.name` 一致）。

**但传给业务层的是带斜杠的原始路径。** 若后端直接拿它算项目 ID：
```python
project_id_for(Path(body["root"]).resolve())   # cullumi/http_api.py:779 的现有写法
```
则必须确保规范化。虽然 `Path.resolve()` 通常会去掉尾斜杠，但**不要依赖它**——`cullumi/face_analysis.py` 同级还有 `os.path.basename` 拼接逻辑，且 Windows 上游代码用 `\\` 分隔符。**实现时显式 `os.path.normpath()` / `Path(...).resolve()` 后再用，并在 QA 阶段用带尾斜杠的路径实测 `project_id` 是否一致。**

### 5.1 端到端链路已实测打通（`/tmp/dnd_poc/e2e_drop.py`）

完整复现了「注册 → 入队 → 事件分发 → 回调收到路径」全链路，结果：

```json
{
  "registered": true,
  "callback_fired": true,
  "paths": [".../T/e2e-stv18g3_/我的照片 文件夹/"],
  "raw_keys": ["name", "pywebviewFullPath"],
  "raw_first": {
    "name": "e2e-stv18g3_",
    "pywebviewFullPath": ".../T/e2e-stv18g3_/我的照片 文件夹/"
  }
}
```

**由此确定的两条实现依据**：

1. **事件对象的确切结构**：回调收到的 `event` 形如
   ```python
   {
     "type": "drop",
     "dataTransfer": {
       "files": [
         {"name": "<js侧匹配名>", "pywebviewFullPath": "<真实路径>"},
       ]
     }
   }
   ```
   `files[i]` **只有 `name` 与 `pywebviewFullPath` 两个键**（`raw_keys` 实证）。注意 `pywebviewFullPath` 是 pywebview 在匹配成功后才补上的——若 JS-name 匹配失败，该键**不存在**，必须用 `.get()` 取并判空。

2. **尾随斜杠在真实链路中确实出现**（`raw_first.pywebviewFullPath` 结尾是 `/`），第 5 节的缺陷是真实存在的，不是测试artifact。

复现要点（供 QA 复用）：需用 `from webview.util import js_bridge_call`（注意**不是** `_js_bridge_call`），签名 `js_bridge_call(window, func_name, param, value_id)`；`func_name` 传 `"pywebviewEventHandler"`，`value_id` 传 `"eventHandler"`。回调在独立线程执行，需给 1–2 秒等待。

## 6. ★ 免改后端的桥接方案（已实测，**推荐采用**）

### 6.1 动机
GPU 任务已在修改 `app.py`、`cullumi/http_api.py`、`cullumi/config.py`、`web/index.html`。若拖放功能也去改 `http_api.py` / `index.html`，两条工作流产生文件重叠 → 必须串行，且有并发覆盖风险。

### 6.2 方案
Python 侧的 drop 回调**不新增 HTTP 路由**，而是直接用 `window.evaluate_js()` 调用前端已存在的全局函数。这样拖放只需要改：
- `app.py`（注册 drop 监听）
- `web/js/session.js`（定义一个全局入口函数）
- `web/css/home.css`（拖拽高亮样式）

**完全不碰 `http_api.py` 与 `index.html`** → 与 GPU 任务的文件交集降为 0。

### 6.3 实测证据（`/tmp/dnd_poc/bridge_test.py`）

```json
{
  "steps": [
    "drop listener registered",
    "pre-drop typeof = 'function'",
    "handler thread = Thread-5 (handler)",
    "raw paths = ['.../T/bridge-htb6p2pv/我的照片 文件夹/']",
    "normalised = '.../T/bridge-htb6p2pv/我的照片 文件夹' (trailing slash gone: True)",
    "evaluate_js returned = 'opened:.../我的照片 文件夹:1'"
  ],
  "roundtrip_ok": true
}
```

由此确认三件事：
1. **`evaluate_js` 可以从 drop 回调线程安全调用** —— 回调运行在 `Thread-5 (handler)`（非主线程），但 pywebview 的 cocoa 后端会内部编组到主线程执行，**未死锁、正常返回**。
2. **外部 `<script>` 定义在 `window` 上的全局函数可被 `evaluate_js` 访问**（`typeof` 返回 `'function'`）。所以 `web/js/session.js` 里挂一个 `window.xxx = function(){...}` 即可被 Python 调到。
3. **`os.path.normpath()` 有效消除尾随斜杠**（`trailing slash gone: True`），可解第 5 节的缺陷。

### 6.4 实现要点
- Python 侧：
  ```python
  import json as _json, os
  root = os.path.normpath(path_from_event)          # 消除尾随斜杠
  window.evaluate_js(f"window.cullumiHandleDroppedPath({_json.dumps(root)})")
  ```
  **路径必须用 `json.dumps()` 转义后再嵌入 JS 字符串**，否则含引号/反斜杠的路径会破坏脚本。
- 前端侧（`web/js/session.js`）定义：
  ```js
  window.cullumiHandleDroppedPath = function (root) {
    if (state.project) { toast("请先返回首页再拖入新的文件夹"); return; }
    // 复用既有流程，跳过 choose-folder 这一步：
    //   json("/api/project/open", { root }) -> showProject(p) -> startScan()
  };
  ```
  这样仍然复用了既有的 `/api/project/open` 与 `/api/scan` 路由，**没有新造打开逻辑**，只是省掉了「弹原生选择框」这一步。
- 拖拽高亮：pywebview 只提供了 `drop` 事件，**`dragenter`/`dragover`/`dragleave` 也可通过 `element.on(...)` 注册**（`num_listeners` 只统计 `drop`，但其余事件同样走 DOM API 通道）。若注册这些事件不可行，退而求其次可由 JS 侧自身监听 `dragenter`/`dragover` 做纯视觉高亮（视觉不需要真实路径，前端自己就能做），只有 `drop` 必须走 Python。



## 7. 打包可行性（**已验证，无需改 spec**）

担心点：`webview.dom` 是子包，通过 `from webview.dom import _dnd_state` 导入，而 `Cullumi-macos.spec` 的 `hiddenimports` 只列了 `webview` 与 `webview.platforms.cocoa`，PyInstaller 静态分析可能漏收。

**实测结论：不会漏。** 用 PyInstaller 6.16.0 的 `CArchiveReader` + `ZlibArchiveReader` 读取 `dist/Cullumi.app/Contents/MacOS/Cullumi` 的 PYZ 归档（724 个模块），`webview` 家族完整存在：

```
webview, webview.dom, webview.dom.classlist, webview.dom.dom,
webview.dom.element, webview.dom.event, webview.dom.propsdict,
webview.errors, webview.event, webview.guilib, webview.http,
webview.localization, webview.menu, webview.models, webview.platforms,
webview.screen, webview.state, webview.util, webview.window,
webview.platforms.cocoa, webview.platforms.gtk, webview.platforms.qt, ...
```

探测脚本：`/tmp/dnd_poc/probe_pyz.py`（若需复跑，注意 PYZ 条目名是 `PYZ.pyz` 而非 `PYZ-00.pyz`）。

同时确认 bundle 内已含：
- `Contents/Frameworks/onnxruntime/capi/libonnxruntime.1.29.0.dylib` + `onnxruntime_pybind11_state.so`
- `Contents/Frameworks/models/blink/{face_detection_yunet_2023mar.onnx, ocec_c.onnx}`

## 8. 实现要点小结

1. **`app.py`**：`webview.create_window(...)` 后，在 `webview.start()` 之前无法访问 DOM（窗口未就绪）。可行做法有二：
   - 用 `webview.start(func, args)` 的回调：`webview.start(lambda: wire_dnd(window), gui="cocoa")` —— 回调在 GUI 线程启动后执行；
   - 或在独立线程中等待 `window.events.loaded` / `shown` 后再注册（实测 sleep 3s 也够，但事件驱动更稳）。
   注意现有 `app.py:106` 的调用是 `webview.start(apply_native_window_icon, (window,), gui="cocoa")`，需要扩展该回调。
2. **回调**：注册 `.on('drop', handler)` 后，handler 在**独立线程**执行，从 `event["dataTransfer"]["files"]` 取 `pywebviewFullPath`。要注意 pywebview 会把匹配过的条目从 `_dnd_state['paths']` 移除，**多次拖拽不会重复消费**。
3. **路径规范化**：见第 5 节，必须处理尾随斜杠。
4. **前端**：复用 `web/js/session.js:125` 的 `chooseProject()` 流程（choose-folder → project/open → showProject → startScan）。拖入场景下应跳过「choose-folder」这一步，直接用已解析的路径调 `/api/project/open` + `/api/scan`。
5. **边界行为（用户已确认）**：
   - 首页空态拖入文件夹 → 打开项目 + 自动扫描（等同点「选择照片文件夹」）
   - 拖入非文件夹（图片文件）→ 提示「只支持文件夹」
   - 已有项目打开时拖入 → 提示「请先返回首页」
6. **样式**：拖拽高亮反馈加在 `web/css/home.css`。
