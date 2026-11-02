# 008 P0.9：单篇文献的核心贡献总结

- 状态：已实现，验收证据见第 7 节（READ-11 仍为进行中：单篇、固定提示，没有缓存和多篇；真实 Anthropic 接口未验证）
- 需求映射：`READ-11`（核心贡献总结）。`READ-06`（翻译）、`READ-12`（动机分析）、`HOME-03` 的 AI 部分不在本切片
- 依赖：[007](./007-p0-cited-qa.md)（模型网关、出处核对、额度）、[003](./003-p0-notes-annotations.md)（笔记）；决策 D-017、D-021

## 1. 问题、目标、范围

**问题**：读一篇新论文前，想先知道它做了什么、主要贡献是什么。通用聊天工具能概括，但没有出处，也不能直接留在项目里。

**目标**：成员在一篇已解析的文献上点「生成核心贡献总结」，得到分段的总结，每段带指向文本块的出处，没有出处的段落明确标「无出处」。用户可以一键把总结存成这篇文献的笔记，笔记里保留出处的页码和「无出处」标记。

**范围内**

- 接口 `POST /projects/{id}/papers/{paper_id}/summary`，响应与 007 的问答响应同形
- 把 007 的取块、转义、编号、出处核对、额度、记账抽成共用函数；总结只是固定了问题
- `model_calls.kind` 增加 `summary`（迁移 `0006`）
- 阅读页「核心贡献总结」区：生成、展示分段与出处、存为笔记
- 存笔记走已有的 `POST /projects/{id}/notes`，不新增笔记类型

**非目标**

| 不做 | 原因与去向 |
| --- | --- |
| 保存总结本身（缓存、历史、重新生成对比） | 与 007 一致：不存模型输出，避免过期内容；用户存成笔记即由用户决定保留什么 |
| 总结多篇文献 | 单篇；多篇综述是 LIT-13，P1 |
| 其他语言之外的输出风格选择、长度调节 | 只提供中文与英文两种，固定 3 至 5 段 |
| 翻译、动机分析 | 各自另写规格 |
| 存为笔记时自动锚定到某个块 | 笔记正文里写页码；块级锚点是用户在阅读器里主动选的行为（003） |

## 2. 用户场景与行为

### 2.1 权限与范围

与 007 一致：member 及以上可生成，viewer 403，非成员 404，归档项目可以生成（只读）。`paper_id` 不属于该项目 404。存为笔记需要能写项目（003 的规则：viewer 403，归档项目 409）。

### 2.2 请求

`POST /api/v1/projects/{id}/papers/{paper_id}/summary`，请求体 `extra=forbid`：

| 字段 | 规则 |
| --- | --- |
| `language` | `zh` 或 `en`，默认 `zh` |

### 2.3 行为

- 文献没有当前解析结果：422 `no_readable_papers`（与 007 同），不调用模型。
- 文本超过 `MODEL_CONTEXT_CHARS`：422 `context_too_large`。
- 固定问题：中文为「请用中文概括这篇论文的核心贡献，写成 3 至 5 个短段落，说明它解决什么问题、提出什么方法、得到什么结果。」英文为对应的英文句子。
- 其余处理、响应形状、错误码、额度、记账（`kind='summary'`）、日志规则全部与 007 相同；总结与问答共用同一个每用户每分钟与每日额度。
- 响应不含问题文字（问题是固定的，不是用户内容），其余字段同 007。

### 2.4 存为笔记

界面把总结拼成纯文本正文：每段一行，有出处的段落后附 `（第 N 页）`，多处出处列出去重后的页码；无出处的段落前加 `【无出处】`；正文开头一行 `核心贡献总结（模型生成，请核对原文）`。拼接在前端完成，保存走已有的笔记接口，笔记作者是点击的用户，可以像其他笔记一样编辑和删除。

## 3. 数据与接口

迁移 `0006`：把 `ck_model_call_kind` 改为允许 `ask`、`summary`。无新表、无新列、无新事件（笔记创建已有 `note.created`）。

不新增配置项。

## 4. 验收标准

后端用例同 007：真实 PostgreSQL、真实解析块、本地假模型。

- S1 Given viewer，When 生成总结，Then 403；非成员 404；`paper_id` 属于别的项目 404；这些情况假模型收到 0 次请求。归档项目可以生成。
- S2 Given 已解析文献，Then 假模型收到的请求里只有这一篇的块，问题是对应语言的固定句子；`language` 取值之外 422。
- S3 Given 文献未解析或没有文件，Then 422 `no_readable_papers`，不调用模型，不记 `model_calls`。
- S4 Given 假模型返回带出处与不带出处的段落，Then 响应的出处原文取自库，无效编号被丢弃并计数，与 007 同。
- S5 Given 成功与失败，Then 各写一行 `model_calls`，`kind='summary'`；问题、回答、论文文本不出现在表或日志里。
- S6 Given 同一用户的问答与总结合计达到每分钟额度，Then 总结 429 `model_rate_limited`（共用额度）。
- S7 迁移 `0006` 之后，直接往 `model_calls` 写 `kind='summary'` 成功，写 `kind='other'` 被 CHECK 拒绝（迁移漂移检查通过）。
- Z1 Given 已解析文献的阅读页，When 点生成，Then 显示分段总结与可展开的出处，无出处的段落标「无出处」。
- Z2 When 点「存为笔记」，Then 该文献的笔记栏出现一条笔记，正文含页码与「【无出处】」标记；刷新后仍在。
- Z3 Given 模型出错，Then 页面显示明确错误，没有总结，也没有「存为笔记」按钮。
- Z4 Given 总结文字含 `<img onerror>`，Then 按文本显示，存成的笔记也按文本显示。

