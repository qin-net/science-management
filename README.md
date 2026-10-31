# ResearchOS

面向研究生的科研工作台。当前只实现了 P0 的第一个切片：账号与工作区、项目、PDF 文献导入、后台解析、带页码和坐标的文本块、PDF 阅读器与块定位。

需求见 `功能预想.md`，技术方案见 `ResearchOS开发文档.md`，开发约束见 `PROMPT.md`。规格、需求映射和决策记录在 [`specs/`](specs/README.md)。

## 当前状态

没有任何阶段标为"完成"。阶段 0 与 P0.1/P0.2 的验收证据见 `specs/000-foundation.md`、`specs/001-p0-workspace-paper-import.md` 第 7 节。

| 已实现并有检查 | 未实现或未验证 |
| --- | --- |
| 注册、登录、退出；Workspace 与成员角色 | OIDC/Keycloak、账号恢复 |
| 项目创建、编辑、归档 | 项目级权限（同 Workspace 成员可见全部项目） |
| PDF 上传、校验、去重、版本、元数据 | 文献检索（arXiv、OpenAlex、Crossref）、DOI 导入 |
| 持久化解析任务：租约、重试、取消、重启恢复 | 章节、表格、图表、公式识别（Docling 未接入默认流程） |
| pdfium 文本块（页码、坐标、内容 hash） | 向量化、问答、翻译、总结 |
| PDF.js 阅读器、点击块跳转并高亮 | 批注、笔记、任务、驾驶舱 |

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
- `DOCLING_PYTHON`：Docling 独立环境的 Python 绝对路径，留空即禁用。Docling 适配器代码存在，但本仓库没有它的安装脚本，也没有在默认流程中验证过。
- `API_ORIGIN`：Web 转发 `/api` 的目标，默认 `http://127.0.0.1:8000`。它在 `next build` 时编译进产物，要在构建环境里设置，不读 `.env`。

## 使用

1. 注册并登录。
2. 创建项目，填写目标和背景。
3. 在项目页上传 PDF。状态依次为排队、解析中、成功或失败；失败会显示真实错误码，并可重新解析。
4. 点击文献进入阅读器。右侧的文本块来自后端解析结果，点击后跳到对应页并高亮。
5. 解析失败的文献仍可阅读 PDF，块区不会出现占位内容。

角色：viewer 只读；member 可上传、编辑、重解析、取消、删除；admin 可管理成员；owner 可管理 owner。非成员访问任何资源返回 404。

## 检查

```bash
bash scripts/check.sh                                 # 密钥扫描、需求映射、ruff、mypy、pytest、前端类型检查与构建
npm --prefix web exec -- playwright install chromium  # 首次运行浏览器验收前执行一次
backend/.venv/bin/python scripts/e2e.py               # 真实数据库、API、Worker 与 Web 构建上的 Playwright 验收
TEST_DOCLING_PYTHON=/path/to/docling/bin/python uv run --directory backend pytest tests/test_docling_adapter.py   # 可选：Docling 适配器，不设置则跳过
```

`check.sh` 会跑完全部步骤再汇总失败项。`e2e.py` 使用独立端口（8100、3100）和独立数据目录，不动开发数据。

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

- 同一 Workspace 的成员能看到其全部项目。
- 上传走原始请求体而非 multipart，一次请求只能传一个文件（D-011）。
- 只有 pdfium 解析，没有章节或图表结构。扫描件和加密 PDF 会明确失败，不会生成空结果。
- 块定位的真实论文样例只有一篇，其余用例使用生成的 PDF。
- 候选验证见 `specs/spikes/` 与 `third_party.lock.yaml`：只有 Docling 实际运行过，PaperQA2 和 Presenton 仅核对版本与许可证，PaperAgent 无许可证。
- 本机没有 Docker，因此依赖 Docker 的候选（如 Presenton）未验证。

## 下一步

1. 评估阶段 0 与 P0.1/P0.2 是否标完成（界面缺口只剩取消解析按钮，见 specs/001 第 7 节）。
2. P0.3 之前先写规格。
3. P0.3：先写规格，再接入 arXiv、OpenAlex（需处理付费额度，见 D-009）、Crossref。
