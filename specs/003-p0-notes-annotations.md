# 003 P0.4（第一部分）：笔记与带来源的批注

- 状态：已实现，验收证据见第 7 节（READ-08 仍为进行中：任意区间高亮未做）
- 需求映射：`READ-08`（高亮批注与阅读笔记，部分：笔记与块级批注）。`READ-06`、`READ-11`、`READ-12` 在本切片**不做**，见非目标
- 依赖：[001](./001-p0-workspace-paper-import.md)（块与内容哈希）、[002](./002-p0-literature-search.md)（无 PDF 文献）；决策 D-016、D-017

## 1. 问题、目标、范围

**问题**：用户能读 PDF、点块定位，但读完无处记录。想法留在别处，就丢了与原文的联系；重新解析或换版本之后，也说不清某条想法针对的是哪一段原文。

**目标**：用户可以在项目里记笔记。笔记可以只属于项目，可以属于一篇文献，也可以锚定到某个版本的某个文本块。锚定的笔记点击后跳到原文并高亮；重新解析后若那段原文变了，界面如实说「原文位置已变化」并保留当时引用的原文，不假装仍然对得上。没有来源的笔记明确标「无来源」。

**范围内**

- 笔记的创建、列表、编辑、删除，三种归属：项目、文献、文本块
- 块锚点：页码、原文快照、内容哈希、坐标，由服务端从块取得
- 重新解析后锚点的重新定位与失效判断
- 乐观并发（两个人同时改一条笔记）
- 阅读页的笔记栏、项目页的笔记面板

**非目标**

| 不做 | 原因与去向 |
| --- | --- |
| 段落翻译、核心贡献总结、动机分析（READ-06、11、12） | 需要模型，本仓库没有配置任何模型；把论文原文发给第三方模型涉及隐私和费用，是需要用户决定的事项，见 D-017。在决定之前不写模型适配器，不留假实现 |
| PDF 页面上框选任意文字的高亮 | 现有定位单位是文本块（D-007）；块内任意区间的坐标没有测量依据。本切片的批注就是块级 |
| 富文本（Tiptap）、Markdown 渲染 | 正文按纯文本保存和显示，换行保留；富文本有 XSS 面和导出格式问题，待有需求再设计 |
| 私有笔记、按笔记授权 | 权限在工作区一级（D-003）：能看项目的人能看全部笔记。这是已知限制 |
| 笔记搜索、标签、导出 | 后续 |
| 笔记关联任务 | 随 P0.5 |

## 2. 用户场景与行为

### 2.1 权限

| 操作 | viewer | member | admin / owner |
| --- | --- | --- | --- |
| 查看笔记 | ✓ | ✓ | ✓ |
| 新建笔记 | 403 | ✓ | ✓ |
| 编辑笔记正文 | 403 | 仅作者 | 仅作者 |
| 删除笔记 | 403 | 仅作者 | ✓（任何人的） |

非成员 404。归档项目可读，写操作 409 `project_archived`。作者离开工作区后其笔记保留，只有 admin/owner 能删。编辑只限作者，是因为管理员改写他人的话没有合理场景，删除则是清理需要。

### 2.2 三种笔记

| 种类 | 条件 | 界面标记 |
| --- | --- | --- |
| 项目笔记 | 无 `paper_id` | 「项目笔记」 |
| 文献笔记 | 有 `paper_id`，无锚点 | 「无来源」：不指向原文的某处 |
| 批注 | 有锚点 | 引用的原文、页码、版本；状态见 2.3 |

### 2.3 锚点与状态

创建批注时客户端只提交 `version_id` 和 `block_id`。服务端检查块属于该版本**当前**解析任务，再自己取 `page_no`、原文、`content_hash`、`bbox`、`granularity` 存成快照。客户端不能提交引用文字，所以无法伪造「原文说过什么」。

读取时按版本当前解析任务的块重新定位，状态由服务端算出：

