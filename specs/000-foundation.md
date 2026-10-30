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

见文末，阶段结束时填写。
