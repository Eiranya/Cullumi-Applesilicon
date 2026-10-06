# 历史归档

本目录是**历史记录，不是当前文档**。内容记录了 macOS 移植与照片预览清晰度两个专题的设计、验证与决策过程，其中若干结论已被后续实现取代。

**当前有效的文档请看：**

| 想了解什么 | 看哪里 |
|---|---|
| 功能、系统要求、安装、使用、已知限制 | [`../README.md`](../README.md) |
| 从源码运行、重新打包、应用内更新 | [`../MACOS-使用说明.md`](../MACOS-使用说明.md) |
| 卡片预览为何重制、如何复现测量 | [`../evaluation/preview-resolution/README.md`](../evaluation/preview-resolution/README.md) |

---

## 归档内容与各自的失效点

| 文件 | 内容 | 现状 |
|---|---|---|
| `PORTING-PLAN.md` | 移植计划与实施记录 | **§0「实勘修正」仍有价值**——记录了三条被实测推翻的假设：`proxy_tools`/`bottle` 无法剔除（pywebview 在模块作用域无条件 import）、CoreML execution provider 确实存在、`.AAE` 零命中。其余章节声明的改动尺度（如「`cullumi/` 24 个模块与 `web/` 全部字节不变」）已被后续功能改动超越。 |
| `PERF-REPORT.md` | 扫描性能剖析 | 提出的 3 个优化点（worker 封顶、每 N 张重启、`fast_analysis` 默认值）**均已落地为代码**，见 `cullumi/analysis_worker.py` 与 `cullumi/config.py`。剖析对象是隔离副本，数据不代表当前性能。 |
| `QA-REPORT.md` | 第一轮 QA | 含整节自我纠错记录。对使用者无操作价值。 |
| `QA-REPORT-ROUND2.md` | 第二轮 QA（`PASS_WITH_ISSUES`） | E1–E6 六项均已修复，其中 E1/E4/E5/E6 已演变为 `verify-change-set.sh` 的常驻门禁。**E1 的结论「版本号单一真相源」至今仍成立**（`Cullumi-macos.spec` 动态读取 `cullumi/__init__.py`）。 |
| `DND-TECH-NOTES.md` | 拖放功能的前置验证笔记 | 使命已完成，拖放已实现。 |
| `GPU-VERIFY-NOTES.md` | CoreML 人脸检测的可行性验证 | **§2.1「失败的尝试」值得保留**——手绘人脸峰值 0.287、远低于 0.85 阈值，证明合成样本不可用、必须用真实照片。功能已落地，详见 `cullumi/face_analysis.py` 注释。 |

---

归档于 v1.0.6 发布前。之所以保留而非删除：这些「被实测推翻的假设」与「失败尝试」的记录重跑一次成本很高。