| `status` | 判断 | 界面 |
| --- | --- | --- |
| `located` | 当前解析里，同页有 `content_hash` 相同的块 | 可点击跳转并高亮，使用**当前**块的 ID 与坐标 |
| `stale` | 版本已解析，但同页没有哈希相同的块（重新解析后原文不同） | 显示快照与「原文位置已变化，下面是记录时的引用」，不跳转 |
| `unparsed` | 该版本当前没有解析结果（解析中、失败或被取消） | 显示快照与「该版本当前没有解析结果」，不跳转 |

同页同哈希有多个块时（重复文字）取 `seq` 最小的，且 `located` 只表示「这页有同样的文字」。这个限制写入已知限制。

笔记始终绑定创建时的版本。上传新版本后，旧版本的批注不迁移到新版本，列表里标「来自 v1」，点击在旧版本里定位。

### 2.4 编辑与并发

`PATCH` 需要 `expected_version`，不一致返回 409 `version_conflict`，响应不改变数据；界面提示「内容已被他人修改，请刷新后再试」并保留用户输入。锚点不可修改。正文不能改成空（批注除外，允许空正文，只是一个标记）。

### 2.5 删除文献

删除文献时一并删除其全部笔记（批注依赖版本，版本被删后没有意义）。界面确认框写明「同时删除这篇文献的笔记」。事件 `paper.deleted` 的载荷带 `note_count`。这是不可恢复的数据删除，沿用现有确认框，不另做回收站。

## 3. 数据与接口

### 3.1 表 `notes`（迁移 `0003`）

| 列 | 说明 |
| --- | --- |
| `id`、`workspace_id`、`project_id` | 与其他对象一致，`project_id` 有索引 |
| `paper_id` | 可空，外键 `papers.id`。空即项目笔记 |
| `version_id` | 可空，外键 `paper_versions.id`。有锚点时必填 |
| `author_id` | 外键 `users.id` |
| `body` | 文本，上限 20000 个字符，数据库 CHECK 与接口校验各一份 |
| `anchor` | 可空 JSONB：`page_no`、`text`、`content_hash`、`bbox`、`granularity`、`block_seq`。创建时由服务端写入，之后不可改 |
| `version` | 整数，从 1 起，每次编辑加 1，供乐观并发 |
| `created_at`、`updated_at` | 时间戳 |

约束：`anchor` 非空要求 `version_id` 和 `paper_id` 非空；`version_id` 非空要求 `paper_id` 非空；批注的 `body` 允许为空串，其余种类 `body` 不能为空（CHECK）。索引 `(project_id, paper_id, created_at)`。

`anchor.text` 是创建时块的原文快照，上限沿用块文本的长度，不再截断。它是第三方 PDF 的内容，渲染时只当文本。

### 3.2 API（前缀 `/api/v1`，嵌套在项目下）

| 方法与路径 | 说明 | 角色 |
| --- | --- | --- |
| `GET /projects/{id}/notes?paper_id=&limit=&offset=` | 按创建时间倒序；`paper_id` 过滤某篇文献，缺省为整个项目；`limit` 1–200，默认 100 | viewer |
| `POST /projects/{id}/notes` | 创建，201 | member |
| `PATCH /projects/{id}/notes/{note_id}` | 改正文，需要 `expected_version` | 作者 |
| `DELETE /projects/{id}/notes/{note_id}` | 删除，204 | 作者或 admin/owner |

**创建请求**（`extra=forbid`）

```json
{"body": "这里的符号和第 3 节不一致",
 "paper_id": "…",
 "anchor": {"version_id": "…", "block_id": "…"}}
```

`paper_id` 省略即项目笔记（此时不能带 `anchor`）；`anchor` 要求 `paper_id`；`version_id` 必须属于该文献，`block_id` 必须属于该版本当前的解析任务，否则 422 `invalid_anchor`。文献或版本不在这个项目里按 404 处理（不泄露其他项目的存在）。`body` 去首尾空白后 1–20000 个字符（批注可为空），不含除换行和制表符以外的控制字符。

**笔记响应**

