# 上游候选验证记录

对应 PROMPT 阶段 0 要求。原始脚本与输出在 `.spike/`（被 git 忽略），本文件是整理后的结论。固定版本同步写在仓库根的 `third_party.lock.yaml`。

- 首次实测：2026-10-30。本次复测与许可证核对：2026-10-31。
- 环境：macOS 24.6 arm64，Python 3.12.16，无 Docker，无 GPU。
- 样例：`samples/pdfs/1706.03762.pdf`（Attention Is All You Need，15 页，2.2 MB，不入库）；生成的旋转与裁剪 PDF（`.spike/docling/rot/`，不入库）。

## 结论一览

| 候选 | 状态 | 已验证 | 未验证 |
| --- | --- | --- | --- |
| Docling | 可选解析器，已有适配器与测试 | 固定版本；真实论文跑通；旋转与裁剪页坐标与渲染墨迹一致；代码与模型许可证 | 冷启动耗时；OCR 路径；表格与公式质量；与 pdfium 的块级对比；环境安装脚本 |
| PaperQA2 | 候选 | PyPI 版本与 Apache-2.0 许可证 | 未安装、未运行。问答质量、引用定位、成本均未知 |
| PaperAgent | 阻塞 | 仓库无 LICENSE（D-010） | 不下载、不运行 |
| Presenton | 阻塞 | README 声明 Apache-2.0，LICENSE 文件存在 | 未运行（需 Docker，本机没有）；未固定版本；PPTX 可编辑性、中文、溢出均未验证 |
| OpenAlex / Crossref / arXiv | 接口形状已实测 | 响应字段、OpenAlex 额度（D-009） | 限流与重试行为、中文查询质量 |

## Docling

### 版本与许可证

| 组件 | 版本 | 许可证 | 来源 |
| --- | --- | --- | --- |
| docling | 2.145.0 | MIT | 已安装包元数据 |
| docling-core | 2.105.0 | MIT | 同上 |
| docling-ibm-models | 4.0.4 | MIT | 同上 |
| docling-parse | 7.24.2 | MIT | 同上 |
| torch | 2.15.0 | Apache-2.0 等（元数据为多项许可证组合） | 同上 |
| rapidocr | 3.10.0 | 未核对 | |
| 模型 docling-project/docling-layout-heron | 8f39ad3c0b4c | Apache-2.0 | Hugging Face 模型卡与 API |
| 模型 docling-project/docling-models | fc0f2d45e221（本地快照） | CDLA-Permissive-2.0 | 本地模型卡 |
| 模型 PaddlePaddle/PP-OCRv6_medium_det、PP-OCRv6_medium_rec、PP-LCNet_x1_0_textline_ori | 本地缓存只有 `refs/main`，没有 snapshot 和权重文件；修订号见锁文件 | Apache-2.0（Hugging Face API 报告，未读模型卡） | Hugging Face API |

复测关闭了 OCR（`do_ocr = False`），所以 Paddle OCR 模型没有参与本次结果。它们是 OCR 路径的依赖，该路径未验证，权重文件是否会被下载、实际由哪个库加载，也没有确认。

`docling-models` 的 Hugging Face API 当前返回 `['cdla-permissive-2.0', 'apache-2.0']`，且 API 的 sha（2199320848bb）与本地快照（fc0f2d45e221）不同，说明本地不是最新版。锁文件记录的是本地实际使用的快照。

### 实测

命令：`HF_HUB_OFFLINE=1 .spike/docling/.venv/bin/python .spike/docling/spike2.py .spike/docling/rot samples/pdfs/1706.03762.pdf`，退出码 0。

- 真实论文（关闭 OCR，模型已缓存）：`ConversionStatus.SUCCESS`，15 页，4 张表，7 张图，**4.9 秒**。这是热缓存数字，冷启动含模型加载与下载的耗时没有测。
- 坐标：Docling 的 provenance 框原点为左下，已经应用页面旋转与裁剪。下表是把框归一化后与 pdfium 渲染出的墨迹包围盒比较（x 与 y 均为页宽、页高的比例）。

