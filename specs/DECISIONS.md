# 技术决策记录

格式：背景 → 决定 → 后果。偏离开发文档的决定必须写明原因。

## D-001 本机无 Docker，用内嵌 PostgreSQL 做开发和测试数据库

- **背景**：开发文档选 PostgreSQL + pgvector，SQLite 会让后续的部分唯一索引、JSONB、`SKIP LOCKED`、向量能力无法验证。本机没有 docker、podman、psql、brew。
- **决定**：开发和测试用 `pgserver` 0.1.4 内嵌的真实 PostgreSQL 16.2 + pgvector 0.6.2（已实测启动并 `create extension vector`）。业务代码只依赖 `DATABASE_URL`；另提供 `docker-compose.yml`，但本机无法验证，标为未验证。**不使用 SQLite 替代。**
- **后果**：`pgserver` 只是开发依赖，不进入生产路径；pgvector 版本较旧，P0.5 引入向量前要复核是否满足需要。

## D-002 PDF 默认解析器用 pypdfium2，Docling 作为可选独立环境解析器

- **背景**：开发文档首选 Docling，但它依赖 torch 和模型下载，环境约 1.2 GB，主环境引入成本高。阅读器定位只需要可靠的文字坐标。
- **决定**：内置 `pdfium` 解析器，秒级返回真实文字与坐标，但不识别结构。Docling 放在独立 Python 环境，经子进程调用，由 `DOCLING_PYTHON` 启用。解析器都走同一块契约。实测结果见 [spike 记录](./spikes/2026-10-30-upstream-candidates.md)。
- **后果**：默认切片没有章节、表格、公式识别，界面和警告如实说明。`READ-02` 保持“进行中”。

## D-003 账号先用内置邮箱密码，Keycloak 延后

- **背景**：开发文档 P0 选 Keycloak，需要 Java 服务；本机无容器环境，P0 切片会被部署阻塞。
- **决定**：P0 用内置账号（Argon2id、服务端会话、HttpOnly Cookie）。权限判断集中在 `security.py`，以后替换身份来源时不影响资源授权。
- **后果**：无账号恢复、无限流。不适合公网部署。生产前必须接入 OIDC 或补齐这些能力，已列入下一步。

## D-004 任务队列用 PostgreSQL 表 + 租约，暂不引入 Redis/Temporal/LangGraph

- **背景**：开发文档 12.1 建议先用 Outbox 与事件表，14.3 要求租约与心跳。
- **决定**：`jobs` 表用 `FOR UPDATE SKIP LOCKED` 领取，租约续期；完成提交用 `lease_owner + attempts` 做 fencing，迟到的旧 Worker 无法写入。业务变更和事件在同一事务。
- **后果**：吞吐足够 P0。LangGraph 在 P1.2 引入，负责 Agent 内层，不替代这张表。

## D-005 解析在子进程运行

- **背景**：线程里跑解析器无法强制超时和取消，原生库崩溃会带走 Worker；Docling 也必须在另一个 Python 环境运行。
- **决定**：所有解析器都是子进程，结果写临时 JSON。Worker 负责超时、取消（杀进程组）和续租。
- **后果**：每次解析多一次进程启动。子进程隔离**不是安全沙箱**。

## D-006 文献 API 嵌套在项目路径下

- **背景**：开发文档 14.4 的 `POST /api/v1/papers/import` 是拟定契约，项目归属要放在请求体里。
- **决定**：改为 `/api/v1/projects/{id}/papers`，归属由路径决定，资源不属于该项目时直接 404。DOI/arXiv 导入在 P0.3 再定入口。
- **后果**：跨项目隔离由路由结构直接保证。

## D-007 前端暂不引入 Tailwind 与 shadcn/ui

- **背景**：开发文档选 shadcn/ui + Tailwind，但切片页面很少，首要风险是 PDF.js 定位。
- **决定**：用 CSS 变量落实文档 12.2 的配色，组件手写。页面增多（P0.6 驾驶舱）时再引入。
- **后果**：之后需要一次样式迁移，代价可控。

## D-008 成员权限只到 Workspace 级

- **背景**：PROMPT P0.1 要求成员/权限边界和跨项目隔离；P2.4 才是完整的项目成员授权。
- **决定**：P0 的成员关系在 Workspace 级；项目隔离指资源不能经其他项目路径访问。项目级 ACL 留给 P2.4。
- **后果**：同 Workspace 成员互相可见全部项目。这是产品边界，界面和规格都写明，避免被理解成项目级私密。

## D-009 OpenAlex 必须按付费额度设计

- **背景**：开发文档写“上线时核实 key、额度与费用”。2026-10-30 实测：无 key 时同一 IP 共享每日 $0.10 预算，响应头 `x-ratelimit-cost-usd: 0.001`，全文 `search` 单次 $0.001，过滤查询实测 $0.0001；免费 key 每日 $1。
- **决定**：P0.3 的适配器必须支持 `OPENALEX_API_KEY`，读取 `x-ratelimit-*` 头，把预算耗尽（429）作为来源降级如实展示，并缓存响应。
- **后果**：P0.3 规格须包含额度处理。

## D-011 上传用原始请求体而不是 multipart

- **背景**：`python-multipart` 解析时会先把内容写入临时文件，无法在接收中途按项目权限和大小拒绝，也多一个依赖。
- **决定**：`POST /projects/{id}/papers` 的请求体就是 PDF 字节，文件名走查询参数。先校验登录和权限，再读请求体；已移除 `python-multipart`。
- **后果**：无法在一个请求里传多个文件或附带字段，批量上传由前端逐个发起。

## D-012 CSRF 用来源白名单

- **背景**：前端经 Next.js rewrite 代理 API，浏览器发出的 `Origin` 是前端地址，后端收到的 `Host` 可能是内部地址，原来的 Origin==Host 判断会拒绝合法请求。
- **决定**：写请求的 `Origin` 必须在 `ALLOWED_ORIGINS` 内，或与 `Host` 同源；无 `Origin` 的请求放行（非浏览器客户端），会话 Cookie 同时用 `SameSite=Lax`。登录和注册也经过这一检查。
- **后果**：部署时必须配置 `ALLOWED_ORIGINS`，否则前端写操作全部 403。

## D-010 暂缓 PaperAgent，写作走组装路线的准备

- **背景**：2026-10-30 核查 `songhahaha66/PaperAgent`：根目录无 LICENSE（`raw.../master/LICENSE` 为 404），GitHub 侧栏不显示许可证；2026-10-29 刚合入 2.0 重构（`82f7769`）；要求 Python 3.14+、PostgreSQL、OpenSandbox。
- **决定**：无许可证即不能假定有使用、修改或分发权利，**不下载运行、不复制代码**。P1.4 默认走开发文档第 6 节的组装路线，除非许可明确。
- **后果**：P1.4 规格须重新判断。
