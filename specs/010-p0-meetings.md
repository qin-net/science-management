# 010 P0.6 续：组会与倒计时

- 状态：进行中（验收证据见第 7 节）
- 需求映射：`HOME-10`（组会倒计时）。组会录音、转写、纪要（MEET-*，P2）不做
- 依赖：[001](./001-p0-workspace-paper-import.md)（成员、角色、项目归档）、[004](./004-p0-tasks.md)（权限与乐观并发的做法）、[005](./005-p0-cockpit.md)、[009](./009-p0-cockpit-reading-activity.md)（总览接口）；新增 D-023

## 1. 问题、目标、范围

**问题**：首页没有"下一次组会是什么时候"。组会时间只在聊天里，临近才想起来。

**目标**：成员可以在项目里登记组会（标题、开始时间、地点文字）。首页显示工作区里最近的下一次组会和还剩多久；没有就明说。时间以带时区的绝对时刻存，页面按浏览器本地时区显示。

**范围内**

- 项目下的组会：新建、列表、编辑、删除
- 总览接口增加 `next_meeting`；活动计数增加 `meetings` 一类
- 项目页的"组会"面板；首页的倒计时卡片

**非目标**

| 不做 | 原因与去向 |
| --- | --- |
| 录音、转写、纪要、决策与任务的自动提取 | P2（MEET-*），需要同意流程、模型和许可核对 |
| 重复组会、提醒通知、日历文件导入导出、会议链接 | 提醒需要投递渠道；链接会引入外链校验问题。地点只是纯文本 |
| 议程、参会人、会议记录 | 对应对象还不存在 |
| 投稿倒计时（HOME-11，P1） | 投稿对象不存在 |
| 页面每秒走动的倒计时 | 只在加载时由服务端给出的秒数算一次，刷新更新 |

## 2. 行为

### 2.1 权限

与任务一致：非成员 404；viewer 可读，写 403；member 可新建和编辑任何组会；删除仅创建者或 admin/owner。归档项目可读，写操作 409 `project_archived`。

### 2.2 字段

| 字段 | 规则 |
| --- | --- |
| `title` | 去首尾空白后 1–200 个字符，不含控制字符，必填 |
| `starts_at` | 必须是带时区偏移的 ISO 8601 时间（如 `2026-11-10T09:30:00+08:00` 或以 `Z` 结尾）；不带偏移的 422；年份限 2000–2100。存为 UTC。可以是过去的时间（补登） |
| `location` | 纯文本，去首尾空白后 ≤200 个字符，不含控制字符，可空；空串存为 null。页面按文本显示，不生成链接 |
| `version` | 乐观并发，同任务：PATCH 需带 `expected_version`，不一致 409 `version_conflict` |

响应还带：`past`（开始时间早于服务器当前时刻）、`created_by_name`、`editable`、`deletable`（按调用者）。客户端不能提交 `created_by`、`version`、`past` 等字段（422）。

### 2.3 总览里的下一次组会

`next_meeting`：工作区内**活跃**项目的组会中，开始时间不早于服务器当前时刻、最早的一个；并列时按创建时间、再按 ID，保证稳定。没有则为 null。字段：`id`、`title`、`starts_at`、`location`、`project_id`、`project_name`、`seconds_until`（服务端在同一请求里算，向下取整，≥0）。

归档项目的组会不进入倒计时（和任务的处理一致）；已过的组会不进入。

### 2.4 页面

- 项目页新增"组会"面板：列表（未开始的在前按时间升序，已过的在后按时间降序，标"已结束"），有写权限的人有新建表单、编辑和删除；时间输入用浏览器的本地时间。
- 首页在总览数字下面显示"下一次组会"卡片：标题、项目名、本地时间、"还有 N 天 M 小时"（不足一天时显示"还有 M 小时 K 分钟"，不足一分钟显示"不到 1 分钟"）；点击进入项目。没有时写"没有安排组会"。
- 统计口径加一条：倒计时按页面加载时刻算，不会自动走；只含活跃项目里还没开始的组会。

## 3. 数据与接口

新表 `meetings`：`id`、`workspace_id`、`project_id`、`title`、`starts_at`（timestamptz）、`location`（可空）、`created_by`、`version`、`created_at`、`updated_at`；CHECK：标题长度 1–200、地点长度 ≤200；索引 `(project_id, starts_at)`。迁移 0007，迁移漂移检查覆盖。

接口：

- `GET /projects/{id}/meetings`（最多 200 条）
- `POST /projects/{id}/meetings`（201）
- `PATCH /projects/{id}/meetings/{meeting_id}`（`location: null` 清空地点；`title`、`starts_at` 不能置空）
- `DELETE /projects/{id}/meetings/{meeting_id}`（204）
- `GET /workspaces/{id}/overview` 增加 `next_meeting`，`activity` 每项增加 `meetings`

事件：`meeting.created`、`meeting.updated`、`meeting.deleted`，载荷只有变化的字段名，不含标题、地点、时间文字。`activity` 里按前缀 `meeting` 计入新的 `meetings` 类，`total` 为五类之和。