| 样例 | 渲染墨迹 (x0, y0, x1, y1) | Docling 归一化后的块并集 | 最大偏差 |
| --- | --- | --- | --- |
| plain | 0.150, 0.142, 0.864, 0.237 | 0.150, 0.143, 0.867, 0.237 | 0.003 |
| crop | 0.083, 0.260, 0.876, 0.457 | 0.083, 0.261, 0.880, 0.457 | 0.004 |
| rot90 | 0.762, 0.150, 0.858, 0.865 | 0.763, 0.150, 0.857, 0.867 | 0.002 |
| rot180 | 0.135, 0.762, 0.850, 0.858 | 0.133, 0.763, 0.850, 0.857 | 0.002 |
| rot270 | 0.142, 0.135, 0.237, 0.850 | 0.143, 0.133, 0.237, 0.850 | 0.002 |

偏差来自字形包围盒与行高的差异，不是坐标系错误。pdfium 解析器的同类检查是 `backend/tests/test_parsing.py::test_c2_bbox_matches_rendered_ink`（8 种组合）。适配器的自动化测试是 `backend/tests/test_docling_adapter.py`（10 个用例：旋转 0/90/180/270 与裁剪的坐标对比墨迹、真实论文、损坏 PDF 返回错误而非空结果）。设置 `TEST_DOCLING_PYTHON` 为 Docling 环境的 python 才会运行，否则跳过并提示未测试。2026-10-31 在本机 10 个全部通过，用时 67 秒；容差改为 0.0001 后坐标用例失败，说明测试有效。

### 与默认流程的关系

按 D-002，默认解析器仍是 pdfium。Docling 在独立 Python 环境里运行（环境约 1.3 GB，模型缓存约 633 MB），由 `DOCLING_PYTHON` 启用。启用它之前，需要补一个能在 CI 之外手动运行的对比检查，并解决安装脚本缺失的问题。

## PaperQA2

- PyPI `paper-qa` 2026.8.12，要求 Python >= 3.11。
- GitHub `Future-House/paper-qa` 的 `main` 分支有 LICENSE 文件，内容为 Apache License 2.0，版权方 FutureHouse。
- 没有安装，没有运行。是否适合作为问答引擎，取决于它的引用定位能否对应到本项目的页码与块 ID，这需要在 P0.5 的规格里先设计评估样例再验证。
- 状态：**候选**。

## PaperAgent

- 2026-10-30 核查：`songhahaha66/PaperAgent` 的 `master` 与 `main` 分支均没有 LICENSE 文件（raw 地址 404，本次 2026-10-31 复查结果相同）。
- 无许可证即没有使用、修改、分发的授权。不下载、不运行、不复制代码。见 D-010。
- 状态：**阻塞**。P1.4 默认走开发文档第 6 节的组装路线。

## Presenton

- GitHub `presenton/presenton` 的 `main` 分支有 LICENSE 文件，内容为 Apache License 2.0；README 的许可证徽章也是 Apache 2.0。
- README 称支持 Docker、Windows、macOS、Linux，可导出可编辑 PPTX。这是上游自述，没有在本项目验证。
- 本机没有 Docker，没有运行。2026-10-31 查询 GitHub API 时被限流，没有取得最新发布版本号，因此**版本未固定**。
- 状态：**阻塞**。要在 P1.5 之前找到能运行 Docker 的环境，或评估文档指定的替代路线。

## 文献检索接口

样例响应保存在 `.spike/{arxiv,openalex,crossref}*.{xml,json}`，于 2026-10-30 获取。

- OpenAlex：无 key 时共享每日 0.10 美元预算（见 D-009）。样例响应里的 `meta.notice` 有说明。
- Crossref：返回 `message.items[]`，字段 `DOI`、`title[]`、`author[]`、`issued.date-parts`；记录可能缺作者、缺年份。
- arXiv：Atom XML，`opensearch:totalResults` 给出命中数。
- 未测：限流响应、超时、重复 DOI、中文查询的实际质量（有 `*-zh` 样例文件，未整理结论）。这些在 P0.3 规格里作为验收项处理。

## 复现方式

```bash
# Docling 环境在 .spike/docling/.venv（被忽略，需自行重建）
HF_HUB_OFFLINE=1 .spike/docling/.venv/bin/python .spike/docling/spike2.py .spike/docling/rot samples/pdfs/1706.03762.pdf
```

重建环境的命令没有脚本化，这是已知缺口。
