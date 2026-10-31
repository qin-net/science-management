# 001 P0.1 / P0.2：工作区与 PDF 文献导入纵向切片

- 状态：进行中（验收证据见第 7 节）
- 需求映射：`PROJ-01`（部分）、`READ-01`（部分）、`READ-02`（部分）、`READ-08` 的定位基础、审计项 `AUDIT-11`（部分）
- 依赖：[000-foundation.md](./000-foundation.md)

## 1. 问题、目标、范围

**问题**：研究生的文献散落在本地文件夹，和项目、任务、证据都没有关联。后续的问答、矩阵、写作都要求原文可定位，所以第一步必须把真实 PDF 变成“有版本、有归属、可定位”的科研对象。

**目标**：用户登录后创建项目，上传真实 PDF，观察真实处理状态，在阅读器里打开 PDF，点击解析出的文本块能跳到原文位置；失败可见、可恢复；重启后数据仍在；其他 Workspace 的用户读不到。

**范围内**

- 账号注册/登录/退出，服务端会话
- Workspace、成员与角色；项目创建、编辑、归档
- PDF 上传、校验、去重、版本、元数据
- 持久化解析任务：排队、租约、超时、重试、取消、恢复
- pdfium 文本块提取与页内坐标；PDF.js 阅读器与块定位
- 活动事件日志

**非目标**（有明确去向，不属于本切片）

| 不做 | 去向 |
| --- | --- |
| 结构识别（章节、表格、图表、公式） | Docling 适配，见 [spikes](./spikes/2026-10-30-upstream-candidates.md) 与第 6 节 |
| 文献检索、DOI/arXiv 导入 | P0.3 |
| 翻译、总结、批注、笔记 | P0.4 |
| 任务、助手、问答 | P0.5 |
| 驾驶舱 | P0.6 |
| OIDC/Keycloak | D-003，生产部署前 |
| 项目级成员、邀请、权限撤销审计 | P2.4 |
| 向量化与分块入库 | 随 P0.5 问答一起设计，否则分块策略无验证依据 |

## 2. 用户场景与行为

### 2.1 角色权限

权限以 Workspace 成员关系为边界，**每次请求都在服务端校验**，不缓存。

| 操作 | viewer | member | admin | owner |
| --- | --- | --- | --- | --- |
| 读项目、文献、块、文件、事件 | ✓ | ✓ | ✓ | ✓ |
| 建/改/归档项目，上传、改元数据、重解析、取消、删除文献 | | ✓ | ✓ | ✓ |
| 增删成员、改角色（owner 角色除外） | | | ✓ | ✓ |
| 授予/移除 owner | | | | ✓ |

- 非成员访问时返回 404，与资源不存在相同，不泄露存在性。
- 成员但角色不足时返回 403 `forbidden`。成员已知资源存在，403 不泄露新信息，还能给出明确提示。
- 最后一位 owner 不可移除或降级。
- 资源路径嵌套在项目下；资源不属于路径中的项目时返回 404，这就是跨项目隔离。
- **已知范围限制**：同一 Workspace 的成员能看到其全部项目，项目级 ACL 属于 P2.4。

### 2.2 导入流程与状态

```
上传 → 校验(大小/魔数) → 落盘 → 同事务写 Paper+Version+Job+Event → 202
                                       ↓
   Worker 领取 → 子进程解析 → 同事务写 Block+元数据+Job 成功+Event
```

任务状态：`queued → running → succeeded | failed | cancelled`；`running → queued` 表示重试等待。

