# 006 P0.7：引用导出（BibTeX、RIS）

- 状态：已实现，验收证据见第 7 节（READ-10 仍为进行中：只有 BibTeX 与 RIS，没有 CSL 格式化文本）
- 需求映射：`READ-10`（引用导出）
- 依赖：[001](./001-p0-workspace-paper-import.md)（文献与元数据）、[002](./002-p0-literature-search.md)（DOI、arXiv ID、来源标注）；决策新增 D-020

## 1. 问题、目标、范围

**问题**：文献在系统里管理好了，写论文时还得手工把标题、作者、DOI 抄进参考文献管理工具。

**目标**：成员可以把项目里的文献导出成 BibTeX 或 RIS 文件，导入 Zotero、EndNote、LaTeX 都能被读出同样的字段。导出只用库里已有的元数据，缺的字段就缺，不编造；文献里的文字再古怪，也不能破坏导出文件的结构。

**范围内**

- 一个只读接口 `GET /projects/{id}/papers/export`，格式 `bibtex` 或 `ris`，可选只导出指定文献
- 项目页文献面板上的两个导出按钮
- 引用键（cite key）的生成与去重
- 转义：文献文字不能改变文件结构

**非目标**

| 不做 | 原因与去向 |
| --- | --- |
| 在线调用 CSL 引擎，渲染成 APA、GB/T 7714 等格式的参考文献文本 | 需要样式库与大量样式文件，且结果要和期刊要求逐条核对；先交付参考文献管理工具能读的交换格式 |
| 区分期刊论文、会议论文、预印本等条目类型 | 库里没有「条目类型」字段，来源也不总给出；从 `venue` 猜类型会猜错。统一用通用类型，见 D-020 |
| 页码、卷期、出版社、编辑等字段 | 库里没有这些字段，不编造 |
| 把 BibTeX 导入回系统 | 导入入口是 DOI、arXiv ID 和 PDF（P0.3）；批量导入另行设计 |
| 导出笔记、批注 | 不是引用信息；笔记含未公开的想法，不应随引用文件流出 |
| 勾选部分文献的界面 | 接口支持 `paper_id`，界面本切片只做整个项目；多选是后续的交互增强 |
| 按 LaTeX 规则把 Unicode 转成 `\'e` 之类的命令 | 文件按 UTF-8 输出，现代工具（biblatex、Zotero）直接读；老的 BibTeX 需要自行转换，写入已知限制 |

## 2. 用户场景与行为

### 2.1 权限

任何工作区成员（含 viewer）可以导出，因为导出的内容本来就能在项目页看到。非成员 404。归档项目可导出。不写事件：这是只读操作，没有改动任何数据。

### 2.2 字段映射

| 库里的字段 | BibTeX | RIS |
| --- | --- | --- |
| 条目类型 | `@misc`（统一） | `TY  - GEN` |
| `title` | `title = {{…}}`（双层花括号，保持原有大小写） | `T1` |
| `authors`（列表，按原顺序） | `author = {甲 and 乙}` | 每人一行 `AU` |
| `year` | `year` | `PY` |
| `venue` | `howpublished` | `T2` |
| `doi` | `doi` | `DO` |
| `arxiv_id` | `eprint` 与 `archiveprefix = {arXiv}` | `AN`（编号），并写入 `UR` |
| 链接 | `url`：有 DOI 用 `https://doi.org/<doi>`，否则有 arXiv ID 用 `https://arxiv.org/abs/<id>`，否则不写 | `UR`，同左 |
| `abstract` | `abstract` | `N2` |

链接只由已校验过的 DOI 与 arXiv ID 拼出（DOI 部分做百分号编码），不使用来源返回的任意网址，所以不会出现 `javascript:` 之类的链接。

### 2.3 缺失字段

字段缺失就整行不写，不写空值，也不写「Unknown」「None」之类的占位。`authors` 为空则没有作者行。没有 `title` 的文献（例如只上传了没有元数据的 PDF）**不导出**：没有标题的引用对参考文献工具没有用，也不能用文件名或编号冒充标题。被跳过的数量在响应头 `X-Export-Skipped` 里给出，界面据此提示。全部被跳过（或项目没有文献）时返回 422 `nothing_to_export`，不返回空文件。

### 2.4 引用键

BibTeX 的键由第一作者姓、年份、标题第一个有意义的词拼成，如 `vaswani2017attention`：

