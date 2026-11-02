# 009 P0.6 续：阅读覆盖与活动时间轴

- 状态：进行中（验收证据见第 7 节）
- 需求映射：`HOME-06`（部分：已解析与有笔记的文献数，没有「读过」和方向分类）、`HOME-07`（部分：最近 14 天按天的活动计数，没有里程碑）。`HOME-10` 不做
- 依赖：[005](./005-p0-cockpit.md)（总览接口与口径）、[003](./003-p0-notes-annotations.md)（笔记）、事件表；决策 D-019 的延续，新增 D-022

## 1. 问题、目标、范围

**问题**：总览只有文献的条数。条数不说明文献有没有被处理过，也看不出最近有没有人在动这个项目。

**目标**：在总览里加两组真实数字：每个项目和整个工作区里「已解析」「有笔记」的文献数；最近 14 天每天的活动数，分文献、笔记、任务三类。口径写在页面上。

**范围内**

- `GET /workspaces/{id}/overview` 增加字段，不新增接口和表
- 首页总览区增加两个汇总数字、项目行上的「有笔记」计数、一块「最近 14 天活动」
- 口径说明增加对应条目

**非目标**

| 不做 | 原因与去向 |
| --- | --- |
| 「阅读数量」「读过」 | 系统没有阅读记录；打开一篇文献不写入任何记录。只给「有笔记的文献」，名称里不出现「已读」 |
| 研究方向、知识覆盖 | 文献没有方向或主题分类，没有可以据此计算覆盖率的定义 |
| 里程碑式的科研时间轴 | 没有里程碑对象。「时间轴」只做成按天的活动计数 |
| 组会、投稿倒计时（HOME-10、HOME-11） | 这两类对象还不存在 |
| 按用户拆分、可选时间段、图表库 | 先把一个固定窗口算对；柱状只用 CSS 画，数字同时以文字给出 |

## 2. 行为

### 2.1 权限

与 005 相同：工作区成员（含 viewer）可读，非成员 404。不增加可见范围：活动计数只含事件的类型分类，不含任何文字。

### 2.2 口径

| 项 | 口径 |
| --- | --- |
| 已解析文献数（汇总） | 活跃与归档项目合计，当前版本有当前解析结果的文献数，与项目行的「已解析」同一口径 |
| 有笔记的文献数（项目、汇总） | 至少有一条笔记或批注（`notes.paper_id` 指向它）的文献数，按文献去重；项目笔记（无 `paper_id`）不计 |
| 活动窗口 | 服务端 UTC 日期，含今天在内的最近 14 天，每天一项，没有活动的日子为 0，不省略 |
| 活动计数 | 窗口内、属于某个项目的事件，每条事件算一次；按事件类型前缀分类：`paper.*` 为文献，`note.*` 为笔记，`task.*` 为任务，`project.*` 为项目；`total` 是四类之和 |

「有笔记」不等于读过：写笔记的人可能只读了摘要，读过的人也可能没写笔记。页面上不使用「已读」字样。

活动计数里的「文献」类包含解析成功、失败、取消等系统事件，所以它反映的是处理活动，不是人的阅读。页面口径说明写明这一点。

## 3. 数据与接口

不新增表。`GET /api/v1/workspaces/{id}/overview` 增加：

```json
{"totals": {"parsed_papers": 5, "noted_papers": 3},
 "projects": [{"noted_paper_count": 2}],
 "activity": [{"date": "2026-10-19", "total": 4, "papers": 2, "notes": 1, "tasks": 1, "projects": 0}]}
```

`activity` 恰好 14 项，日期升序，最后一项是 `today`。

## 4. 验收标准

后端用例用真实 PostgreSQL，不用 mock。

- R1 Given 项目里 3 篇文献，一篇已解析且有两条笔记，一篇有一条批注但未解析，一篇都没有，Then 项目的 `noted_paper_count` 为 2（按文献去重），汇总 `noted_papers` 为 2，`parsed_papers` 为 1。
- R2 Given 只有项目笔记（无文献），Then `noted_paper_count` 为 0。
- R3 Given 归档项目里有已解析文献与笔记，Then 汇总的 `parsed_papers`、`noted_papers` 仍计入，项目行也有。
- R4 Given 其他工作区的文献和笔记，Then 不计入。
- R5 Given 删除一篇有笔记的文献，Then 笔记随之删除，计数立刻下降（读时计算）。
- T1 Given 空工作区，Then `activity` 有 14 项、全为 0、最后一项日期是 `today`。
- T2 Given 今天的文献、笔记、任务、项目事件各若干，Then 最后一项的四类计数与事件数一致，`total` 是四类之和。
- T3 Given 手工插入的更早日期的事件（昨天、13 天前、14 天前），Then 昨天和 13 天前落在对应项，14 天前不出现；日期边界按 UTC。
- T4 Given 其他工作区的事件，Then 不计入；没有项目的事件（`workspace.created`）不属于四类，也不计入。
- T5 Given 事件，Then `activity` 不含任何标题或正文文字。

**界面（真实浏览器）**

- U1 Given 有文献、笔记和任务，Then 首页显示「已解析文献」「有笔记的文献」与 API 一致，刷新后不变；项目行显示「有笔记 n」。
- U2 Given 当天做了一些操作，Then 「最近 14 天活动」显示 14 天，最后一天的数字与操作数一致，并有文字形式的数字（不只是图形）。
- U3 Given 新工作区，Then 活动区显示 14 天全为 0 和说明，没有 NaN 或 undefined。
- U4 Given 展开「统计口径」，Then 包含「有笔记不等于读过」和活动按 UTC 日期的说明。

## 5. 技术决策与风险

决策见 [DECISIONS.md](./DECISIONS.md) 的 D-022。

