# ResearchOS

面向研究生的科研工作台。当前实现了 P0 的前三个切片：账号与工作区、项目、PDF 文献导入、后台解析、带页码和坐标的文本块、PDF 阅读器与块定位，以及 arXiv / OpenAlex / Crossref 文献检索和按 DOI / arXiv ID 导入元数据。

需求见 `功能预想.md`，技术方案见 `ResearchOS开发文档.md`，开发约束见 `PROMPT.md`。规格、需求映射和决策记录在 [`specs/`](specs/README.md)。

## 当前状态

没有任何阶段标为"完成"。阶段 0 与 P0.1/P0.2 的验收证据见 `specs/000-foundation.md`、`specs/001-p0-workspace-paper-import.md` 第 7 节；P0.3 见 `specs/002-p0-literature-search.md` 第 7 节。

| 已实现并有检查 | 未实现或未验证 |
| --- | --- |
| 注册、登录、退出；Workspace 与成员角色 | OIDC/Keycloak、账号恢复 |
| 项目创建、编辑、归档 | 项目级权限（同 Workspace 成员可见全部项目） |
| PDF 上传、校验、去重、版本、元数据 | 自然语言检索、相关性排序、Semantic Scholar 等其他来源 |
| 持久化解析任务：租约、重试、取消、重启恢复 | 章节、表格、图表、公式识别（Docling 未接入默认流程） |
| pdfium 文本块（页码、坐标、内容 hash） | 向量化、多轮对话、翻译 |
| PDF.js 阅读器、点击块跳转并高亮 | 驾驶舱里的进度百分比、「读过」的记录、里程碑时间轴 |
| 项目笔记、文献笔记、块级批注（带原文快照，重新解析后判断是否仍能定位），块内选中一段文字即可批注并标记 | PDF 画布上按区间高亮（解析只有块级坐标）、跨块选区；翻译、动机分析（模型已定，见 D-017/D-021，各自待写规格） |
| 项目任务与看板（状态、负责人、期限、优先级、受阻原因、关联文献、按负责人过滤、并发冲突提示） | 拖拽、依赖、日历、AI 任务草案（模型已定，待写规格） |
| 首页总览：活跃项目、文献、未完成与逾期任务的计数，每个项目的任务状态与最近活动，「我的待办」，最近动态，已解析与有笔记的文献数，最近 14 天按天的活动计数，下一次组会的倒计时（项目页登记组会），页面上有统计口径 | 进度百分比与健康度、AI 日报与建议、阅读量与研究方向、里程碑时间轴、投稿倒计时、组会提醒与重复组会 |
| 引用导出：项目内文献导出为 BibTeX 或 RIS，文字转义后不破坏文件结构，缺失字段不编造 | CSL 格式化文本（APA、GB/T 7714）、勾选部分文献、条目类型区分 |
| 带出处的文献问答（Anthropic Messages API）：分段回答，每段附指向文本块的原文出处，库里取不到的引用被丢弃，无出处的段落明确标注；需配置 `ANTHROPIC_API_KEY`，问题与所选文献全文会发送给 Anthropic | 多轮对话、问答历史、向量检索（文献过长时直接拒绝）、流式输出 |
| 单篇核心贡献总结（中文/英文）：分段、带出处，一键存成文献笔记（无出处的段落标「【无出处】」） | 缓存、多篇综述、分段总结长文 |
| 三源检索（来源状态、去重合并、缓存、限速）；按 DOI / arXiv ID 导入无 PDF 文献 | 自动下载 PDF；OpenAlex key 与 Crossref polite pool 的在线路径 |

## 环境要求

