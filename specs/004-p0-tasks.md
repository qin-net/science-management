# 004 P0.5（第一部分）：项目任务与看板

- 状态：已实现，验收证据见第 7 节（PROJ-02 仍为进行中：拖拽与更多视图未做）
- 需求映射：`PROJ-02`（看板，状态、负责人、筛选）。`HOME-03`（智能任务面板）只做「汇总待办」中不需要模型的部分，留给 P0.6，本切片只提供它要用的计数。P0.5 的「基础助手」不在本切片，见非目标
- 依赖：[001](./001-p0-workspace-paper-import.md)（成员与角色、项目归档）、[003](./003-p0-notes-annotations.md)（事件与乐观并发的做法）；决策 D-017，新增 D-018

## 1. 问题、目标、范围

**问题**：研究中的待办散在聊天和脑子里，项目里没有一处能说清「谁在做什么、卡在哪、什么时候到期」。

**目标**：成员可以在项目里建任务，给它负责人、期限、优先级，可选地关联一篇文献，按状态在看板上推进；卡住的任务必须写明原因；两个人同时改同一个任务时不会互相覆盖；刷新或重启后任务还在。逾期由服务端按固定规则算，不由页面猜。

**范围内**

- 任务的创建、列表（过滤、分页）、编辑、状态流转、删除
- 状态：`todo`（待办）、`doing`（进行中）、`blocked`（受阻）、`done`（完成）、`cancelled`（已取消）
- 负责人（必须是该工作区成员）、期限（日期）、优先级、关联文献（可选）
- 项目页看板，按状态分列；按负责人过滤
- 按状态的任务计数（给 P0.6 驾驶舱用）

**非目标**

| 不做 | 原因与去向 |
| --- | --- |
| 基础助手：基于已导入资料回答并给出证据 | 回答需要模型；没有配置任何模型，隐私与费用待用户决定，见 D-017。不写假实现。检索式的证据定位（不生成回答）可以单独做，另行写规格 |
| AI 建议的任务草案与接受流程（HOME-03 的 AI 部分、MEET-10） | 需要模型，同上 |
| 任务依赖、里程碑、甘特图、日历（PROJ-03、PROJ-04） | P1 |
| 拖拽换列 | 用下拉框改状态，键盘与读屏可用，也便于自动化验证；拖拽是后续的交互增强 |
| 任务评论、附件、子任务、重复任务、提醒通知 | 后续 |
| 任务关联笔记、实验、稿件 | 对应对象还不存在 |
| 外部看板（Plane）同步 | 开发文档说明已有 Plane 时才接 |

## 2. 用户场景与行为

### 2.1 权限

| 操作 | viewer | member | admin / owner |
| --- | --- | --- | --- |
| 查看任务 | ✓ | ✓ | ✓ |
| 新建、编辑、改状态 | 403 | ✓（任何成员可改任何任务） | ✓ |
| 删除 | 403 | 仅创建者 | ✓（任何人的） |

非成员 404。归档项目可读，写操作 409 `project_archived`。任何成员可改任何任务，是因为任务是协作对象，换负责人、补期限是日常操作；删除比修改更难挽回，所以限制到创建者或管理员。

### 2.2 状态与流转

任何状态之间都可以流转（已完成的任务可以重新打开，已取消的可以恢复），因为科研任务反复是常态，硬性禁止只会逼用户删了重建。规则只有三条：

1. 变成 `blocked` 时必须带非空的 `blocked_reason`（≤500 字符）；已经是 `blocked` 的任务可以单独改原因。
2. 离开 `blocked` 时原因清空，客户端不能在非 `blocked` 状态下设置原因（422）。
3. 变成 `done` 时服务端记录 `completed_at`；离开 `done` 时清空。`cancelled` 不记完成时间。

状态变化写入事件（见 3.3），所以「谁在什么时候把它改成了什么」可查。

### 2.3 字段