## 5. 技术决策与风险

沿用 D-021，不新增决策。

| 风险 | 处理 |
| --- | --- |
| 总结遗漏或曲解论文的真正贡献 | 无法由服务端判断；界面提示「模型生成，请核对原文」，出处原文放在旁边；存成的笔记开头保留同一提示 |
| 存为笔记后「无出处」标记被用户编辑掉 | 笔记正文是用户自己的文字，可编辑；这是预期行为，不强制 |
| 论文过长被拒绝 | 如实报 `context_too_large`；分段总结长文是后续 |

**未验证的假设**：同 007，真实模型的总结质量与引用准确性没有验证。

## 6. 实施计划与完成定义

1. 迁移 `0006`、模型 CHECK。
2. 把 `ask.py` 的主体抽成共用函数，007 的用例必须仍然全过。
3. 总结接口。
4. 后端用例 S1 至 S7，含可失败性检查。
5. 前端：阅读页总结区、存为笔记。
6. Playwright Z1 至 Z4。
7. 文档：README、映射表、本节证据。

**完成定义**：第 4 节每一条有自动化证据；`bash scripts/check.sh` 与 `scripts/e2e.py` 通过；READ-11 标「进行中」（单篇总结已做，没有多语言风格、多篇、缓存）。

## 7. 验收证据

记录日期 2026-11-02，本机 macOS arm64，uv，Node 24，Python 3.12，内嵌 PostgreSQL。

### 7.1 自动检查

| 命令 | 结果 |
| --- | --- |
| `bash scripts/check.sh` | 退出码 0，`all checks passed`；pytest 332 passed、12 skipped（10 个 Docling、2 个在线契约）；ruff、mypy、前端类型检查与构建、迁移漂移检查（含 `0006`）通过 |
| `backend/.venv/bin/python scripts/e2e.py` | 59 passed，其中本切片 3 个（`web/e2e/summary.spec.ts`），其余无回归 |

### 7.2 标准与证据

后端用例 `backend/tests/test_summary.py`（8 个），复用 `test_ask.py` 的辅助函数；`ask.py` 的主体抽成 `run_cited_question` 之后，007 的 24 个用例原样全过。

| 标准 | 证据 |
| --- | --- |
| S1 | `test_s1_members_summarize_viewers_and_strangers_do_not`、`test_s1_an_archived_project_can_still_be_summarized` |
| S2 | `test_s2_only_this_paper_is_sent_with_the_fixed_question`（问题另用关键词核对，防止常量被改后测试跟着通过） |
| S3 | `test_s3_a_paper_without_text_is_refused_before_the_model` |
| S4 | `test_s4_evidence_comes_from_the_database_and_bad_numbers_are_dropped` |
| S5 | `test_s5_one_summary_row_per_call_and_no_words_anywhere` |
| S6 | `test_s6_questions_and_summaries_share_one_allowance` |
| S7 | `test_s7_the_ledger_accepts_summary_and_nothing_else` |
| Z1、Z2 | `web/e2e/summary.spec.ts` 的第一个用例（生成、展开出处、存为笔记、刷新后仍在；笔记含页码与「【无出处】」） |
| Z3 | 同文件：模型 500 时显示错误，没有总结和「存为笔记」按钮 |
| Z4 | 同文件：总结与存成的笔记含 `<img onerror>` 时按文本显示 |

**可失败性检查**：对 `ask.py`、`schemas.py` 做 6 处改动（viewer 可生成、不限定 `paper_id`、`kind` 记成 `ask`、英文用中文问题、`language` 不校验、中文问题文字被改），最初 5 处被抓住。「中文问题文字被改」没有被抓住，因为测试拿常量和常量比较；加入关键词核对后再次检查被抓住。每次改动后已还原。

### 7.3 未验证与已知限制

- 与 007 相同：没有用真实 Anthropic 接口验证；总结质量、是否遗漏真正的贡献无法由服务端判断。
- 存为笔记的拼接在前端完成，没有单独的前端单元测试，只通过 Z2 的浏览器用例覆盖。
- 提示固定，语言只有中文和英文；没有缓存，重复生成会重复计费，但受额度限制。
- 文献过长（超过 `MODEL_CONTEXT_CHARS`）时拒绝，没有分段总结。
- 规格的 Z1 与 Z2 在同一个浏览器用例里验证。
