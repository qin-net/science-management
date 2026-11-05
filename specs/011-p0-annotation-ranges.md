# 011 P0.4（续）：块内任意区间批注

- 状态：实现与验收证据见第 7 节
- 需求映射：`READ-08`（高亮批注与阅读笔记，补上 003 列为非目标的"任意区间"一部分）
- 依赖：[003](./003-p0-notes-annotations.md)（块锚点、内容哈希、失效判断）、[001](./001-p0-workspace-paper-import.md)（块与坐标）；决策 D-007、D-016、D-024
- 前置：003 的 K1–K15、L1–L7 已通过，本切片不改变它们的语义

## 1. 问题、目标、范围

**问题**：003 的批注只能锚定整个文本块。块常常是一整段甚至一整页，用户想标的是其中一句，现在只能把整块都标上，引用里也带上一堆无关的话。

**目标**：在阅读页的文本块里选中任意一段文字就能批注。存的是这段文字在块里的位置和当时的那段原文；重新解析后如果块本身没变，这一段仍然对得上；块变了，和 003 一样如实说"原文位置已变化"。

**范围内**

- 块内选区：字符起止位置（`start`、`end`，左闭右开），由浏览器选区换算，服务端只接受数字
- 服务端从块原文截出引用文字（`text[start:end]`），客户端不能提交引用字符串
- 越界、倒序、只有一个端点、空白选区一律 422
- 笔记条目显示区间的原文；重新解析后落在块内的同一位置
- 文本块里用标记显示这条区间

**非目标**

| 不做 | 原因与去向 |
| --- | --- |
| PDF 画布上框选任意文字并按区间画高亮 | 解析结果只有块级坐标（`bbox` 是整块的），没有逐行或逐字的坐标；要按区间画就必须重新测量版面，属于解析器能力，见 D-024。PDF 上的高亮仍是整块 |
| 跨块的选区 | 选区两端必须在同一个块的原文里；跨块要么拆成多条，要么需要复合锚点，后者没有需求推动。界面在跨块时提示"请在同一段内选择" |
| 区间批注的定位不依赖内容哈希 | 与 003 一致：块内容变了，整条批注转 `stale`，区间不单独重定位。子串级别的重定位要模糊匹配，代价与误配风险都高 |
| 多段区间、合并区间、删除区间 | 一条批注一个区间 |
| 富文本、下划线、颜色分类 | 仍是纯文本批注，只有一种标记 |

## 2. 用户场景与行为

### 2.1 权限

与 003 第 2.1 节相同，本切片不新增权限面：viewer 只读，member 可建，作者可改，作者或 admin 可删，归档项目只读，非成员 404。

### 2.2 流程

1. 用户在阅读页左栏的文本块里用鼠标选中一段文字。松开鼠标时，页面把选区换算成块内的字符起止位置。
2. 笔记栏出现按钮"批注所选文字（第 N 页）"，点击后以这段区间创建批注。正文可以留空（纯标记）。
3. 条目显示页码、版本号和区间的原文；文本块里这段原文被标记出来，点条目跳到该页并把这段原文滚动到可见位置。
4. 选区为空、跨块、或与块无关时，按钮不出现（不弹错误，避免打扰）。

### 2.3 状态与失败

| 情况 | 行为 |
| --- | --- |
| 区间落在块内 | `located`，引用是区间的原文，文本块里标记这段 |
| 块的内容哈希在当前解析里找不到 | `stale`，与 003 相同：显示记录时的区间原文，不跳转 |
| 版本当前没有解析结果 | `unparsed`，与 003 相同 |
| 端点越界、倒序、只给一个、负数 | 422 `invalid_anchor`，库里没有记录 |
| 选中的是块外的空白（含块首尾的空白） | 前端允许，服务端按区间原样存；不做额外裁剪，保证"所见即所存" |

## 3. 数据与接口

### 3.1 数据

不新增表、不改迁移。`notes.anchor` 是 JSONB，本切片在其中增加两个键：

```json
{
  "page_no": 1, "text": "整块原文", "content_hash": "sha256:…",
  "bbox": [...], "granularity": "block", "block_seq": 3,
  "range": [12, 30],
  "quote": "区间原文"
}
```

没有 `range` 的旧锚点（003 建的）按整块处理，`quote` 为 null，行为不变。

### 3.2 接口

创建批注的请求体，`anchor` 增加可选的两个字段：

```json
{"body": "", "paper_id": "…", "anchor": {"version_id": "…", "block_id": "…", "start": 12, "end": 30}}
```

响应 `anchor` 增加两个字段：

```json
{"…": "…", "range": [12, 30], "quote": "区间原文"}
```

规则：