| 字段 | 规则 |
| --- | --- |
| `title` | 去首尾空白后 1–200 个字符，必填 |
| `description` | 纯文本，≤5000 个字符，默认空。换行保留，页面按文本显示（同 D-016） |
| `priority` | `low`、`normal`（默认）、`high` |
| `assignee_id` | 可空。必须是该项目所在工作区的成员，否则 422 `invalid_assignee` |
| `due_date` | 可空，`YYYY-MM-DD`。日期不带时区 |
| `paper_id` | 可空，必须是本项目的文献，否则 404（不泄露其他项目的存在） |

标题与描述不含除换行和制表符以外的控制字符。

### 2.4 逾期

`overdue` 由服务端算：`due_date` 早于**服务器 UTC 的今天**，且状态是 `todo`、`doing` 或 `blocked`。`done`、`cancelled` 永远不逾期，没有期限的任务也不逾期。「今天」按 UTC 算是已知限制：东八区的用户在早上 8 点前看到的「今天」仍是前一天。

### 2.5 负责人离开工作区

负责人被移出工作区后，任务保留其 `assignee_id`，响应里 `assignee_active` 为 false，界面写「（已不是成员）」。不自动改成未分配，因为静默改动会让人找不到原来是谁在负责。

### 2.6 删除文献

删除文献时，关联它的任务保留，`paper_id` 置空；事件 `paper.deleted` 的载荷加 `task_count`（被解除关联的任务数）。任务不是文献的一部分，不随文献删除。

### 2.7 编辑与并发

`PATCH` 需要 `expected_version`，不一致返回 409 `version_conflict`，响应带 `current_version`，数据不变；界面提示「内容已被他人修改，请刷新后再试」并保留用户输入。只改传入的字段；传 `null` 表示清空 `assignee_id`、`due_date`、`paper_id`。

## 3. 数据与接口

### 3.1 表 `tasks`（迁移 `0004`）

| 列 | 说明 |
| --- | --- |
| `id`、`workspace_id`、`project_id` | 与其他对象一致，`project_id` 有索引 |
| `title`、`description` | 文本；数据库 CHECK：标题 1–200，描述 ≤5000 |
| `status` | CHECK 在五个取值内 |
| `priority` | CHECK 在三个取值内 |
| `blocked_reason` | 可空文本，≤500。CHECK：`status='blocked'` 时非空，否则为空 |
| `assignee_id` | 可空，外键 `users.id` |
| `due_date` | 可空 `date` |
| `paper_id` | 可空，外键 `papers.id` |
| `created_by` | 外键 `users.id` |
| `completed_at` | 可空时间戳。CHECK：只有 `status='done'` 时非空 |
| `version`、`created_at`、`updated_at` | 乐观并发与时间 |

索引 `(project_id, status)`、`(project_id, assignee_id)`。

### 3.2 API（前缀 `/api/v1`，嵌套在项目下）

| 方法与路径 | 说明 | 角色 |
| --- | --- | --- |
| `GET /projects/{id}/tasks?status=&assignee=&paper_id=&limit=&offset=` | 按创建时间倒序。`assignee` 为用户 ID 或 `none`（未分配）；`status` 可重复传以取多个状态；`limit` 1–200，默认 100 | viewer |
| `POST /projects/{id}/tasks` | 创建，201，初始状态 `todo`（可显式指定 `todo`/`doing`，指定 `blocked` 需带原因） | member |
| `PATCH /projects/{id}/tasks/{task_id}` | 局部修改，需要 `expected_version` | member |
| `DELETE /projects/{id}/tasks/{task_id}` | 删除，204 | 创建者或 admin/owner |

请求体 `extra=forbid`，客户端不能提交 `created_by`、`completed_at`、`version`、`overdue` 等。

**列表响应**

```json
{"items": [ … ], "total": 12, "offset": 0, "limit": 100,
 "counts": {"todo": 5, "doing": 3, "blocked": 1, "done": 2, "cancelled": 1, "overdue": 2}}
```