| 情况 | 用户看到的 | 错误码 | 可恢复方式 |
| --- | --- | --- | --- |
| 排队中 | 等待 Worker；Worker 离线时明确提示 | | 启动 Worker |
| 解析中 | 阶段名、尝试次数；pdfium 不提供百分比，不伪造进度条 | | 可取消 |
| 文件超限 | 上传立即失败，不留记录 | `file_too_large` 413 | 换文件 |
| 非 PDF | 上传立即失败，不留记录 | `not_a_pdf` 415 | 换文件 |
| 同项目重复文件 | 指向已有文献 | `duplicate_file` 409 | 打开已有文献 |
| 加密/损坏 | 解析失败，文件保留 | `pdf_unreadable` | 上传新版本或删除 |
| 无可提取文本（扫描件） | 解析失败，文件仍可阅读 | `no_extractable_text` | 改用 OCR 解析器（配置后）或上传新版本 |
| 部分页无文本 | 成功，带警告列出页码 | | |
| 解析超时 | 重试至上限后失败 | `parse_timeout` | 手动重试 |
| 解析进程崩溃 | 重试至上限后失败 | `parser_crashed` | 手动重试 |
| 解析器成功退出但无有效块 | 失败，不当作成功 | `parser_empty_output` | 手动重试 |
| Worker 中途死亡 | 租约过期后重领，超限失败 | `worker_lost` | 手动重试 |
| 存储文件丢失 | 失败不重试 | `file_missing` | 上传新版本 |
| 用户取消 | `cancelled`；运行中先显示“已请求取消” | | 手动重试 |

### 2.3 页面

| 路由 | 内容 | 空/加载/失败/拒绝 |
| --- | --- | --- |
| `/login` | 登录、注册 | 表单错误就地显示 |
| `/` | 项目列表与创建 | 无项目时引导创建；后端不可达时显示错误与重试 |
| `/projects/[id]` | 项目信息、上传、文献列表、成员、活动 | 无文献时引导上传；404 显示“不存在或无权访问” |
| `/projects/[id]/papers/[paperId]` | 三栏阅读器：解析块 / PDF / 元数据与版本 | 解析未完成时 PDF 仍可读；块区显示真实状态与错误；页级块不画框 |

## 3. 数据与接口

### 3.1 实体

全部使用 UUID，业务实体带 `workspace_id`，项目内资源带 `project_id`。定义见 `backend/researchos/models.py`，迁移见 `backend/migrations/versions/`（目录不叫 `alembic`，避免和同名包冲突）。

| 实体 | 要点 |
| --- | --- |
| `users` / `auth_sessions` | 密码 Argon2id；会话只存 token 的 SHA-256 |
| `workspaces` / `workspace_members` | 角色 owner/admin/member/viewer |
| `projects` | `version` 乐观锁；归档后拒绝写入 |
| `papers` | 元数据可为 null 表示未知；`metadata_provenance` 记录每个字段来源；项目内 DOI 唯一 |
| `paper_versions` | **不可变**；项目内 sha256 唯一；更新文件必建新版本 |
| `jobs` | 租约、尝试次数、取消标志、错误码；同一对象同时只有一个活动任务（部分唯一索引） |
| `paper_blocks` | 写一次；按解析任务归属，`current_parse_job_id` 指向当前有效解析 |
| `research_events` | 追加写，与业务变更同事务 |
| `worker_heartbeats` | 健康检查据此判断 Worker 是否存活 |

**坐标约定**：`bbox = [x0, y0, x1, y1]`，相对**显示页面**（已应用裁剪框和 `/Rotate`，与 PDF.js viewport 一致）归一化到 0–1，原点左上。无法定位时 `bbox = null` 且 `granularity = "page"`，前端只跳页不画框。

实测（见 [spike](./spikes/2026-10-30-upstream-candidates.md)）：PDFium 的文字矩形始终在未旋转的用户空间，不随裁剪框和 `/Rotate` 变化，直接归一化在旋转页和裁剪页上是错的。`parsers/base.py:display_bbox` 负责换算，C2 用渲染墨迹校验。

**块的粒度**：`pdfium` 产出 `text_line`，按字符遍历成行，不用 `count_rects`：后者在 15 页真实论文上把一行拆成多段，且 `get_text_bounded` 在 7 页上读出重复字符。行内上下标会被 PDFium 拆到单独的行，这是已知限制，不做伪造的段落合并。

**迁移策略**：Alembic 单向追加。发布前运行 `alembic upgrade head`；健康检查比对库内版本与代码 head，不一致即 not ready。

### 3.2 API（前缀 `/api/v1`）