## 4. 验收标准

后端用例用真实 PostgreSQL，不用 mock。

- G1 权限：非成员 404；viewer 读 200、写 403；member 新建、编辑 200/201；他人创建的组会，非创建者的 member 删除 403，admin 删除 204，创建者删除 204。
- G2 归档项目：读 200，新建、编辑、删除都 409 `project_archived`。
- G3 校验：空标题、超长标题、控制字符、无时区偏移的时间、年份越界、超长地点、未知字段（含 `created_by`、`version`、`past`）都 422；空串地点存为 null。
- G4 列表：按开始时间升序；`past` 对过去的组会为真；`editable`/`deletable` 对不同调用者取值不同；`created_by_name` 正确。
- G5 编辑：用旧的 `expected_version` 得到 409 并带 `current_version`；只改所发送的字段；`location: null` 清空；没有变化时不增加版本、不写事件。
- G6 事件：创建、修改、删除各写一条，载荷不含标题或地点文字。
- G7 通过自己项目的路径访问别的项目的组会 ID 得到 404。
- G8 数据库自己也拒绝：直接写库的空标题、超长地点被 CHECK 拒绝。
- G9 持久：创建后在新的数据库会话里能读到，时间与提交的是同一绝对时刻（偏移不同的两种写法得到同一 UTC 值）。
- H1 `next_meeting`：选最早的未来组会；忽略已过的、归档项目的、其他工作区的；并列时稳定；没有时为 null；`seconds_until` 与手工算的相差不超过 5 秒。
- H2 viewer 也能看到 `next_meeting`。
- H3 `activity` 的 `meetings` 与操作数一致，`total` 是五类之和；`recent_events` 里有 `meeting.*` 且不含文字。

**界面（真实浏览器）**

- V1 Given member 在项目页新建组会，Then 列表出现该条，刷新后仍在，时间显示为本地时间。
- V2 Given 3 天 2 小时 30 分钟后的组会，Then 首页显示"还有 3 天 2 小时"，点击进入该项目；刷新后不变。
- V3 Given 新工作区，Then 首页写"没有安排组会"，没有 NaN 或 undefined。
- V4 Given 编辑和删除，Then 刷新后保持；viewer 看到列表但没有新建、编辑、删除控件；过去的组会标"已结束"且不进倒计时。
- V5 Given 归档项目，Then 面板只读，首页倒计时不含该项目的组会。

## 5. 技术决策与风险

决策见 [DECISIONS.md](./DECISIONS.md) 的 D-023。

| 风险 | 处理 |
| --- | --- |
| 时区错乱，会议显示错一天 | 只存带偏移的绝对时刻；不带偏移的拒绝；页面用浏览器时区显示；G9 验证两种写法同一时刻 |
| 页面上的倒计时与服务器不同步 | 页面只显示服务端给的秒数，不用本机时钟；加载时刻之后不走 |
| 地点文字被当成链接或脚本 | 纯文本，React 按文本渲染，不解析链接；V1 之外在后端用含标记文字的用例检查原样存取 |
| 与任务一致的权限被误改 | 复用同一套角色判断；G1 覆盖 |

**未验证的假设**：用户的浏览器时区正确；夏令时切换日的显示没有专门测试。

## 6. 实施计划与完成定义

1. 迁移 0007、模型、接口、总览扩展。
2. 后端用例 G1–G9、H1–H3，含可失败性检查。
3. 前端：项目页面板、首页卡片、口径说明、活动表加一列。
4. Playwright V1–V5。
5. 文档：映射表、README、D-023、本节证据。

**完成定义**：第 4 节每一条有自动化证据；`bash scripts/check.sh` 与 `scripts/e2e.py` 通过；HOME-10 标"进行中"（没有提醒通知，没有录音纪要）。

## 7. 验收证据

记录日期 2026-11-02，本机 macOS arm64，uv，Node 24，Python 3.12，内嵌 PostgreSQL。

### 7.1 自动检查

| 命令 | 结果 |
| --- | --- |
| `bash scripts/check.sh` | 退出码 0，`all checks passed`。secrets 160 个文件 0 问题；ruff、mypy（41 个源文件）通过；pytest（含迁移漂移检查，覆盖迁移 0007）370 passed、12 skipped（10 个 Docling、2 个在线契约，均未设对应环境变量）；前端类型检查与构建通过 |
| `backend/.venv/bin/python scripts/e2e.py` | 68 passed，其中本切片 5 个（`web/e2e/meetings.spec.ts` 的 V1 至 V5），其余无回归 |

### 7.2 标准与证据

后端用例在真实 PostgreSQL 上，没有 mock；文件 `backend/tests/test_meetings.py`，27 个用例（G3 的参数化占 13 个）。`test_overview.py` 的 T1、T5 更新为含 `meetings` 键。