`counts` 统计整个项目、**不受过滤和分页影响**，`overdue` 按 2.4。`total` 是过滤后的条数。

**任务响应**

```json
{"id": "…", "title": "…", "description": "…", "status": "blocked", "priority": "high",
 "blocked_reason": "等数据集授权", "assignee_id": "…", "assignee_name": "…",
 "assignee_active": true, "due_date": "2026-11-05", "overdue": false,
 "paper_id": "…", "paper_title": "…", "created_by": "…", "created_by_name": "…",
 "completed_at": null, "version": 3, "editable": true, "deletable": false,
 "created_at": "…", "updated_at": "…"}
```

`editable`、`deletable` 由服务端按调用者算出，前端只据此显示按钮。

**新增错误码**：`invalid_assignee`（422）、`blocked_reason_required`（422）、`blocked_reason_not_allowed`（422）。其余沿用 `version_conflict`、`forbidden`、`not_found`、`project_archived`、`validation_error`。

### 3.3 事件与现有接口的改动

- 事件 `task.created`、`task.updated`、`task.deleted`。载荷只含结构化信息：`{"status": …, "paper_id": …}`；`task.updated` 加 `"changed": [字段名…]`，状态变化时加 `"from"`、`"to"`。**不含标题、描述和受阻原因**。
- `DELETE /papers/{id}` 解除任务关联；`paper.deleted` 载荷加 `task_count`。
- 前端 `EVENT_LABELS` 补三条；删除文献的确认框补一句「关联的任务会保留，只是不再指向这篇文献」。

## 4. 验收标准

后端用例同样用真实 PostgreSQL，不用 mock。

**权限与归属**

- T1 Given viewer，When 创建、编辑、删除，Then 403，查看可以。非成员 404。归档项目可读，写操作 409。
- T2 Given member A 创建的任务，When member B 编辑与改状态，Then 成功；B 删除 403；A 删除成功；admin 删除别人的成功。`editable`/`deletable` 对不同调用者取值正确。
- T3 Given 别的项目的 `paper_id`，When 创建或编辑，Then 404，不留记录；别的项目的任务在本项目列表里看不到，也不能被本项目路径改或删。
- T4 Given 不是该工作区成员的用户 ID（包括另一个工作区的人）作负责人，Then 422 `invalid_assignee`。

**状态与字段**

- T5 Given 改成 `blocked` 不带原因，Then 422 `blocked_reason_required`；带原因成功；在 `todo` 状态下设原因 422 `blocked_reason_not_allowed`；离开 `blocked` 后原因为空。
- T6 Given 改成 `done`，Then `completed_at` 有值；再改回 `doing`，Then 为空；`cancelled` 无完成时间。五个状态两两之间的流转均可（至少覆盖「完成后重开」「取消后恢复」）。
- T7 Given 非法状态、优先级、日期（`2026-02-30`）、空标题、201 个字符的标题、超长描述、控制字符、`extra` 字段（`created_by`、`overdue`、`completed_at`、`version`），Then 422，库里没有记录。
- T8 Given 期限为昨天，Then `todo`/`doing`/`blocked` 任务 `overdue=true`，`done`/`cancelled` 与无期限的为 false；今天与明天为 false；`counts.overdue` 一致。

**并发与一致性**

- T9 Given 两个并发 PATCH 带相同 `expected_version`，Then 一个成功（版本加 1），另一个 409 `version_conflict` 且带 `current_version`，数据只含成功者的修改。
- T10 Given PATCH 传 `null`，Then 清空负责人、期限、关联文献；未传的字段不变。
- T11 Given 负责人被移出工作区，Then 任务仍显示他，`assignee_active=false`。
- T12 Given 删除文献，Then 关联任务保留且 `paper_id` 为空，其他任务不变，`paper.deleted` 的 `task_count` 正确。
- T13 Given 列表过滤，Then `status`（单个与多个）、`assignee`（用户 ID、`none`）、`paper_id` 正确；`counts` 不受过滤和分页影响；分页边界（`limit` 0、201）422。
- T14 Given 事件，Then 载荷里没有标题、描述或原因，状态变化带 `from`/`to`。
- T15 Given 重新读库，Then 任务仍在（数据在 PostgreSQL）。数据库自身拒绝非法组合（`blocked` 无原因、非 `done` 却有完成时间）。