```json
{"id": "…", "kind": "annotation", "paper_id": "…", "paper_title": "…",
 "author_id": "…", "author_name": "…", "body": "…", "version": 1,
 "editable": true, "deletable": true,
 "anchor": {"version_id": "…", "version_no": 1, "page_no": 3, "text": "原文快照",
            "status": "located", "block_id": "当前块 ID，仅 located",
            "bbox": [0.1, 0.2, 0.9, 0.3], "granularity": "block"},
 "created_at": "…", "updated_at": "…"}
```

`kind` 为 `project`、`paper`、`annotation`。`anchor.bbox` 与 `block_id` 在 `located` 时来自当前块，其余状态为 null。`editable`、`deletable` 由服务端按调用者算出，前端只据此显示按钮，不自己推断权限。`paper_title` 取文献当前标题，可为 null。

**新增错误码**：`invalid_anchor`（422）、`note_too_long`（422，也可用现有 `validation_error`，以实现为准并在 7 节写明）。其余沿用 `version_conflict`、`forbidden`、`not_found`、`project_archived`。

### 3.3 事件与现有接口的改动

- 事件 `note.created`、`note.updated`、`note.deleted`，载荷 `{"kind": …, "paper_id": …}`，**不含正文和原文快照**。
- `DELETE /papers/{id}` 先删该文献的笔记，再删版本；`paper.deleted` 载荷加 `note_count`。
- 前端 `EVENT_LABELS` 补三条文案。

## 4. 验收标准

后端用例同样用真实 PostgreSQL，不用 mock。

**权限与归属**

- K1 Given viewer，When 创建、编辑、删除，Then 403；查看可以。非成员 404。归档项目可读，写操作 409。
- K2 Given member A 的笔记，When member B 编辑，Then 403；B 删除也 403；admin 删除成功；A 删除成功。
- K3 Given 别的项目的 `paper_id`、`version_id` 或 `block_id`，When 创建，Then 404 或 422，不创建任何记录；另一个项目的笔记在本项目列表里看不到。
- K4 Given 请求带自拟的 `text`、`page_no`、`content_hash`、`author_id`，Then 422，没有字段被采用。

**锚点**

- K5 Given 有解析结果的版本，When 对一个块创建批注，Then 响应里的原文、页码、坐标等于块的值，`status=located`，`block_id` 为该块。
- K6 Given 版本没有解析结果，When 以任意 `block_id` 创建，Then 422 `invalid_anchor`。给无 PDF 文献的 `version_id` 同样。
- K7 Given 重新解析后某页文字不变，Then 批注仍是 `located`，且 `block_id`、`bbox` 是新任务里的块；Given 该页文字变了，Then `stale`，快照原样保留，`block_id` 与 `bbox` 为 null；Given 解析进行中或失败，Then `unparsed`。（测试需让解析器输出可控：用不同内容的 PDF 版本和现有重解析接口，不 mock。）
- K8 Given 上传新版本，Then 旧版本的批注不迁移，`version_no` 仍是旧版本号，仍可定位。
- K9 Given 同页两个相同文字的块，Then `located` 取 `seq` 最小的那个。

**编辑与并发**

- K10 Given 两次带相同 `expected_version` 的并发 PATCH，Then 一个成功（版本加 1），另一个 409 `version_conflict`，数据只含成功者的正文。
- K11 Given PATCH 带 `anchor`、`paper_id` 等字段，Then 422，锚点不变。
- K12 Given 正文为空（非批注）、超过 20000 个字符、含控制字符，Then 422，库里没有记录。

**一致性**

- K13 Given 删除文献，Then 其笔记一并删除，其他文献和项目笔记不受影响，事件载荷的 `note_count` 正确。
- K14 Given 事件，Then 载荷里没有正文或原文快照。
- K15 Given 重启后读取，Then 笔记仍在（数据在 PostgreSQL，用例直接重新读库）。

**界面（真实浏览器）**