| 风险 | 处理 |
| --- | --- |
| 「有笔记」被误读成「已读」 | 名称不用「已读」；口径写明；D-022 记录原因 |
| 活动计数被系统事件撑大 | 口径写明文献类含解析事件；不按人拆分 |
| 日期边界与用户本地时间不一致 | 与逾期一样用 UTC，页面写明；东八区早 8 点前属于前一天 |
| 查询变慢 | 活动用一次分组聚合，窗口固定 14 天；文献与笔记各一次聚合；未做性能测量 |

**未验证的假设**：工作区事件量在窗口内不大；`research_events` 只有 `(project_id, created_at)` 索引，没有按工作区和时间的索引，量大时需要加。

## 6. 实施计划与完成定义

1. 后端：扩展 `overview`，用例 R1 至 R5、T1 至 T5，含可失败性检查。
2. 前端：汇总数字、项目行、活动区、口径说明。
3. Playwright U1 至 U4。
4. 文档：映射表、README、D-022、本节证据。

**完成定义**：第 4 节每一条有自动化证据；`bash scripts/check.sh` 与 `scripts/e2e.py` 通过；HOME-06、HOME-07 标「进行中」。

## 7. 验收证据

记录日期 2026-11-02，本机 macOS arm64，uv，Node 24，Python 3.12，内嵌 PostgreSQL。

### 7.1 自动检查

| 命令 | 结果 |
| --- | --- |
| `bash scripts/check.sh` | 退出码 0，`all checks passed`。secrets 154 个文件 0 问题；ruff、mypy（40 个源文件）通过；pytest（含迁移漂移检查）343 passed、12 skipped（10 个 Docling、2 个在线契约，均未设对应环境变量）；前端类型检查与构建通过 |
| `backend/.venv/bin/python scripts/e2e.py` | 63 passed，其中本切片 3 个（`web/e2e/overview.spec.ts` 的 U1+U2、U3、U4），其余无回归 |

### 7.2 标准与证据

后端用例在真实 PostgreSQL 和真实生成的 PDF 上，没有 mock；文件 `backend/tests/test_overview.py`，本切片新增 11 个用例，整个文件 22 个。

| 标准 | 证据 |
| --- | --- |
| R1 | `test_r1_noted_papers_are_counted_once_each_and_parsing_is_separate`（3 篇文献、3 条笔记，其中一篇有两条；未解析的文献也可以有笔记） |
| R2 | `test_r2_project_notes_do_not_count_as_noted_papers` |
| R3 | `test_r3_archived_projects_still_count_their_papers` |
| R4 | `test_r4_other_workspaces_are_not_counted` |
| R5 | `test_r5_deleting_a_paper_takes_its_notes_out_of_the_count` |
| T1 | `test_t1_an_empty_workspace_still_has_fourteen_zero_days`（14 项、日期连续、最后一项是 `today`） |
| T2 | `test_t2_todays_events_are_split_by_kind` |
| T3 | `test_t3_the_window_is_fourteen_utc_days`（昨天、13 天前在窗口内，14 天前不在）、`test_t3_events_just_either_side_of_utc_midnight_land_on_different_days`（UTC 午夜前 1 秒与之后分在两天） |
| T4 | `test_t4_other_workspaces_events_are_not_counted` |
| T5 | `test_t5_activity_carries_numbers_and_no_words` |
| U1 | `web/e2e/overview.spec.ts` 的 U1+U2：上传并解析一篇文献、加两个任务、写一条批注，回首页核对汇总数字与项目行，刷新后再核对一次 |
| U2 | 同上用例：14 行；今天一行的项目 1、任务 2、笔记 1、文献 3（导入、排队、解析成功）、合计 7，与上面的操作一致，刷新后不变 |
| U3 | `U3: a new workspace shows fourteen empty days…` |
| U4 | `U4: the definitions say a note is not a read…` |

U1 与 U2 合在一个用例里，因为两者需要同一批操作；这一点与规格里的编号不是一一对应。

**可失败性检查**：逐个改坏 `overview.py`，`test_overview.py` 应失败。9 处改动中 6 处被抓住：笔记不按文献去重（R1）、窗口改成 13 天（T1）、活动查询不限工作区（T4）、笔记计数不限工作区（R4）、活动不计任务类事件（P1 等）、已解析汇总只算活跃项目（R3）。

另外 3 处没有被抓住，原因都是改动不改变结果，不是用例漏了：

- 去掉 `paper_id IS NOT NULL`：`count(distinct paper_id)` 忽略 NULL，项目笔记本来就不会被计入。
- 去掉 `project_id IS NOT NULL`：没有项目的事件是 `workspace.created`，类型前缀不在四类里，分组后被丢弃。
- 窗口起点早一天：多出来的一天不在 14 项列表里，被丢弃。

这三处条件保留，作为纵深防御。每次改动后已还原。

**过程中发现并修复的缺陷**：无。旧用例 P2、P7、N1 因为总数字段和文案变了而更新，期望值改动只是加了新字段。

### 7.3 未验证与已知限制

- 「有笔记」不等于读过。系统不记录谁打开了哪篇文献（D-022）。
- 活动里的「文献」类包含 Worker 写的解析事件，所以一次上传至少算 3 次，反映处理活动，不是人的阅读。
- 日期按 UTC，东八区早 8 点前的操作算在前一天。
- `research_events` 没有按工作区和时间的索引，事件量大时活动查询会变慢；没有做性能测量。
- 没有方向、知识覆盖、里程碑、组会与投稿倒计时。HOME-06、HOME-07 因此保持「进行中」，HOME-10 未做。
- 柱状图只是辅助，数字以表格给出；没有在窄屏下检查表格的显示。
- e2e 只在 Chromium 上跑过。