**界面（真实浏览器）**

- M1 Given member 在项目页，When 创建任务，Then 出现在「待办」列，刷新后仍在；列标题上的计数正确。
- M2 Given 一个任务，When 改为「受阻」，Then 界面要求填写原因，填了才能保存，卡片显示原因；改为「完成」后移到「完成」列，刷新后仍在。
- M3 Given viewer，Then 能看到看板，没有新建表单、状态下拉、编辑与删除按钮。
- M4 Given 两个标签页同时编辑一个任务，Then 后保存的一方看到冲突提示且输入没有丢失。
- M5 Given 任务正文含 `<img onerror>` 之类的标记，Then 页面按文本显示，没有生成元素，没有脚本执行。
- M6 Given 逾期任务，Then 卡片有「已逾期」标记；完成后标记消失。
- M7 Given 按负责人过滤，Then 只显示符合的任务，计数仍是全项目的。
- M8 Given 任务关联了文献，When 删除该文献，Then 任务仍在，不再显示文献链接。

## 5. 技术决策与风险

决策见 [DECISIONS.md](./DECISIONS.md) 的 D-018。

| 风险 | 处理 |
| --- | --- |
| 受阻任务没有原因，成了谁都不知道为什么卡着的死任务 | 数据库 CHECK 与接口各校验一次 |
| 两人同时改，后者静默覆盖 | `expected_version` 乐观并发，冲突不丢输入 |
| 负责人是不相干的人（越权关联） | 只允许该工作区成员；移出后保留并标注，不静默改 |
| 页面自己算逾期，各端口径不一致 | 只由服务端算，口径写在 2.4，界面只显示 |
| 任务文字里的标记进入页面 | 一律按文本渲染 |
| 删除文献连带丢任务 | 任务保留，只解除关联 |

**未验证的假设**：UTC 日期作为「今天」对东八区用户在早上 8 点前会晚一天，没有做按用户时区的版本，需要时再改为请求里带时区。

## 6. 实施计划与完成定义

1. 迁移 `0004` 与模型，含 CHECK。
2. 后端：四个接口、事件、删除文献时解除关联。
3. 后端用例 T1 至 T15，含可失败性检查。
4. 前端：项目页看板与创建表单、编辑、改状态、过滤。
5. Playwright M1 至 M8。
6. 文档：README、映射表、本节证据。

**完成定义**：第 4 节每一条有自动化证据；`bash scripts/check.sh` 与 `scripts/e2e.py` 通过；PROJ-02 标「进行中」（看板有，拖拽与更多视图没有）；HOME-03 保持「未做」；基础助手与 AI 任务草案在 D-017 说明原因。

## 7. 验收证据

记录日期 2026-10-31，本机 macOS arm64，uv，Node 24，Python 3.12，内嵌 PostgreSQL。

### 7.1 自动检查

| 命令 | 结果 |
| --- | --- |
| `bash scripts/check.sh` | 退出码 0，`all checks passed`。ruff、mypy（36 个源文件）通过；pytest（含迁移漂移检查）273 passed、12 skipped（10 个 Docling、2 个在线契约，均未设对应环境变量）；前端类型检查与构建通过 |
| `backend/.venv/bin/python scripts/e2e.py` | 43 passed，其中本切片 8 个（`web/e2e/tasks.spec.ts`），其余 35 个无回归 |

### 7.2 标准与证据

后端用例在真实 PostgreSQL 上，没有 mock；文件 `backend/tests/test_tasks.py`（34 个用例，部分带参数）。