- L1 Given member 在阅读页，When 选中一个块并写批注，Then 笔记栏出现带页码与原文的条目；刷新后仍在；点击条目跳到该页并高亮对应块。
- L2 Given 文献页，When 写不带来源的笔记，Then 条目显示「无来源」；项目页的笔记面板能看到它并链到文献。
- L3 Given 重新解析后原文变了，Then 条目显示「原文位置已变化」和记录时的引用，点击不跳转；没有 undefined 或空白。
- L4 Given viewer，Then 能看到笔记，没有输入框、编辑和删除按钮。
- L5 Given 两个浏览器同时编辑一条笔记，Then 后保存的一方看到冲突提示且输入没有丢失。
- L6 Given 笔记正文含 `<img onerror>` 之类的标记，Then 页面按文本显示，没有生成元素，没有脚本执行。
- L7 Given 删除文献，Then 确认框写明会同时删除笔记，确认后项目笔记面板里不再有它们。

## 5. 技术决策与风险

决策见 [DECISIONS.md](./DECISIONS.md) 的 D-016、D-017。

| 风险 | 处理 |
| --- | --- |
| 原文变了而批注还指向旧位置 | 锚点存哈希与快照，读取时重新比对；`stale` 时不跳转、不假装对得上 |
| 用户伪造「原文说过」 | 快照只由服务端从块写入，客户端不能提交引用文字 |
| 笔记正文或快照里的标记进入页面 | 一律按文本渲染；不使用 `dangerouslySetInnerHTML` |
| 两人覆盖对方的修改 | `expected_version` 乐观并发，冲突不丢输入 |
| 笔记在工作区内不分私有 | 已知限制，写在界面「项目信息」旁与 README |
| 删除文献连带删笔记 | 确认框明示，事件记录数量；没有回收站是已知限制 |
| 重复文字的块让 `located` 指向错误位置 | 已知限制，见 2.3；界面只展示页码与引用，不声称是唯一位置 |

**未验证的假设**：块哈希在不同解析器（pdfium 与 Docling）之间不可比，换解析器后批注多半变 `stale`，这是预期而非缺陷，但没有用 Docling 验证。

## 6. 实施计划与完成定义

1. 迁移与模型：`notes` 表与约束。
2. 后端：锚点解析、四个接口、事件、删除文献时清理。
3. 后端用例 K1 至 K15，含可失败性检查。
4. 前端：阅读页笔记栏、批注创建、项目页笔记面板、文献页的笔记。
5. Playwright L1 至 L7。
6. 文档：README、映射表、本节证据。

**完成定义**：第 4 节每一条有自动化证据；`bash scripts/check.sh` 与 `scripts/e2e.py` 通过；READ-08 标「进行中」（只完成笔记与块级批注，任意区间高亮未做），READ-06、READ-11、READ-12 保持「未做」并在 D-017 说明原因。

## 7. 验收证据

记录日期 2026-10-31，本机 macOS arm64，uv，Node 24，Python 3.12，内嵌 PostgreSQL。

### 7.1 自动检查

| 命令 | 结果 |
| --- | --- |
| `bash scripts/check.sh` | 退出码 0，`all checks passed`。secrets 122 个文件 0 问题；需求映射 152 个 ID 0 问题；ruff、mypy（35 个源文件）通过；pytest（含迁移漂移检查）239 passed、12 skipped（10 个 Docling、2 个在线契约，均未设对应环境变量）；前端类型检查与构建通过 |
| `backend/.venv/bin/python scripts/e2e.py` | 35 passed，其中本切片 7 个（`web/e2e/notes.spec.ts`），其余 28 个无回归 |

### 7.2 标准与证据

后端用例在真实 PostgreSQL 和真实生成的 PDF 上，没有 mock；文件 `backend/tests/test_notes.py`。