错误统一为 `{"error": {"code", "message", "details", "request_id"}}`。

| 方法与路径 | 说明 | 角色 |
| --- | --- | --- |
| `POST /auth/register` | 注册并建个人 Workspace | 公开，可关闭 |
| `POST /auth/login` · `POST /auth/logout` · `GET /auth/me` | 会话 | |
| `GET/POST /workspaces` | 我的 Workspace | 登录 |
| `GET/POST /workspaces/{id}/members` | 列出/添加已注册用户 | viewer / admin |
| `PATCH/DELETE /workspaces/{id}/members/{user_id}` | 改角色/移除 | admin |
| `GET/POST /workspaces/{id}/projects` | 项目列表/创建 | viewer / member |
| `GET/PATCH /projects/{id}` | 详情/编辑；`expected_version` 不符返回 409 | viewer / member |
| `GET /projects/{id}/events` | 活动 | viewer |
| `GET/POST /projects/{id}/papers` | 列表（分页、状态筛选）/上传，上传返回 202 | viewer / member |
| `GET/PATCH/DELETE /projects/{id}/papers/{pid}` | 详情/改元数据/删除 | viewer / member |
| `GET/POST /projects/{id}/papers/{pid}/versions` | 版本列表/上传新版本 | viewer / member |
| `GET .../versions/{vid}/file` | PDF 原文，`nosniff` + `sandbox` | viewer |
| `GET .../versions/{vid}/blocks` | 当前解析的块，可按页、分页 | viewer |
| `POST .../versions/{vid}/parse` | 手动重试/重解析，返回 202 | member |
| `POST /projects/{id}/jobs/{jid}/cancel` | 取消 | member |
| `GET /health/live` · `GET /health/ready` | 见 000 | 公开，不含敏感信息 |

开发文档 14.4 的 `POST /api/v1/papers/import` 调整为嵌套路径，原因见 D-006。

**上传请求**（D-011）：请求体是 PDF 原始字节，`Content-Type: application/pdf`，文件名放在查询参数 `filename`，不存在时记为 `upload.pdf`。服务端边收边计数，超限立即中止；`Content-Length` 已超限时不读取请求体。响应 202：

```json
{"paper": {"id": "...", "current_version": {"...": "..."}, "parse_job": {"status": "queued"}}}
```

**列表响应**附带 `worker_online`：最近 `WORKER_STALE_SECONDS` 内有 Worker 心跳。前端据此提示“排队但没有 Worker 在运行”。

**删除**：存在活动任务时返回 409 `job_active`，须先取消。先提交数据库，再删文件；崩溃时最多留下孤儿文件，不会有悬空引用。事件保留。

**解析进程环境**：只传 `PATH`、`LANG`、`HOME`、`PYTHONPATH`，不传 `DATABASE_URL` 等配置，解析器拿不到数据库凭据。

## 4. 验收标准

**账号与权限**

- A1 Given 新用户，When 注册，Then 建立个人 Workspace 且本人为 owner，响应带 HttpOnly 会话 Cookie，库内不含明文密码或明文 token。
- A2 Given 错误密码或不存在的邮箱，When 登录，Then 返回相同的 401。
- A3 Given 已退出或已过期的会话，When 访问，Then 返回 401。
- A4 Given 用户 B 不在 Workspace X，When 访问 X 的项目、文献、块、文件、事件、任务，或对其写操作，Then 全部 404 且无副作用。
- A5 Given viewer，When 上传、编辑、重解析、取消、删除，Then 403；读取成功。
- A6 Given 成员被移除，When 使用仍有效的会话再次访问，Then 立即 404。
- A7 Given 同 Workspace 的项目 P1、P2，When 用 P2 路径访问 P1 的文献，Then 404。
- A8 Given 唯一 owner，When 将其移除或降级，Then 409。
- A9 Given 跨站 Origin，When 发起写请求，Then 403 `csrf_rejected`。

**导入**