| 标准 | 证据 |
| --- | --- |
| T1 | `test_t1_viewer_reads_but_cannot_write_and_outsider_sees_nothing`、`test_t1_archived_project_is_readable_but_not_writable` |
| T2 | `test_t2_any_member_edits_but_only_creator_or_admin_deletes`（含 `editable`/`deletable`） |
| T3 | `test_t3_other_projects_objects_are_refused_and_never_listed` |
| T4 | `test_t4_assignee_must_be_a_member_of_the_workspace`（另一工作区的人、不存在的 ID，创建与编辑各一次） |
| T5 | `test_t5_blocked_needs_a_reason_and_only_blocked_has_one` |
| T6 | `test_t6_done_records_the_time_and_every_transition_is_allowed`（五个状态两两共 25 种流转）、`test_t6_editing_a_done_task_keeps_its_completion_time` |
| T7 | `test_t7_invalid_input_is_refused_and_nothing_is_stored`（15 种非法输入）、`test_t7_invalid_edits_are_refused_and_nothing_changes`、`test_t7_limits_are_inclusive_and_text_is_trimmed` |
| T8 | `test_t8_overdue_is_decided_by_the_server`（昨天、今天、明天，五种状态，`counts.overdue`，重开后重新逾期） |
| T9 | `test_t9_two_edits_from_the_same_version_cannot_both_win`（两个线程同时 PATCH，恰为一个 200 和一个 409） |
| T10 | `test_t10_null_clears_and_unsent_fields_stay`、`test_t10_a_patch_that_changes_nothing_does_not_bump_the_version` |
| T11 | `test_t11_an_assignee_who_left_is_kept_and_flagged`（移出后保留、`assignee_active=false`，不能再被新指派） |
| T12 | `test_t12_deleting_a_paper_keeps_its_tasks`（`task_count` 为 2，其他任务不变） |
| T13 | `test_t13_filters_and_counts`（单个与多个状态、`none`、用户 ID、文献；计数不受过滤和分页影响；非法参数 422） |
| T14 | `test_t14_events_say_what_happened_without_the_words` |
| T15 | `test_t15_tasks_are_read_back_and_the_database_refuses_bad_combinations`（新会话读库；6 个 CHECK 共 10 种非法组合直接写库被拒） |
| M1 至 M8 | `web/e2e/tasks.spec.ts` 的 8 个用例，名称以 M1…M8 开头 |

**可失败性检查**：逐个改坏实现，`test_tasks.py` 都应失败。12 处改动各被至少一个用例抓住：任何成员都可删除、删除不检查创建者、去掉版本号检查、逾期包含已完成、离开完成状态不清完成时间、不检查负责人、不检查文献归属、事件载荷带标题、离开受阻状态保留原因、无变化的 PATCH 也加版本、`counts` 受过滤影响（第一次改动没写对，修正后被 `test_t13` 抓住）、viewer 可写。每次改动后已还原。

**过程中发现并修复的缺陷**：M8 第一次运行暴露「关联文献」下拉框只在页面载入时取一次文献列表，上传或导入新文献后下拉框里没有它；现在上传、导入、删除文献后任务面板都会刷新。

### 7.3 未验证与已知限制

- 「逾期」按服务器 UTC 日期算，东八区早上 8 点前仍是前一天；没有做按用户时区的版本。
- 看板用下拉框改状态，没有拖拽；没有按状态以外的排序，每列按创建时间倒序。
- 一次最多取 200 个任务，超出时界面只提示「只显示最近的 N 个」，没有分页。
- 任务没有评论、提醒、依赖、子任务，也不关联笔记（对应对象或功能还不存在）。
- 基础助手与 AI 任务草案未做，原因见 D-017。
- 并发测试覆盖同一账号的两个线程和两个标签页，没有测两个不同账号同时编辑（后端逻辑相同）。
- e2e 只在 Chromium 上跑过。