- `start`、`end` 必须同时出现或同时不出现，都是整数；`0 <= start < end <= length(text)`。不满足即 422 `invalid_anchor`。
- `quote` 只由服务端从块原文截取，客户端提交 `quote`、`text`、`page_no`、`content_hash` 等字段仍是 422（003 的 K4 继续成立）。
- 列表、详情、编辑、删除的契约不变；`range`/`quote` 只读。

## 4. 验收标准

**后端（真实 PostgreSQL 与真实 PDF）**

- M1 Given member 在块内选 12 到 30 这段，Then 201，`anchor.range` 为 `[12, 30]`，`anchor.quote` 等于块的 `text[12:30]`，`anchor.text` 仍是整块原文，状态 `located`。
- M2 Given 端点越界（`end` 大于原文长度）、倒序（`start >= end`）、负数、只给 `start`、只给 `end`、非整数，Then 422 `invalid_anchor`，库里没有记录。
- M3 Given 客户端在 `anchor` 里提交 `quote`，Then 422，引用的文字只可能来自服务端。
- M4 Given 区间是块的首尾（`0` 到原文长度），Then 201，引用等于整块原文。
- M5 Given 重新解析后块原文没变，Then 同级批注仍 `located`，`range` 与 `quote` 不变。
- M6 Given 重新解析后块原文变了，Then `stale`，`range` 与 `quote` 仍是记录时的值，`block_id` 为 null。
- M7 Given 003 时期建的整块批注（锚点里没有 `range`），Then 读取时 `range` 为 null、`quote` 为 null，`text` 不变，行为与之前一致。
- M8 Given 编辑批注正文，Then `range`、`quote`、`text` 不变，版本加一。
- M9 Given 事件，Then 载荷里没有区间原文（与 003 的 K14 一致）。

**界面（真实浏览器）**

- N1 Given member 在阅读页选中一段文字，Then 出现"批注所选文字"，点击后条目显示这段原文和页码；刷新后仍在；文本块里这段被标记出来。
- N2 Given 整块批注（不选区）与区间批注同时存在，Then 两者的条目都能区分：区间批注显示更短的引用，整块批注显示整块原文。
- N3 Given viewer，Then 能看，没有选区按钮、编辑和删除按钮。
- N4 Given 跨块选区，Then 不出现批注按钮，也不报错。

## 5. 技术决策与风险

见 [DECISIONS.md](./DECISIONS.md) 的 D-024。

| 风险 | 处理 |
| --- | --- |
| 选区偏移与服务器看到的原文不一致（换行、裁掉的空白） | 区间与整块 `text` 同一份字符串；前端从**同一个** `block.text` 计算偏移，不读 DOM 文本，避免 HTML 折行影响 |
| 重解析后块没变但区间指的地方不对 | 块级哈希只能保证块没变；块内布局变化不会被发现。已知限制 |
| 用户伪造引用 | 引用由服务端截取，客户端提交 `quote` 即 422 |
| 跨块选区被静默忽略 | 界面明确提示"请在同一段内选择"，不生成按钮 |
| 超长块导致选区计算慢 | 偏移是字符串切片，长度线性；不作优化 |

**可失败性**：见第 7 节记录。

## 6. 实施计划与完成定义

1. 规格与决策（本文、D-024）。
2. 后端：`schemas.py` 的 `NoteAnchorIn`/`NoteAnchorOut`，`api/notes.py` 的 `_anchor_snapshot` 与 `_render`。
3. 后端用例 M1 至 M9，加可失败性检查。
4. 前端：文本块改为可选择文字的元素，选区换算，笔记栏按钮，条目的区间引用，块内标记。
5. Playwright N1 至 N4。
6. 文档：需求表、README、003 的非目标说明指向本切片。

**完成定义**：第 4 节每一条有自动化证据；`bash scripts/check.sh` 与 `backend/.venv/bin/python scripts/e2e.py` 通过；READ-08 视完成情况更新（PDF 画布上的区间高亮仍未做，见 D-024）。

## 7. 验收证据

2026-11-05 在本地跑，PostgreSQL 为项目内嵌实例，PDF 为测试夹具 `multipage.pdf`（pdfium 解析）。

### 7.1 命令与结果

| 命令 | 结果 |
| --- | --- |
| `bash scripts/check.sh` | 退出码 0：`secrets: 162 files, 0 problems`；ruff `All checks passed!`；mypy 41 files 无错误；`383 passed, 12 skipped`（含 M1–M9）；前端 `tsc --noEmit` 与 `next build` 通过 |
| `backend/.venv/bin/python scripts/e2e.py` | 退出码 0，`72 passed (2.5m)`（含 N1–N4） |

### 7.2 第 4 节逐条对应