| 标准 | 证据 |
| --- | --- |
| G1 | `test_g1_roles`（viewer、非成员、member、非创建者 member 删除 403、owner 删除 204、创建者删除 204） |
| G2 | `test_g2_an_archived_project_is_readable_and_not_writable`（读 200；新建、编辑、删除 409 `project_archived`；`editable`、`deletable` 均为假） |
| G3 | `test_g3_invalid_bodies_are_refused`（13 种坏请求，含空标题、超长、控制字符、无偏移时间、年份两侧越界、未知字段 `created_by`/`version`/`past`、缺时间）、`test_g3_limits_are_inclusive_and_an_empty_place_is_none`（200 字符边界、空白地点存为 null、含标记的文字原样存取） |
| G4 | `test_g4_listing_is_by_start_time_and_flags_follow_the_caller` |
| G5 | `test_g5_editing_is_versioned_partial_and_idempotent`（旧版本 409 带 `current_version`、只改所发字段、`location: null` 清空、无变化不加版本、`title`/`starts_at` 置空 422） |
| G6 | `test_g6_events_name_the_change_and_hold_no_words` |
| G7 | `test_g7_a_meeting_is_not_reachable_through_another_project` |
| G8 | `test_g8_the_database_refuses_what_the_api_would`（直接写库，空标题、超长标题、空地点、超长地点都被 CHECK 拒绝） |
| G9 | `test_g9_the_instant_is_kept_whatever_offset_it_was_sent_in`（`+08:00` 与 `Z` 两种写法得到同一 UTC 值，并直接查表确认） |
| H1 | `test_h1_next_meeting_is_the_earliest_that_has_not_started`（已过、归档项目、其他工作区都被忽略；`seconds_until` 与手算相差小于 5 秒）、`test_h1_ties_are_broken_by_creation_order`、`test_h1_deleting_the_next_one_moves_to_the_following`；没有时为 null 在同一文件的第一条断言 |
| H2 | `test_h2_a_viewer_sees_the_next_meeting` |
| H3 | `test_h3_activity_counts_meetings_and_the_total_adds_up`（`meetings` 为 3，`total` 是五类之和，`recent_events` 里有三种 `meeting.*` 且不含文字） |
| V1 | `V1: a meeting is added, shown in local time, and still there after a reload`（浏览器时区固定为 Asia/Shanghai，显示 2030/6/1 09:30；标题和地点含 HTML 标记，按文字显示） |
| V2 | `V2: the home page counts down…`（3 天 2 小时 30 分后的组会，首页显示"还有 3 天 2 小时"，刷新不变，点击进入该项目；更远的一个不被选中） |
| V3 | `V3: a new workspace says there is no meeting…`（"没有安排组会"，页面文字里没有 NaN、undefined、null） |
| V4 | `V4: edit and delete survive a reload…`（编辑、删除刷新后保持；viewer 看到 2 条，没有表单、编辑、删除；已过的标"已结束"，排在未开始的后面，删除将来的之后首页不再有倒计时） |
| V5 | `V5: an archived project's panel is read-only…`（归档后面板无表单、编辑、删除；首页倒计时不含它） |

**可失败性检查**：逐个改坏实现，`test_meetings.py` 与 `test_overview.py` 应失败。19 处改动中 17 处被抓住：归档项目可写、任何人可删、不查版本、无变化也升版本、跨项目可访问、viewer 可写、`past` 取反、事件带标题、倒计时含已过的、含归档项目的、取最晚的、并列顺序反了、`seconds_until` 偏移、活动不计组会、接受无偏移时间、年份不限、`editable` 不看归档（见下）。

第一轮漏掉 3 处，其中 `editable`/`deletable` 不看归档是用例缺口：G2 只断言了状态码，没断言这两个标志。已在 G2 里补上断言，改动重做后被抓住（上面的 17 处含它）。仍未被抓住的 2 处：

- 倒计时查询去掉工作区条件：没有被抓住。`active` 项目集合本来就只来自当前工作区，这个条件与它重复，改动不改变结果。保留作纵深防御。
- 不转成 UTC（`return value`）：没有被抓住。Postgres 的 `timestamptz` 会自行归一化，列表和库里读到的是同一时刻，所以 G9 通过。这个转换保证的是响应里的时间字符串统一为 UTC，当前没有用例断言响应的偏移格式。

每次改动后已还原。

**过程中发现并修复的缺陷**：项目页在同一页面归档项目后，组会列表不会重新读取，已加载的行仍带 `editable: true`，页面上还显示编辑和删除按钮，点了只会得到 409。V5 发现，修复是让列表的读取键包含 `canWrite`，归档后重新读取。

### 7.3 未验证与已知限制

- 倒计时只在页面加载时算一次，不会自动走；页面停留时间长了会变旧，刷新才更新（D-023）。
- 夏令时切换日的显示没有专门测试；e2e 只固定了 Asia/Shanghai 一个时区，只在 Chromium 上跑过。
- 响应里的 `starts_at` 偏移格式没有用例断言（见上，不转 UTC 的改动未被抓住）。
- 没有提醒通知、重复组会、日历导入导出、会议链接，没有录音、转写、纪要（MEET-*）。HOME-10 因此保持"进行中"。
- 项目页的组会列表最多 200 条，没有分页。
- 没有在窄屏下检查面板和首页卡片的显示。