- macOS 或 Linux
- [uv](https://docs.astral.sh/uv/getting-started/installation/)，Python 3.12 由 uv 管理
- Node.js 20.9 或更新（开发时用 24.21.0）
- 不需要 Docker 和本机 PostgreSQL。`DATABASE_URL` 留空时，脚本会启动内嵌 PostgreSQL，数据在 `data/pg`（见 D-001）

## 安装与启动

```bash
bash scripts/setup.sh   # 安装依赖，缺 .env 时从 .env.example 创建，执行数据库迁移
bash scripts/dev.sh     # 同时启动 API(8000)、Worker、Web(3000)
```

打开 http://localhost:3000，注册账号后会自动得到一个个人 Workspace。`Ctrl-C` 停止三个进程，内嵌数据库继续运行，用 `bash scripts/stop_db.sh` 停止。

检查配置和依赖：

```bash
bash scripts/doctor.sh   # 逐项检查数据库、迁移、存储、Worker 心跳，失败项给出修复方法
```

`doctor.sh` 要求 Worker 正在运行。单独运行时报 `worker (no worker heartbeat)` 是预期结果。

健康检查端点：`GET /health/live`（进程存活）、`GET /health/ready`（真实检查数据库、迁移、存储、Worker，任一失败返回 503）。

## 配置

复制 `.env.example` 为 `.env`（`setup.sh` 会做），所有变量都有说明。`.env` 已被 git 忽略，不要提交真实密码或密钥。

- `DATABASE_URL`：留空使用内嵌数据库。生产写法 `postgresql+psycopg://USER:PASSWORD@HOST:5432/researchos`。
- `ALLOWED_ORIGINS`：允许发起写请求的前端来源。部署到其他域名时必须改，否则前端写操作全部 403（D-012）。
- `COOKIE_SECURE`：HTTPS 后面设为 `true`。
- `MAX_UPLOAD_MB`、`STORAGE_DIR`：上传大小上限与文件目录，默认 `data/files`。
- `CONTACT_EMAIL`：写进 User-Agent 与 Crossref 的 `mailto`，让 Crossref 把请求放进 polite pool（未在线验证）。留空也能用。
- `OPENALEX_API_KEY`：留空时与同一出口 IP 的所有请求共用 OpenAlex 每日额度（D-009），界面会提示。key 只放在请求头里（带 key 的在线路径未验证）。
- `SEARCH_USER_PER_MINUTE` 等检索相关项见 `.env.example`。arXiv 的请求间隔不能小于 3 秒。
- `DOCLING_PYTHON`：Docling 独立环境的 Python 绝对路径，留空即禁用。Docling 适配器代码存在，但本仓库没有它的安装脚本，也没有在默认流程中验证过。
- `API_ORIGIN`：Web 转发 `/api` 的目标，默认 `http://127.0.0.1:8000`。它在 `next build` 时编译进产物，要在构建环境里设置，不读 `.env`。

## 使用

1. 注册并登录。
2. 创建项目，填写目标和背景。
3. 在项目页上传 PDF。状态依次为排队、解析中、成功或失败；失败会显示真实错误码，并可重新解析。
4. 点击文献进入阅读器。右侧的文本块来自后端解析结果，点击后跳到对应页并高亮。
5. 解析失败的文献仍可阅读 PDF，块区不会出现占位内容。
6. 在项目页「检索文献」输入关键词。每个来源的状态（正常、超时、被限流、额度用完等）和原因都会列出，某个来源失败不影响其余来源的结果。结果按各来源名次融合排序，不是相关性评分。点「导入」把元数据放进项目，得到一篇「无 PDF」的文献，之后可在文献页上传 PDF。

角色：viewer 只读；member 可上传、编辑、重解析、取消、删除；admin 可管理成员；owner 可管理 owner。非成员访问任何资源返回 404。

## 检查

```bash
bash scripts/check.sh                                 # 密钥扫描、需求映射、ruff、mypy、pytest、前端类型检查与构建
npm --prefix web exec -- playwright install chromium  # 首次运行浏览器验收前执行一次
backend/.venv/bin/python scripts/e2e.py               # 真实数据库、API、Worker 与 Web 构建上的 Playwright 验收
TEST_DOCLING_PYTHON=/path/to/docling/bin/python uv run --directory backend pytest tests/test_docling_adapter.py   # 可选：Docling 适配器，不设置则跳过
TEST_LIVE_SOURCES=1 uv run --directory backend pytest tests/test_literature_live.py -s   # 可选：访问三个真实文献来源，需要外网
```

`check.sh` 会跑完全部步骤再汇总失败项。`e2e.py` 使用独立端口（8100、3100、8101）和独立数据目录，不动开发数据；检索用例访问本地的假文献来源，不访问外网。

## 目录

```
backend/    FastAPI、SQLAlchemy、Alembic 迁移、Worker、解析器适配、测试
web/        Next.js 前端、PDF.js 阅读器、Playwright 用例
scripts/    安装、启动、迁移、检查脚本
specs/      规格、需求映射表、决策记录
samples/    本地测试用 PDF（不入库，见 .gitignore）
data/       本地数据库与上传文件（不入库）
.spike/     上游候选的原始验证文件（不入库），结论见 specs/spikes/
```

## 已知限制

- 笔记在工作区内不分私有；删除文献会连带删除其笔记，没有回收站（specs/003 7.3）。
- 块内区间批注（specs/011，D-024）：PDF 画布上仍按整块高亮，解析结果只有块级坐标；跨块选区不做；一条批注只有一个区间；块内排版变化不会被发现。
- 任务的「逾期」按服务器 UTC 日期算，东八区早上 8 点前仍是前一天；看板只用下拉框改状态，没有拖拽（specs/004 7.3）。
- 首页总览只有计数，没有进度百分比（D-019）；「有笔记的文献」不等于读过，最近 14 天活动按 UTC 日期、含解析的系统事件（specs/009，D-022）；按工作区汇总，项目数以数百为限，没有分页也没有做性能测量（specs/005 7.3）。
- 组会（specs/010，D-023）：时间按带时区的绝对时刻存，页面按浏览器时区显示；首页倒计时只在加载时由服务端算一次，不会自动走；没有提醒通知、重复组会、日历导入导出，没有录音纪要；夏令时切换日没有专门测试。
- 引用导出只用 `bibtexparser`、`rispy` 读回验证过，没有在 Zotero、EndNote、Overleaf 里实际导入，也没有用 `bibtex` 编译；引用键随元数据变化（specs/006 7.3）。
- 文献问答与总结只在真实模型上跑通过一次成功路径（经第三方中转、一份小文件，specs/007 与 008 的 7.4），官方地址直连、真实的错误响应、长文献都没有验证，这些仍只由本地假服务覆盖；回答质量、引用是否真的支持那句话无法由服务端判断，需要用户对照原文；每用户每分钟 5 次、每日 50 次与单次 12 万字符是占位值，等费用上限确定后再调（specs/007 7.3，D-021）。
- 同一 Workspace 的成员能看到其全部项目。
- 上传走原始请求体而非 multipart，一次请求只能传一个文件（D-011）。
- 只有 pdfium 解析，没有章节或图表结构。扫描件和加密 PDF 会明确失败，不会生成空结果。
- 块定位的真实论文样例只有一篇，其余用例使用生成的 PDF。
- 候选验证见 `specs/spikes/` 与 `third_party.lock.yaml`：只有 Docling 实际运行过，PaperQA2 和 Presenton 仅核对版本与许可证，PaperAgent 无许可证。
- 本机没有 Docker，因此依赖 Docker 的候选（如 Presenton）未验证。

## 下一步

1. 评估阶段 0 与 P0.1/P0.2 是否标完成（取消解析按钮的浏览器用例已补，剩余缺口见 specs/001 第 7 节）。
2. 翻译、动机分析、AI 任务草案复用 `researchos/model.py`，各自先写规格（D-017 已解除阻塞）；费用上限待确认。