| 标准 | 证据 |
| --- | --- |
| K1 | `test_k1_viewer_reads_but_cannot_write_and_outsider_sees_nothing`、`test_k1_archived_project_is_readable_but_not_writable` |
| K2 | `test_k2_only_the_author_edits_and_authors_or_admins_delete`（含 `editable`/`deletable` 对不同调用者的取值） |
| K3 | `test_k3_other_projects_objects_are_refused_and_never_listed` |
| K4 | `test_k4_the_client_cannot_supply_the_quoted_text_or_the_author`（`text`、`page_no`、`content_hash`、`author_id`、`version` 均 422） |
| K5 | `test_k5_annotation_copies_the_block_and_is_located`（并读库核对 `content_hash`、`block_seq`） |
| K6 | `test_k6_nothing_to_anchor_to_without_a_parse`（无解析、不存在的块、不存在的版本、无 `paper_id` 的锚点） |
| K7 | `test_k7_located_follows_a_reparse_and_goes_stale_when_the_text_changes`、`test_k7_a_version_without_a_current_parse_is_unparsed` |
| K8 | `test_k8_a_new_version_leaves_old_annotations_on_the_old_version` |
| K9 | `test_k9_a_repeated_text_on_one_page_resolves_to_the_first_block` |
| K10 | `test_k10_two_edits_from_the_same_version_cannot_both_win`（两个线程同时 PATCH，结果恰为一个 200 和一个 409） |
| K11 | `test_k11_patch_changes_the_body_and_nothing_else` |
| K12 | `test_k12_invalid_bodies_…`（6 种非法正文，创建与编辑各一次）、`test_k12_limits_are_inclusive_…`（20000 个字符可存）、`test_k12_the_database_refuses_what_the_api_would`（直接写库被 CHECK 拒绝） |
| K13 | `test_k13_deleting_a_paper_removes_its_notes_and_only_those`（`note_count` 为 2 和 1） |
| K14 | `test_k14_events_say_what_happened_without_the_words` |
| K15 | `test_k15_notes_are_read_back_from_the_database`（新会话直接读库） |
| 列表 | `test_listing_filters_by_paper_orders_newest_first_and_pages` |
| L1 至 L7 | `web/e2e/notes.spec.ts` 的 7 个用例，名称以 L1…L7 开头 |

**可失败性检查**：逐个改坏实现，`test_notes.py` 都应失败。结果：去掉 stale 判断、让任何人都能编辑、让任何人都能删除、去掉版本号检查、接受任意解析任务的块、事件载荷带正文、删文献时不删笔记、重复块取最大 `seq`，这 8 处改动各被至少一个用例抓住。第 9 处（去掉 `paper_version_id` 条件）没有被抓住：它与「块属于当前解析任务」重复（任务只属于一个版本），不是独立的防线，保留作为纵深防御。每次改动后已还原。

### 7.3 未验证与已知限制

- **L3 的「原文变了」状态是人为造出来的**：pdfium 解析是确定的，同一个文件重新解析不会改变任何一页，所以浏览器用例通过控制接口改写存下的哈希，来得到「库里记的引用在当前解析里找不到」这个状态；后端 K7 也是同样的做法。真实的「换了解析器或换了文件之后哈希变化」没有验证，Docling 与 pdfium 的哈希是否可比也没有验证。
- `located` 只表示「这页有同样文字的块」，重复文字取 `seq` 最小者（K9），可能指向错误的那一处。
- 块级批注的任意区间由 [011](./011-p0-annotation-ranges.md) 补上（块内字符区间，D-024）。PDF 画布上按区间画高亮仍未做，因为解析结果只有块级坐标，所以 READ-08 仍是「进行中」。
- 笔记在工作区内不分私有；管理员不能改他人的笔记，但能删。
- 没有回收站：删除文献会连带删除它的笔记，界面确认框已写明。
- 点击「跳转」使用笔记响应里带的当前块 ID 和坐标，不再单独查块；若同时有人重新解析，页面上的块列表与笔记状态可能短暂不一致，刷新后一致。
- 笔记列表一次最多取 200 条，没有分页界面。
- `invalid_anchor` 与长度错误：长度、控制字符走 `validation_error`（422），只有锚点问题用 `invalid_anchor`。
- 翻译、核心贡献总结、动机分析（READ-06、11、12）未做，原因见 D-017。
- e2e 只在 Chromium 上跑过。