- B1 Given 真实 PDF，When 上传，Then 返回 202，文件 sha256 与上传一致，事件已记录。
- B2 Given 超限（含不带 Content-Length 的分块上传）、非 PDF、空文件，When 上传，Then 返回 413/415，不留数据库记录和残留文件。
- B3 Given 同项目相同文件，When 再次上传，Then 409 并指向已有文献；并发上传也只留一份。不同项目可各存一份。
- B4 Given 恶意文件名（`../../x.pdf`、控制字符），When 上传，Then 存储路径不含用户输入，下载头安全。
- B5 Given 已有文献，When 上传新版本，Then 版本号递增、旧版本和旧块不变。
- B6 Given 用户修改过的元数据，When 解析完成，Then 不被 PDF 内嵌元数据覆盖。缺失字段保持 null，不用文件名冒充标题。
- B7 Given 重复 DOI，When 修改元数据，Then 409 并指向已有文献；DOI 大小写和 `https://doi.org/` 前缀归一化。

**解析任务**

- C1 Given 真实论文，When 解析，Then 块带页码、坐标、内容 hash，`succeeded`。
- C2 Given 已知位置文字的 PDF（含旋转页、裁剪框偏移页），When 解析，Then bbox 与渲染出的墨迹位置一致（容差写在测试中）。
- C3 Given 损坏/加密/扫描件，Then 分别得到 2.2 的错误码；**绝不**以空结果标记成功。
- C4 Given 解析中杀掉 Worker，When 新 Worker 启动，Then 租约过期后恢复，块不重复。
- C5 Given 旧 Worker 租约已被接管，When 旧 Worker 迟到提交，Then 提交被拒，不写入任何块（fencing）。
- C6 Given 解析进程超时或崩溃，Then 重试有上限，到限后失败并记录错误码。
- C7 Given 运行中任务，When 取消，Then 进程被终止，状态 `cancelled`，无块写入。
- C8 Given 活动任务存在，When 重复请求解析，Then 409，且只有一个活动任务。
- C9 Given 重解析成功，Then `current_parse_job_id` 切到新任务；失败时旧块继续有效。
- C10 Given 已入库数据，When 重启 API、Worker、数据库，Then 数据与文件仍在。

**阅读器（真实浏览器）**

- D1 Given 已解析论文，When 打开阅读器，Then PDF.js 渲染 PDF，块列表来自后端。
- D2 Given 点击某块，Then 跳到对应页，高亮框覆盖该块文字（用真实渲染的墨迹校验）。
- D3 Given 解析失败的文献，Then PDF 仍可读，界面显示真实错误，块区无假内容。

## 5. 技术决策与风险

决策编号见 [DECISIONS.md](./DECISIONS.md)。

| 风险 | 处理 |
| --- | --- |
| 恶意 PDF 触发解析器漏洞 | 子进程隔离、超时、重试上限；**不是沙箱**，公开多用户部署前需容器隔离 |
| 解析器崩溃毒化队列 | 每次领取都计入尝试次数，崩溃也不会无限循环 |
| 上传撑爆磁盘 | 流式字节计数，超限中止；总配额未做 |
| PDF 内嵌标题是垃圾值 | 标来源为 `pdf_metadata`，可编辑 |
| 登录暴力破解 | **未做限流**，已知限制，列入下一步 |
| 文件与数据库不一致 | 先落盘后提交；提交失败清理；进程在两步之间崩溃会留孤儿文件，已知限制 |

**未验证的假设**：pdfium 文本块在复杂版式（多栏、公式）下的阅读顺序未评估；10 篇多样化样本未建立（第 7 节写明实际数量）。

## 6. 实施计划与完成定义

1. 数据模型与迁移 → 2. 认证与权限 → 3. 项目与成员 API → 4. 存储与上传 → 5. Worker 与解析 → 6. 前端 → 7. 浏览器验收。

**完成定义**：第 4 节全部有自动化或实测证据；README 可从零启动；映射表已更新。

## 7. 验收证据

记录日期 2026-10-31，环境同 [000-foundation.md](./000-foundation.md) 第 7 节。

