# 000 阶段 0：验证与工程底座

- 状态：进行中（验收证据见第 7 节）
- 需求映射：PROMPT 阶段 0；开发文档第 12、15.2、16 节

## 1. 问题、目标、范围

**问题**：仓库只有需求和方案文档，所列候选都没有在本项目运行过。开发文档第 19 节也明确写了尚未确认运行、许可和接口情况。

**目标**：新环境按 README 可重复启动；配置或依赖错误能得到明确诊断；密钥不入库；健康状态反映真实依赖；上游候选有固定版本和真实结果的记录。

**范围**：工程结构、配置、迁移、日志、统一错误、健康检查、自动检查、候选验证记录、`third_party.lock.yaml`。

**非目标**：部署到生产、容器化全部服务、完成所有候选的集成。

## 2. 行为

| 能力 | 行为 |
| --- | --- |
| `scripts/setup.sh` | 校验工具链，安装依赖，缺 `.env` 时从示例创建 |
| `scripts/dev.sh` | 启动数据库、迁移、API、Worker、Web |
| `uv run python -m researchos.doctor` | 逐项检查配置、数据库、迁移、存储、解析器，失败项给出修复提示，非零退出 |
| `GET /health/live` | 只说明进程存活 |
| `GET /health/ready` | 真实检查数据库、迁移版本、存储、Worker 心跳；任一必需项失败返回 503 |
| 请求日志 | JSON 行，字段白名单，不记录请求体与 Cookie |
| 错误 | 统一结构，带 `request_id`；500 不泄露内部信息 |

## 3. 数据与接口

`GET /health/ready` 响应：

```json
{
  "status": "ready | not_ready",
  "checks": {
    "database": {"status": "ok | failed", "detail": "..."},
    "migrations": {"status": "ok | failed", "detail": "..."},
    "storage": {"status": "ok | failed"},
    "worker": {"status": "ok | failed", "detail": "..."}
  },
  "parsers": {"pdfium": "available", "docling": "not_configured | available | unavailable"}
}
```

`docling` 是可选能力，不影响 `status`。响应不含路径、URL、版本号以外的内部信息。

## 4. 验收标准

- E1 Given 全新克隆，When 按 README 执行，Then 服务启动且 `/health/ready` 为 ready。
- E2 Given `DATABASE_URL` 缺失、格式错误或数据库不可达，When 运行 doctor 或访问 ready，Then 明确指出问题，不输出密码。
- E3 Given 迁移未执行，Then ready 返回 503，并指明需要运行迁移。
- E4 Given Worker 未运行，Then ready 返回 503，`worker` 项失败；数据库停止时同样如实反映。
- E5 Given 仓库内容，When 运行 `scripts/check.sh`，Then 执行 lint、类型检查、测试、需求映射检查，并能发现被提交的 `.env` 或常见密钥形态。
- E6 Given 未处理异常，Then 响应为 500 + `internal_error`，日志含 `request_id`，响应不含堆栈。

## 5. 技术决策与风险

见 [DECISIONS.md](./DECISIONS.md) 的 D-001 至 D-005。

- CI 文件可编写但本环境无法触发运行，须在规格中标为未验证。
- `docker-compose.yml` 本机无 Docker，无法验证，须标为未验证。

## 6. 实施计划与完成定义

工程骨架 → 配置/日志/错误 → 迁移 → 健康检查与 doctor → 脚本 → 候选验证 → 文档。

完成定义：E1–E6 均有证据。

## 7. 验收证据

记录日期 2026-10-31，本机 macOS 24.6 arm64，uv 0.13.6，Node 24.21.0，Python 3.12.16。无 Docker，数据库为内嵌 PostgreSQL（D-001）。

执行 `bash scripts/check.sh`，退出码 0，输出 `all checks passed`：

| 步骤 | 结果 |
| --- | --- |
| secrets | 81 个文件，0 个问题 |
| requirements mapping | 152 个 ID，152 行，0 个问题 |
| ruff | All checks passed |
| mypy | 25 个源文件，无问题 |
| pytest（含迁移漂移检查） | 114 passed，10 skipped（Docling 用例），1 个 Starlette 弃用警告 |
| web typecheck / build | 通过，Next.js 16.4.3 |

| 标准 | 证据 | 状态 |
| --- | --- | --- |
| E1 | 2026-10-31 在 `git clone` 出的干净目录（已提交状态，加本次未提交的 README 与文档）里，依赖全新安装：`bash scripts/setup.sh` 退出码 0，约 10 秒（依赖由 uv 与 npm 缓存提供，没有测断网下载时间）；`bash scripts/dev.sh` 后 `/health/ready` 返回 200（database、migrations、storage、worker 均 ok），Web `/login` 返回 200；经 Web 代理注册、建项目、上传 `samples/pdfs/1706.03762.pdf`，返回 202，解析 `succeeded`，15 页。已停止服务并删除克隆 | 通过（限于本机、有包缓存） |
| E2 | `test_foundation.py::test_e2_*`（缺失、格式错误、不可达三种，不输出密码） | 通过 |
| E3 | `test_e3_doctor_flags_a_schema_that_has_not_been_migrated`；`test_e3_ready_returns_503_and_names_the_fix_when_not_migrated`（空库上 ready 为 503，migrations 项为 `schema is behind; run scripts/migrate.sh`，响应不含库名或连接串）；迁移漂移检查 `test_models_and_migrations_do_not_drift`。把迁移判断临时改成永不触发后 ready 用例失败，已还原 | 通过 |
| E4 | `test_e4_ready_fails_without_a_worker_and_passes_with_one`；`test_e4_ready_reports_a_database_that_stops_after_startup`（另起临时 PostgreSQL，迁移后 ready 为 200，用 `pg_ctl` 真正停掉后为 503，database、migrations、worker 均 failed，响应不含路径或连接串）。把健康检查临时改成恒报 database ok 后该用例失败，已还原 | 通过 |
| E5 | `test_secrets_check.py` 全部用例，加 `check_secrets.py` 对本仓库的扫描 | 通过 |
| E6 | `test_e6_unhandled_error_is_generic_and_correlated` | 通过 |

手动运行 `bash scripts/doctor.sh`（未启动 Worker）：database、migrations、storage 为 ok，worker 为 failed（no worker heartbeat），并给出启动命令；pdfium 可用，docling 为 not_configured。退出码 1，与 E4 的预期一致。

**未完成，阶段 0 不能标完成**

- 候选验证记录已落盘：`specs/spikes/2026-10-30-upstream-candidates.md` 与 `third_party.lock.yaml`。只有 Docling 实际运行过；PaperQA2、Presenton 只核对了版本与许可证，PaperAgent 无许可证。Docling 的冷启动、OCR、表格与公式质量未测，环境也没有安装脚本。适配器有自动化测试（`tests/test_docling_adapter.py`，10 个用例），但需要设置 `TEST_DOCLING_PYTHON`，否则跳过，`check.sh` 默认不带它。
- 干净克隆重放只在本机做过，依赖有本地缓存；没有在另一台机器或断网缓存的环境验证。

下一阶段入口：重新评估是否标完成（界面缺口见 001 第 7 节）。