| 标准 | 证据 |
| --- | --- |
| M1 | `backend/tests/test_notes.py::test_m1_a_range_is_cut_from_the_block_by_the_server`：选中的是块中间的词 `text`（不是块首），断言 `range`、`quote`、`text`、`status`、`page_no`，并直接查库确认 `anchor` 里的两个键 |
| M2 | `test_m2_a_range_that_does_not_fit_is_refused`（4 组越界/倒序/空/负数）与 `test_m2_half_a_range_or_a_non_integer_is_refused`（只给一个端点、字符串 `"1"`），都断言 422 `invalid_anchor` 且库里没有记录 |
| M3 | `test_m3_the_quote_cannot_be_supplied_by_the_client`：提交 `quote`、`text`、`page_no`、`content_hash` 各一条，均 422 |
| M4 | `test_m4_the_whole_block_is_a_valid_range`：`0` 到 `len(text)`，引用等于整块原文 |
| M5 | `test_m5_a_reparse_that_keeps_the_block_keeps_the_range`：重新解析后仍 `located`，`range`/`quote` 不变 |
| M6 | `test_m6_a_reparse_that_changes_the_block_makes_the_range_stale`：`stale`，`range`/`quote` 保留，`block_id` 为 null |
| M7 | `test_m7_older_whole_block_anchors_read_as_no_range`：003 的旧锚点读出 `range`/`quote` 为 null，`text` 不变 |
| M8 | `test_m8_editing_the_body_leaves_the_range_alone`：正文加一版本，区间三个字段不动 |
| M9 | `test_m9_events_hold_no_quoted_words`：事件载荷里不含区间原文 |
| N1 | `web/e2e/annotations.spec.ts` 的 N1：真实鼠标选区 → 按钮 → 条目 → 刷新后仍在 → 块内 `<mark>` 显示这段文字 |
| N2 | N2：整块批注与区间批注并列，条目分别显示整块原文与短引用，`note-quote` 上带「部分文字」 |
| N3 | N3：viewer 能看到标记与条目，没有选区按钮、编辑、删除 |
| N4 | N4：跨两个块的选区不出现按钮、不报错、也不生成条目 |

**可失败性检查**：逐个改坏实现，`test_notes.py` 都应失败。第一轮 9 处改动里 8 处被抓住，1 处漏网：

| 改动 | 结果 |
| --- | --- |
| 去掉上界（`<= len(text)`） | 抓住（M2 越界） |
| 允许空区间（`start <= end`） | 抓住（M2 空区间） |
| 去掉下界（允许负数） | 抓住（M2 负数） |
| 不检查两个端点是否同时给出 | 抓住（M2 单端点） |
| 不把 `range` 写进锚点 | 抓住（M1、M4、M7） |
| 不截取引用（存空串） | 抓住（M1、M4） |
| 引用忽略 `end`（`text[:end]`） | **第一轮漏网**：M1 当时选的 `Marker` 正好在块首，`text[:end]` 与 `text[start:end]` 相同。把 M1 改成选块中间的词后，第二轮抓住 |
| 响应里不带 `range` | 抓住（M1、M5、M8） |
| 响应里不带 `quote` | 抓住（M1、M2 之外的读取用例） |

第二轮补测：`quote` 忽略 `start`（`text[end:]`）、`start`/`end` 去掉 `StrictInt`（字符串被转成整数），都被抓住。合计 12 处改动，11 处被抓住，1 处经加强用例后抓住；没有需要记录为「等价」的漏网。每次改动后已还原。

### 7.3 过程中发现的问题

- **N4 一开始失败，是测试写错了**：断言「页面上没有 `role=alert`」时命中了 Next.js 自己渲染的路由播报器，不是本应用的提示。改成匹配 `.notice.error[role="alert"]`（与 `notes.spec.ts`、`management.spec.ts` 一致）后通过。应用本身的行为是对的：跨块选区确实什么都不生成。
- 前端第一次实现 `offsetsIn` 时把 `element.firstChild` 当作文本节点，但块内文字已经被 `<span>`/`<mark>` 包起来，偏移永远算不对。改成用 `TreeWalker` 走过全部文本节点（N1、N3 会失败）。
- 范围校验一度放在 schema 里，导致错误码变成 `validation_error` 而不是 `invalid_anchor`，且 `"1"` 被当成 `1`。改到 `api/notes.py` 并用 `StrictInt` 后与 003 的错误约定一致。

### 7.4 未验证与已知限制

- **PDF 画布上没有按区间高亮**：左栏文本块里标出了这段文字，右边 PDF 仍是整块高亮。按区间画需要逐字包围盒，解析器没给，见 D-024。
- 块内排版变化（块文字没变但换行位置变了）不会被发现，区间可能指向别的位置；`content_hash` 只看整块文字。
- 跨块选区、多个区间、子串级重定位都没有做；一条批注一个区间。
- 选区在块首尾的空白上时按原样存，不裁剪；界面不做提示。
- 没有在真实触屏设备上试过长按选词；N1–N4 用的是鼠标事件。