- 作者姓：作者字符串含逗号时取逗号前，否则取最后一个空白分隔的词；没有作者用 `anon`；
- 年份缺失用 `nd`；标题词取标题中第一个由字母数字组成的、长度不少于 3 的词（不做停用词过滤，`The Attention` 得到 `the`），没有则用 `paper`；
- 全部转为 ASCII 小写字母和数字（先做 Unicode 归一化去掉重音，无法转换的字符丢弃）；
- 同一次导出里重复时，后面的依次加 `a`、`b`、`c`…（`vaswani2017attentiona`），顺序按导出顺序，所以同样的数据每次导出结果相同。

RIS 没有引用键。

### 2.5 排序与范围

导出顺序同项目文献列表：创建时间新的在前。`paper_id` 可重复传入以只导出指定的几篇，顺序仍按创建时间。有任何一个 `paper_id` 不属于本项目，整个请求 404，不导出其余的（不泄露其他项目的存在）。最多 500 篇；超出时 422（`too_many`），要求用 `paper_id` 分批。

### 2.6 转义与结构安全

文献的标题、作者、摘要来自第三方（PDF 元数据、Crossref 等），可能含 `}`、`@`、换行、`ER  -` 这类会破坏文件结构的内容。

- 所有文字先把连续空白（含换行、制表符）折成一个空格，去首尾空白；去掉除此之外的控制字符。
- BibTeX：对 `\`、`{`、`}`、`&`、`%`、`$`、`#`、`_`、`^`、`~` 加转义。花括号写成 `\textbraceleft{}`、`\textbraceright{}`，反斜杠、`^`、`~` 写成 `\textbackslash{}`、`\textasciicircum{}`、`\textasciitilde{}`，其余在前面加反斜杠。不用 `\{` 和 `\}`：经典 BibTeX 数花括号时不认反斜杠转义，会把记录提前结束。转义后原文里的花括号不再出现，所以文件里的花括号始终成对，一条记录不会提前结束或带出新记录。
- `eprint` 只来自校验过的 arXiv ID，字符集安全。`doi` 的校验只要求以 `10.` 开头且没有空白，所以 DOI 里可能有 `{`、`}`、`\`：BibTeX 里含这三个字符的 DOI 不写 `doi` 字段（RIS 的 `DO` 一行不受影响），拼 `url` 时对 DOI 做百分号编码。
- RIS：折成一行后，值里不会再有换行，所以不可能出现新的 `XX  - ` 行或 `ER  - `。

## 3. 数据与接口

不新增表。

### 3.1 `GET /api/v1/projects/{id}/papers/export?format=bibtex|ris&paper_id=…`

- `format` 必填，只接受 `bibtex`、`ris`，其他 422。
- 200，正文是文本，UTF-8。BibTeX：`Content-Type: application/x-bibtex; charset=utf-8`，`Content-Disposition: attachment; filename="references.bib"`。RIS：`application/x-research-info-systems; charset=utf-8`，`filename="references.ris"`。
- 响应头 `X-Export-Count`（导出条数）、`X-Export-Skipped`（因无标题跳过的条数）、`X-Content-Type-Options: nosniff`。
- RIS 用 CRLF 换行，BibTeX 用 LF。
- 路径 `/papers/export` 必须注册在 `/papers/{paper_id}` 之前，否则 `export` 会被当成非法的 ID。

**新增错误码**：`nothing_to_export`（422）、`too_many`（422）。其余沿用 `not_found`、`validation_error`。

### 3.2 示例

```bibtex
@misc{vaswani2017attention,
  title = {{Attention Is All You Need}},
  author = {Ashish Vaswani and Noam Shazeer},
  year = {2017},
  howpublished = {NeurIPS},
  eprint = {1706.03762},
  archiveprefix = {arXiv},
  url = {https://arxiv.org/abs/1706.03762},
}
```

```ris
TY  - GEN
T1  - Attention Is All You Need
AU  - Ashish Vaswani
AU  - Noam Shazeer
PY  - 2017
T2  - NeurIPS
AN  - 1706.03762
UR  - https://arxiv.org/abs/1706.03762
ER  - 
```

## 4. 验收标准

后端用例同样用真实 PostgreSQL，不用 mock；导出的文件用独立的解析库（`bibtexparser`、`rispy`，仅开发依赖）读回来比对，而不是和实现用同一段代码核对。

- E1 Given 非成员，Then 404；viewer、member 都可以导出；归档项目可导出。
- E2 Given 字段齐全的文献，When 导出 BibTeX 并用 `bibtexparser` 读回，Then 条目类型是 `misc`，标题、作者（顺序不变）、年份、DOI、eprint、url、摘要与库里一致。
- E3 Given 同一批文献，When 导出 RIS 并用 `rispy` 读回，Then `type_of_reference` 为 `GEN`，标题、作者（顺序不变）、年份、DOI、url、摘要一致，条数相同。
- E4 Given DOI 含 `{`、`}` 或 `\`，Then BibTeX 不写 `doi` 字段、`url` 里是编码后的 DOI，文件结构完好，RIS 的 `DO` 保留原值。Given 标题、作者、摘要含 `}`、`{`、`@article{x,`、换行、`ER  - `、`& % _ $ # \ ^ ~`、中文和带重音的字母，Then 导出文件里的条数不变，读回的值与（折叠空白、解转义后的）原文相同，没有多出的条目或字段。
- E5 Given 缺少年份、作者、DOI 的文献，Then 对应字段整行不出现，文件里没有 `None`、`Unknown`、`{}`。Given 没有标题的文献，Then 被跳过，`X-Export-Skipped` 正确，`X-Export-Count` 为实际条数；全部被跳过或项目没有文献，Then 422 `nothing_to_export`。
- E6 Given 重名的第一作者、年份和标题词，Then 引用键在一次导出中互不相同、只含 `a-z0-9`、重复导出结果完全相同；缺作者、缺年份、非 ASCII 姓名各有合法的键。
- E7 Given `paper_id` 过滤，Then 只导出指定的；含别的项目的文献 ID 则 404，不导出任何内容；`format` 缺失或非法、`paper_id` 不是 UUID，Then 422；超过 500 篇的请求 422 `too_many`（用直接写库造出的数据）。
- E8 Given 另一个项目、另一个工作区的文献，Then 一概不出现在导出里。
- E9 Given 导出响应，Then `Content-Type`、`Content-Disposition`、`X-Content-Type-Options`、两个计数头与 3.1 一致；RIS 为 CRLF。
- E10 Given 来源里带有 `javascript:` 之类的链接，Then 导出里的链接只来自 DOI 与 arXiv ID。

**界面（真实浏览器）**

- X1 Given 项目里有文献，When 点「导出 BibTeX」，Then 浏览器下载 `references.bib`，内容含该文献标题。点「导出 RIS」同理。
- X2 Given 没有文献的项目，Then 两个导出按钮不可用，并说明原因；Given 只有无标题文献，When 导出，Then 界面显示服务端返回的错误，不下载空文件。
- X3 Given viewer，Then 可以导出。Given 标题含 `<img onerror>` 或 `}`，Then 下载的文件按文本含有它，页面没有生成元素。

## 5. 技术决策与风险

决策见 [DECISIONS.md](./DECISIONS.md) 的 D-020。

| 风险 | 处理 |
| --- | --- |
| 文献里的文字破坏文件结构（多出条目、提前结束） | 折空白、转义，用例 E4 用独立解析库读回 |
| 猜错条目类型，参考文献里把会议论文标成期刊文章 | 统一用通用类型，宁可不精确也不错误 |
| 用空值或占位符冒充缺失字段，被工具原样印进参考文献 | 缺失字段整行不写，用例 E5 |
| 链接指向任意第三方网址 | 链接只由校验过的 DOI、arXiv ID 拼出，用例 E10 |
| 导出泄露笔记等内部内容 | 只导出文献元数据，不含笔记、批注、任务 |
| 引用键每次不同，论文里的 `\cite` 失效 | 键由数据确定地生成；但库里的元数据一改，键会随之变，写入已知限制 |

**未验证的假设**：Zotero、EndNote、Overleaf 实际导入这些文件的表现没有验证，只用 `bibtexparser` 与 `rispy` 读回过；没有用 `bibtex` 或 `biber` 实际编译过参考文献。

## 6. 实施计划与完成定义

1. 后端：`GET /projects/{id}/papers/export`，注册在 `/papers/{paper_id}` 之前。
2. 后端用例 E1 至 E10，含可失败性检查。
3. 前端：文献面板上的两个导出按钮，错误显示。
4. Playwright X1 至 X3。
5. 文档：README、映射表、本节证据。

**完成定义**：第 4 节每一条有自动化证据；`bash scripts/check.sh` 与 `scripts/e2e.py` 通过；READ-10 标「进行中」或「完成」取决于证据：本切片只做 BibTeX 与 RIS，没有 CSL 格式化文本，所以标「进行中」，并写明缺什么。

## 7. 验收证据

记录日期 2026-11-01，本机 macOS arm64，uv，Node 24，Python 3.12，内嵌 PostgreSQL。

### 7.1 自动检查

| 命令 | 结果 |
| --- | --- |
| `bash scripts/check.sh` | 退出码 0，`all checks passed`。secrets 139 个文件 0 问题；需求映射 152 个 ID 0 问题；ruff、mypy（38 个源文件）通过；pytest（含迁移漂移检查）300 passed、12 skipped（10 个 Docling、2 个在线契约，均未设对应环境变量）；前端类型检查与构建通过 |
| `backend/.venv/bin/python scripts/e2e.py` | 51 passed，其中本切片 3 个（`web/e2e/export.spec.ts`），其余 48 个无回归 |

### 7.2 标准与证据

后端用例在真实 PostgreSQL 和真实生成的 PDF 上，没有 mock；导出的文件用 `bibtexparser`、`rispy` 读回。文件 `backend/tests/test_citations.py`（16 个用例）。

| 标准 | 证据 |
| --- | --- |
| E1 | `test_e1_members_export_and_strangers_do_not`、`test_e1_an_archived_project_can_still_be_exported` |
| E2 | `test_e2_bibtex_reads_back_with_every_field` |
| E3 | `test_e3_ris_reads_back_with_every_field` |
| E4 | `test_e4_hostile_text_cannot_end_a_record_or_start_another`、`test_e4_a_doi_with_braces_does_not_break_bibtex` |
| E5 | `test_e5_missing_fields_are_left_out_and_titleless_papers_are_skipped`、`test_e5_nothing_to_export_is_an_error_not_an_empty_file` |
| E6 | `test_e6_cite_keys_are_unique_plain_and_stable`、`test_e6_a_suffix_never_lands_on_another_papers_own_key` |
| E7 | `test_e7_paper_ids_choose_and_a_foreign_id_refuses_everything`、`test_e7_bad_parameters_are_422`、`test_e7_more_than_the_limit_is_refused`（直接写库造 501 篇） |
| E8 | `test_e8_other_projects_and_workspaces_never_leak_in` |
| E9 | `test_e9_headers_and_line_endings` |
| E10 | `test_e10_links_are_built_from_identifiers_only` |
| X1 至 X3 | `web/e2e/export.spec.ts` 的 3 个用例，名称以 X1…X3 开头 |

**可失败性检查**：逐个改坏 `citations.py` 与导出接口，18 处改动中最初 17 处被抓住。第 18 处「引用键不去重」（去掉碰撞检查的循环）最初没有被抓住：原有用例里 `a` 后缀从未落在另一篇文献自己的键上。为此新增 `test_e6_a_suffix_never_lands_on_another_papers_own_key`，改动后再次检查已被抓住。每次改动后已还原。

**过程中发现并修复的缺陷**：写测试时发现的都是测试自身的问题（引用键期望值写错、花括号计数忽略了 `\textbackslash{}` 本身带花括号）；实现没有因此改动。浏览器测试 X1、X3 的失败原因是测试辅助函数在阅读器页面载入完成前就填表，输入被页面随后的重建冲掉，与导出功能无关；改为等页面载入后再填。

### 7.3 未验证与已知限制

- Zotero、EndNote、Overleaf 实际导入这些文件的表现没有验证，只用 `bibtexparser` 与 `rispy` 读回；没有用 `bibtex` 或 `biber` 实际编译过参考文献。
- 没有 CSL 格式化文本（APA、GB/T 7714 等）；条目类型统一为 `@misc` / `GEN`。
- 界面只导出整个项目；接口支持 `paper_id`，但没有勾选部分文献的界面。
- 引用键由元数据生成，元数据一改，键会随之变化，论文里已有的 `\cite` 可能失效。
- 文件按 UTF-8 输出，老的 BibTeX 需要自行把 Unicode 转成命令。
- 一次最多 500 篇；超出需要用 `paper_id` 分批。
- e2e 只在 Chromium 上跑过。