**后端**：`bash scripts/check.sh` 中 pytest 为 114 passed、10 skipped（跳过的是需要 `TEST_DOCLING_PYTHON` 的 Docling 用例）。**浏览器**：`backend/.venv/bin/python scripts/e2e.py`，退出码 0，Playwright 20 passed（45.8s；`reader.spec.ts` 15 个，新增 `management.spec.ts` 5 个），使用真实 PostgreSQL、API、Worker 和 Next.js 生产构建，无 mock。首次运行因缺 Chromium headless shell 全部失败，执行 `npm --prefix web exec -- playwright install chromium` 后通过。

| 标准 | 证据 |
| --- | --- |
| A1–A9 | `test_auth_permissions.py` 中同名 `test_a1_` 到 `test_a9_` |
| B1–B7 | `test_import.py` 中同名 `test_b1_` 到 `test_b7_` |
| C1、C3 | `test_parsing.py::test_c1_*`（含 `test_c1_real_paper`，样例 `samples/pdfs/1706.03762.pdf`）、`test_c3_*`（corrupt、encrypted、scanned） |
| C2 | `test_parsing.py::test_c2_bbox_matches_rendered_ink`，旋转 0/90/180/270，含裁剪框 |
| C4–C9 | `test_jobs.py` 中同名 `test_c4_` 到 `test_c9_` |
| C10 | `test_restart.py::test_c10_data_survives_restarting_everything` |
| D1–D3 | `web/e2e/reader.spec.ts`：导入并阅读且刷新后仍在、块跳转与高亮、旋转与裁剪下高亮贴合墨迹（6 种）、解析失败显示真实错误、损坏与加密文件 |
| 界面控件 | `web/e2e/management.spec.ts`：项目改名与目标、归档后只读且刷新仍保持、恢复；元数据保存并刷新后仍在、DOI 规范化、非法 DOI 被拒、同项目重复 DOI 显示 409 提示且不保存；上传新版本得到 v2（当前）、v1 仍可切换、重复文件被拒；成员添加（含未注册邮箱提示）、改角色后对方权限随之变化、移除后对方得到 not-found；最后一位所有者不能被移除或降级 |
| 额外 | 上传被拒原因可见、非成员看到 not-found 且 viewer 无写入控件、会话过期回到登录页（均在 e2e 中通过） |

**范围与限制**

- 块级定位的样例只有一篇真实论文（Attention Is All You Need）。其余用例使用生成的 PDF。覆盖面窄，换一批版式不同的论文可能暴露问题。
- 解析只用 pdfium，没有章节、表格、图表、公式结构，Docling 未接入（D-002）。
- C4 到 C7 的测试用的是测试内的假解析进程来制造崩溃和超时，Worker 与租约逻辑是真实的。
- 前端没有单元测试，只有上述 e2e。

**未完成，P0.1 / P0.2 不能标完成**

- 根 README 已补，并在本机干净克隆里重放通过（见 000 第 7 节 E1）；未在另一台机器验证。
- 界面控件核对（读了 `web/src` 的页面代码，对照 2.3 节）：项目编辑与归档、成员增删与改角色、活动列表、上传、取消解析、重新解析、删除、元数据编辑（标题、作者、年份、DOI、arXiv ID，显示来源）、上传新版本，控件都存在。
- 此前缺的浏览器用例已补（见上表「界面控件」），每个用例都在操作后刷新或换另一个用户重新读取，只改本地状态的控件过不了。把项目名输入框的 `disabled={archived}` 临时去掉后归档用例失败，已还原。
- **仍没有浏览器用例：取消解析。** pdfium 解析 2000 页 PDF 只要约 0.1 秒，任务在能点按钮之前就已结束，界面无法稳定制造「排队中或解析中」的状态。取消的行为由 `test_jobs.py::test_c7_*` 在后端验证，按钮本身未经浏览器验证。要补，需要一个不被 Worker 领取的排队任务（例如不带 Worker 的另一套栈）。
- 助手栏、笔记、批注属 P0.4/P0.5。

下一步：取消解析按钮是已知缺口；其余缺口见上。是否把 P0.1/P0.2 标完成，仍待决定，状态保持「进行中」。
