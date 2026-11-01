# 002 P0.3：文献检索与元数据导入

- 状态：已实现，验收证据见第 7 节（在线契约测试已跑一次；未验证项见 7.3）
- 需求映射：`LIT-01`、`LIT-02`、`LIT-03`、`LIT-10`（部分）。`LIT-08` 不在本切片，见非目标
- 依赖：[001](./001-p0-workspace-paper-import.md)；决策 D-006、D-009，新增 D-013 至 D-015

## 1. 问题、目标、范围

**问题**：用户现在只能上传手头已有的 PDF。找文献、核对 DOI、补全元数据都要离开系统，找到的文献也无法带着来源进入项目。

**目标**：在项目里按关键词同时检索 arXiv、OpenAlex、Crossref，结果去重合并，每条结果标明由哪些来源给出、哪些字段缺失；任何一个来源超时、限流、额度用尽或返回坏数据，界面都如实说明原因，其余来源的结果照常显示。用户可以按 DOI 或 arXiv ID 把一条文献的元数据导入项目，之后再给它补 PDF。

**范围内**

- 三个来源的适配器：超时、一次重试、限流、额度、坏响应
- 结果标准化、合并、去重、`in_project` 标记
- 响应缓存、跨进程的来源限速、每用户调用上限
- 按 DOI / arXiv ID 导入元数据，生成没有 PDF 的文献记录
- 检索页面与「无 PDF」文献的展示

**非目标**

| 不做 | 原因与去向 |
| --- | --- |
| 自然语言解析、多语关键词生成（LIT-08） | 需要 LLM，本仓库尚未配置任何模型。本切片按用户输入的原样检索，不翻译。中文查询只有 OpenAlex 能给出有意义的结果，见 3.5 |
| 按向量或相关性模型排序 | 还没有嵌入。排序用来源名次的倒数融合，不声称是相关性（D-015） |
| 自动下载 PDF | 开放获取链接来自第三方，服务端按它们取文件有 SSRF 风险。先只保存链接，PDF 用现有上传补 |
| Semantic Scholar、PubMed、DBLP、IEEE/ACM | LIT-04 至 LIT-07，P1 以后 |
| 引用网络、同作者推荐、自动综述 | LIT-11 至 LIT-13 |
| 保存检索历史 | 随 LIT-09 的可追溯检索一起设计。本切片的调用记录不含查询文本 |
| 标题近似自动合并 | 只提示可能重复，不合并（开发文档 LIT-10） |

## 2. 用户场景与行为

### 2.1 权限

| 操作 | viewer | member | admin / owner |
| --- | --- | --- | --- |
| 检索、导入元数据 | 403 | ✓ | ✓ |
| 查看已导入的文献 | ✓ | ✓ | ✓ |

检索不写库，但会消耗全体用户共用的第三方额度（D-009），所以按写权限处理。非成员返回 404，归档项目与现有写操作一样被拒绝，且这两种情况都不会向第三方发请求。

### 2.2 检索流程

```
POST search → 校验 → 权限 → 每用户上限 → 对每个所选来源并行：
   缓存命中? → 是: 直接用
   否: 冷却中/额度用尽/未到间隔? → 是: 记录状态，不发请求
   否: 发请求(超时) → 失败且可重试: 重试一次 → 解析并标准化 → 写缓存
→ 合并去重 → 排序 → 返回 results + 每个来源的状态
```

响应总是 200，只要请求本身合法。结果为空时必须区分三种情形，界面文案不同：

| 情形 | 判断 | 界面 |
| --- | --- | --- |
| 确实没有命中 | 所有所选来源 `ok` 且结果为 0 | 「没有找到匹配的文献」 |
| 部分来源失败 | 至少一个来源非 `ok` | 结果照常显示，失败来源旁写原因 |
| 全部来源失败 | 没有任何来源 `ok` | 「所有来源都没有返回结果」，逐个列原因，不显示「没有找到」 |

### 2.3 来源状态

| `status` | 含义 | 是否重试 | 是否缓存 |
| --- | --- | --- | --- |
| `ok` | 成功，`count` 可为 0 | | 缓存 |
| `timeout` | 超过超时仍无响应 | 重试一次 | 否 |
| `unavailable` | 连接失败或 5xx | 重试一次 | 否 |
| `rate_limited` | 429，带 `retry_after` 秒数 | 不重试，写冷却 | 否 |
| `quota_exhausted` | OpenAlex 剩余额度不足或 429 且头里标明额度，带 `reset_at` | 不重试 | 否 |
| `cooling_down` | 之前被限流，冷却未结束，本次没有发请求，带 `until` | | 否 |
| `throttled` | 本地间隔限制：等不到发送时机，本次没有发请求 | | 否 |
| `bad_request` | 来源返回 400（我们的参数被拒），带来源给出的原因 | 否 | 否 |
| `bad_response` | HTTP 成功但内容不可用：非法 JSON/XML、缺少必需结构、arXiv 的 200 错误条目 | 否 | 否 |

每个来源另带 `message`（面向用户的一句话）、`cached`、`fetched_at`、`duration_ms`。OpenAlex 另带 `quota`（来自响应头的 `remaining_usd`、`reset_at`，没取到就是 null，不估算）。

响应还带 `outcome`，由服务端算出，前端不得自己推断：`results`（有结果）、`no_match`（全部来源 `ok` 且无结果）、`partial`（有来源失败，但仍有结果或其余来源 `ok` 无结果）、`all_failed`（没有任何来源 `ok`）。

### 2.4 结果与合并

每条结果是一篇文献的合并视图。同一篇文献被多个来源返回时合并为一条，合并键依次为：DOI（小写，去 `https://doi.org/` 前缀）、arXiv ID（去版本号）。DOI 形如 `10.48550/arxiv.<id>` 时同时视为该 arXiv ID；OpenAlex 的 `locations[].landing_page_url` 指向 `arxiv.org/abs/<id>` 时也取该 ID。多个键把不同记录连起来时按连通分量合并。

| 字段 | 取值规则（按序取第一个非空） | 记录来源 |
| --- | --- | --- |
| `title`、`authors` | arXiv，Crossref，OpenAlex | 每个字段单独记 `provenance` |
| `year`、`venue` | Crossref，OpenAlex，arXiv（首次提交年份） | 同上 |
| `doi` | Crossref，OpenAlex，arXiv（仅当 arXiv 条目自带 `arxiv:doi`） | 同上 |
| `abstract` | arXiv，Crossref，OpenAlex | 同上 |
| `citations` | 不合并：`{"openalex": n, "crossref": n}`，各来源各自的数 | |
| `open_access_url`、`landing_url` | OpenAlex，arXiv | |

- 不同来源给出不同的非空 `year` 时，采用规则取到的值，同时把各来源的值放进 `conflicts`（期刊版和预印本年份常常不同，不能静默二选一）。
- 缺失就是缺失：值为 null 或空列表，字段名进入 `missing`，不从文件名、标题或别的字段猜。Crossref 的 `date-parts: [[null]]` 视为年份未知。
- 摘要是纯文本：Crossref 的 JATS 标签、OpenAlex 的倒排索引在适配器里还原成文本，标签被去掉，前端只当文本渲染，不当 HTML。
- 标题规范化（小写、去标点和空白）相同但合并键不同的两条，**不合并**，各自带 `similar_to`。与当前项目里已有文献标题相同但键不同的，带 `similar_papers`（文献 ID 列表）。
- `in_project`：当前项目已有 DOI 或 arXiv ID 相同的文献时为该文献 ID，否则 null。只查当前项目。
- 排序：每个来源内部有名次，合并后按名次倒数融合（RRF，k=60）降序，同分按年份降序、再按标题。响应带 `ranking: "reciprocal_rank_fusion"`，界面注明「按各来源名次融合，不是相关性评分」。

### 2.5 按 DOI / arXiv ID 导入元数据

`POST .../papers/import`，请求只有 `doi` 或 `arxiv_id` 之一。**服务端自己向来源取元数据**，不接受客户端提交的标题、作者等字段，避免伪造来源。

| 输入 | 查询 | 失败 |
| --- | --- | --- |
| DOI | 先 Crossref `/works/{doi}`；Crossref 返回 404 再查 OpenAlex `/works/doi:{doi}` | 两者都 404：404 `doi_not_found`。有来源非 404 失败且无来源成功：502 `sources_unavailable`，列出各来源状态 |
| arXiv ID | arXiv `id_list`，接受 `1706.03762`、`1706.03762v7`、`arXiv:1706.03762`、`arxiv.org/abs/…` 或 `/pdf/…` 链接，旧式 `hep-th/9901001` | 无此条目或 arXiv 返回错误条目：404 `arxiv_not_found`；超时等：502 |

- **返回记录必须就是所请求的那篇**：OpenAlex 按 `doi:10.48550/arXiv.1706.03762` 查询时，实测返回的是 DOI 为 `10.65215/2q58a426` 的另一条记录（2026-10-31）。所以回退到 OpenAlex 时，只有返回记录的 DOI 规范化后等于所请求的 DOI 才采用，否则视为该来源没有这条，不拿别的记录冒充。
- DOI 形如 `10.48550/arxiv.<id>`（DataCite 为 arXiv 分配的 DOI，Crossref 实测对它返回 404）时，直接按 arXiv ID 导入，保存的 `doi` 为空，`arxiv_id` 为 `<id>`。
- 成功后创建一条**没有 PDF** 的文献：`current_version = null`，没有解析任务。`title`、`authors`、`year`、`doi`、`arxiv_id` 写入各字段，`metadata_provenance[字段]` 为实际给出该值的来源名（`crossref`、`openalex`、`arxiv`），来源没给的字段不写入。新增 `abstract`、`venue`、`source_links`（`open_access_url`、`landing_url`）三个可空字段。返回 201 和文献。
- 来源返回的记录没有标题时，不创建，返回 422 `record_incomplete`。
- 项目内已有相同 DOI 或 arXiv ID（含 `10.48550/arxiv.X` 与 arXiv ID 的对应）：409 `duplicate_paper`，`details.paper_id` 指向已有文献。项目内 arXiv ID 唯一由数据库部分唯一索引保证，与 DOI 一致，所以并发导入也只留一份。
- 写入事件 `paper.imported`，载荷 `{"method": "metadata", "source": "<来源名>"}`，不含查询文本以外的第三方内容。

**无 PDF 文献**

- 项目页的行显示「无 PDF」，不显示「未解析」。文献页显示元数据、摘要和链接，不渲染阅读器，并提供「上传 PDF」；上传后成为 v1 并按现有流程解析。
- PDF 解析得到的内嵌元数据只填补**未知**字段，不覆盖用户手填或来自检索来源的字段（把 B6 从「用户字段」扩展到所有已有来源）。
- 可编辑元数据、删除；没有版本，所以重解析、取消、块、文件下载都返回 404。

## 3. 数据与接口

### 3.1 来源适配器

接口版本与参数于 2026-10-31 实测（样例见 [spikes](./spikes/2026-10-31-literature-sources.md)）。

| 来源 | 搜索 | 按标识取单条 | 年份过滤 | 实测的失败形态 |
| --- | --- | --- | --- | --- |
| arXiv | `export.arxiv.org/api/query?search_query=…`，Atom XML | `id_list=` | `submittedDate:[YYYYMMDDHHMM TO …]`，写在 `search_query` 里 | 参数缺失时 **HTTP 200**，正文是一个标题为 `Error` 的条目；`search_query` 语法错误也可能 200 并返回无关命中，所以只能识别明确的错误条目，不能保证发现一切 |
| OpenAlex | `api.openalex.org/works?search=…&select=…` | `/works/doi:{doi}` | `filter=publication_year:A-B` | 无效字段 400，正文含合法字段列表；预算用尽 429；响应头带额度 |
| Crossref | `api.crossref.org/works?query.bibliographic=…&select=…` | `/works/{doi}` | `filter=from-pub-date:Y,until-pub-date:Y` | 无效过滤器 400；不存在的 DOI 404；公共池响应头 `x-rate-limit-limit: 1`、`x-rate-limit-interval: 1s` |

- 请求头 `User-Agent: ResearchOS/<版本> (mailto:<CONTACT_EMAIL>)`；未配置 `CONTACT_EMAIL` 时只发 `ResearchOS/<版本>`。Crossref 在配置了邮箱时额外带 `mailto` 参数。**不发送**用户或项目的任何标识。
- OpenAlex 的 key 通过 `Authorization: Bearer` 头发送，不放进 URL，避免出现在日志和错误信息里。
- 超时：连接 3 秒，单个来源总计 `SEARCH_SOURCE_TIMEOUT_SECONDS`（默认 10）。响应体上限 2 MB，超出按 `bad_response` 处理，读到一半就中止。
- 每次请求最多取 `limit` 条（默认 20，上限 25）。只取第一页，不做翻页（非目标）。`total` 是来源报告的命中总数，不等于返回条数。
- 用户输入只作为查询参数的**值**经 HTTP 库编码，不拼进 URL 路径或主机。DOI 进入路径前先通过 `DOI_RE` 校验并编码。适配器不接受任何来自用户的 URL。
- arXiv 的 `search_query`：把输入按空白切词，每个词转成 `all:词`，用 `AND` 连接；词中的 `"`、`:`、`(`、`)`、`+` 等字符被去掉。输入里的 `AND/OR/ANDNOT` 当作普通词。需要精确短语的用户暂时做不到，这是已知限制。
- XML 用 `defusedxml` 解析（D-014），拒绝实体与外部 DTD。

### 3.2 共享状态与缓存（PostgreSQL，迁移 `0002`）

限速和冷却必须跨进程生效（可能同时有多个 API 进程），所以放在数据库，不放进程内存。

| 表 | 字段要点 | 用途 |
| --- | --- | --- |
| `source_state` | `source` 主键、`next_allowed_at`、`cooldown_until`、`cooldown_reason`、`quota_remaining_usd`、`quota_reset_at`、`updated_at` | 间隔预约、429 冷却、OpenAlex 额度 |
| `search_cache` | `key` 主键（来源 + 操作 + 规范化参数的 SHA-256）、`source`、`payload` JSONB（标准化后的结果，不含原始响应）、`total`、`fetched_at`、`expires_at` | 响应缓存。第三方的公开元数据，不含用户信息，所有用户和项目共享；`in_project` 在每次响应时重新计算，不进缓存 |
| `search_calls` | `id`、`user_id`、`created_at`、`sources` | 每用户限额。**不记录查询文本** |

- 串行与间隔（arXiv、Crossref）：arXiv 条款要求同一时间只有一个连接、两次请求间隔至少 3 秒、所有机器合计；Crossref 公共池并发上限为 1。所以这两个来源在发请求前先取一个 **PostgreSQL 会话级咨询锁**（每个来源一把，持有到响应读完），等锁最多 `SEARCH_MAX_WAIT_SECONDS`（默认 8），拿不到就记 `throttled` 并且不发请求。进程崩溃时连接断开，锁自动释放，不会卡死。拿到锁后读 `next_allowed_at`，需要等待且总等待不超过上限就睡到那一刻再发；请求**结束后**写 `next_allowed_at = 结束时间 + 间隔`。这样间隔从上一个请求完成算起，不会因为慢响应而重叠。
- OpenAlex 不加锁，只检查额度和冷却，每次请求前预约 0.2 秒的最小间隔（原子 `UPDATE … RETURNING`）。
- 间隔配置：arXiv 3 秒（可调大不可调小，配置校验拒绝小于 3），Crossref 取最近一次响应头 `x-rate-limit-interval / x-rate-limit-limit`（拿不到时 1 秒）。
- 冷却：429 带 `Retry-After`（秒数或 HTTP 日期）时按它冷却；没有时 Crossref/arXiv 冷却 60 秒。OpenAlex 的 429 在 `X-RateLimit-Remaining` 为 0 或 `X-RateLimit-Remaining-USD` 不足时按 `X-RateLimit-Reset` 秒数写到 `quota_reset_at`，状态为 `quota_exhausted`；头缺失时按 `rate_limited` 冷却 60 秒。OpenAlex 文档只说明了这两种 429 的原因，没有给出响应体和 `Retry-After`，所以不依赖响应体。
- OpenAlex 额度：每次成功响应后记录 `x-ratelimit-remaining-usd` 与 `x-ratelimit-reset`；剩余低于一次搜索的成本（`x-ratelimit-cost-usd` 的最近值，默认 0.001）时，在 `quota_reset_at` 之前不发请求，状态 `quota_exhausted`。没有设置 key 时，响应里多一个 `quota.shared: true`，界面提示「未配置 OpenAlex key，与同一出口 IP 的所有请求共用每日 $0.10」。
- 缓存 TTL：搜索结果 `SEARCH_CACHE_TTL_SECONDS`（默认 21600，6 小时），按标识取单条 24 小时。只缓存 `ok`。过期行在写入时顺带清理，表不会无限增长；清理有测试。
- 每用户限额：`SEARCH_USER_PER_MINUTE`（默认 10）次搜索。超过返回 429 `search_rate_limited`，`details.retry_after` 为秒数，**不发任何上游请求**。导入按同一表计数，同一额度。

### 3.3 API（前缀 `/api/v1`，嵌套在项目下，见 D-006）

开发文档 14.4 的 `POST /api/v1/papers/search` 与 `/papers/import` 调整为项目路径，原因同 D-006：权限、`in_project` 和去重都以项目为边界。

| 方法与路径 | 说明 | 角色 |
| --- | --- | --- |
| `POST /projects/{id}/papers/search` | 聚合检索，200 | member |
| `POST /projects/{id}/papers/import` | 按 DOI 或 arXiv ID 导入元数据，201 | member |

**搜索请求**

```json
{"query": "attention is all you need",
 "sources": ["arxiv", "openalex", "crossref"],
 "year_from": 2017, "year_to": 2018, "limit": 20}
```

`query` 去首尾空白后 1–300 个字符，不含控制字符；`sources` 省略即全部，不能为空、不能重复、只能是三个已知名字；`year_from ≤ year_to`，范围 1500 到当前年份加 1；`limit` 1–25。违反返回 422，不发上游请求。

**搜索响应**

```json
{"outcome": "results | no_match | partial | all_failed",
 "ranking": "reciprocal_rank_fusion",
 "sources": {
   "arxiv": {"status": "ok", "count": 12, "total": 47, "cached": false,
             "fetched_at": "…", "duration_ms": 640, "message": null},
   "openalex": {"status": "quota_exhausted", "count": 0, "total": null, "cached": false,
                "reset_at": "…", "quota": {"remaining_usd": 0, "reset_at": "…", "shared": true},
                "message": "OpenAlex 今日额度已用完，将在 … 重置"},
   "crossref": {"status": "timeout", "count": 0, "total": null, "message": "Crossref 10 秒内没有响应，已重试 1 次"}},
 "results": [{
   "title": "…", "authors": ["…"], "year": 2017, "venue": null,
   "doi": "10.48550/arxiv.1706.03762", "arxiv_id": "1706.03762",
   "abstract": "…", "sources": ["arxiv", "openalex"],
   "provenance": {"title": "arxiv", "year": "crossref"},
   "conflicts": {"year": {"arxiv": 2017, "crossref": 2025}},
   "missing": ["venue"], "citations": {"openalex": 1234},
   "open_access_url": "…", "landing_url": "…",
   "in_project": null, "similar_to": [], "similar_papers": []}]}
```

**导入请求**：`{"doi": "10.1038/nature14539"}` 或 `{"arxiv_id": "1706.03762"}`，二选一，其余字段 422（`extra=forbid`）。响应 201，形状与现有 `PaperOut` 相同，新增 `abstract`、`venue`、`source_links`。

**新增错误码**

| 码 | 状态 | 含义 |
| --- | --- | --- |
| `search_rate_limited` | 429 | 本用户搜索太频繁 |
| `doi_not_found` / `arxiv_not_found` | 404 | 来源明确表示没有 |
| `sources_unavailable` | 502 | 没有来源给出答案，且至少一个因故障失败；`details.sources` 列各来源状态 |
| `record_incomplete` | 422 | 来源记录缺标题 |
| `duplicate_paper` | 409 | 项目已有；`details.paper_id` |
| `invalid_arxiv_id` / `invalid_doi` | 422 | 格式不对 |

### 3.4 现有表与接口的改动

- `papers` 新增可空列 `abstract`（文本）、`venue`（文本）、`source_links`（JSONB，`open_access_url`、`landing_url`）。`current_version_id` 本来就可空。
- 新增部分唯一索引 `uq_papers_project_arxiv (project_id, arxiv_id) WHERE arxiv_id IS NOT NULL`。迁移前若库里已有重复，迁移会失败并指出重复项；P0.1/P0.2 阶段没有对 arXiv ID 做唯一约束，所以这是真实风险，只在开发数据上发生过的话手动处理即可。
- `PATCH` 元数据：`arxiv_id` 归一化（去 `arXiv:` 前缀、URL、版本号），冲突返回 409 `duplicate_arxiv_id`，与 DOI 的 409 并列；`metadata_provenance` 的值域从 `user`/`pdf_metadata` 扩为再加 `arxiv`、`openalex`、`crossref`。前端的来源文案要覆盖这三个值，否则显示「未知」。
- 上传新版本/上传 PDF 到无 PDF 的文献：版本号从 1 开始，`current_version_id` 设为该版本，创建解析任务。这条路径在 P0.2 没有被测试过，要补。
- 解析完成回填元数据时，只填 `metadata_provenance` 中**没有**记录的字段。

### 3.5 已知的数据质量限制

- 中文查询：只有 OpenAlex 可能给出中文文献，arXiv 与 Crossref 对中文标题检索效果未评估。本切片不翻译，也不做多语关键词。
- Crossref 记录常缺作者、摘要；实测 40 条 `type:other` 记录里 39 条没有 `author`，2 条年份为 `[[null]]`。缺失按未知展示。
- OpenAlex 的 DOI 可能是出版商 DOI 或预印本服务的 DOI，同一篇论文的预印本与期刊版会各有一条。合并只靠 DOI、arXiv ID，两者不同的记录不合并，只提示相似。
- 引用数各来源口径不同，分开展示，不相加、不排序。

## 4. 验收标准

测试方式：适配器通过真实 HTTP 访问**本地起的假上游服务**（真实 socket，用 `httpx` 的真实客户端，不替换 HTTP 库）。假上游回放 2026-10-31 实测录下的真实响应，并能按需制造慢响应、429、5xx、截断的正文、巨大正文、200 错误条目。数据库用真实 PostgreSQL。另有**在线契约测试**，直接访问三个真实来源，仅在设置 `TEST_LIVE_SOURCES=1` 时运行，否则跳过并写明未测试；默认检查不依赖外网，也不消耗 OpenAlex 额度。

**检索与来源状态**

- F1 Given 三个来源都正常，When 搜索，Then `outcome=results`，每个来源 `ok`，结果带 `sources`、`provenance`，字段缺失时在 `missing` 里。
- F2 Given 一个来源超时，When 搜索，Then 该来源 `timeout`（先重试一次，假上游记录到 2 次请求），其余来源结果照常返回，`outcome=partial`，总耗时不超过超时加一个重试的上界。
- F3 Given 一个来源返回 5xx 或断开连接，Then `unavailable`，重试一次后放弃。
- F4 Given 一个来源返回 429，Then `rate_limited`，带 `retry_after`；**不重试**；下一次搜索在冷却期内 `cooling_down` 且假上游收到 0 次请求；冷却过期后恢复请求。
- F5 Given OpenAlex 响应头剩余额度低于一次搜索成本，Then 之后的搜索不再向 OpenAlex 发请求，状态 `quota_exhausted`，带 `reset_at`；额度重置时间过后恢复。429 且 `X-RateLimit-Remaining: 0` 同样进入此状态。没有 key 时 `quota.shared=true`。
- F6 Given 配置了 `OPENALEX_API_KEY`，Then 请求带 `Authorization: Bearer`，URL、日志、错误信息、响应里都不出现该 key（测试扫描假上游收到的 URL、应用日志和响应）。
- F7 Given 来源返回 400，Then `bad_request`，`message` 带来源的原因；返回非法 JSON/XML、缺少必需结构、arXiv 的 200 错误条目、超过 2 MB 的正文，Then `bad_response`，不抛 500，不写缓存，其余来源不受影响。
- F8 Given 三个来源全部失败，Then `outcome=all_failed`，**不是** `no_match`，响应里没有「没有结果」的暗示；三个来源都 `ok` 且无命中，Then `no_match`。两者的界面文案不同。
- F9 Given 同一查询在 TTL 内重复，Then 第二次 `cached=true`，假上游 0 次请求；TTL 过期后重新请求；失败结果不被缓存；缓存里没有用户或项目信息；不同用户的同一查询共享缓存但各自的 `in_project` 正确。
- F10 Given 并发多次搜索，Then 对 arXiv 的请求任意两次开始间隔不小于配置间隔、且任何时刻最多一个在途（假上游记录开始与结束时间）；Crossref 同样串行，间隔取自响应头；两个来源的请求在多进程下也不重叠（用两个真实进程同时搜索验证）。
- F11 Given 需要等待超过 `SEARCH_MAX_WAIT_SECONDS`，Then 该来源 `throttled`，不发请求，不阻塞其他来源。
- F12 Given 同一用户一分钟内超过上限，Then 429 `search_rate_limited`，假上游 0 次请求；另一个用户不受影响。

**输入与权限**

- G1 Given viewer，When 搜索或导入，Then 403；非成员 404；归档项目被拒绝；三种情况假上游都是 0 次请求。
- G2 Given 空查询、超长查询、含控制字符、未知来源、重复来源、`year_from > year_to`、`limit` 越界，Then 422，0 次上游请求。
- G3 Given 查询含 `&`、`#`、`%`、换行、`../`、Lucene 语法字符和 Unicode，Then 假上游收到的是被正确编码的单个参数值，没有多出的参数或路径段（逐个字符断言原始请求行）。
- G4 Given XML 含外部实体或 DTD（billion laughs 的小规模版本），Then 按 `bad_response` 处理，不读本地文件，不发外部请求。
- G5 Given 来源返回的标题或摘要含 HTML/脚本，Then 响应里是纯文本；Crossref JATS 标签被去掉；浏览器中以文本渲染（e2e 校验页面上没有被执行的脚本，也没有生成的元素）。

**合并与去重**

- H1 Given 三个来源返回同一篇论文（DOI 大小写不同、带 `https://doi.org/` 前缀；arXiv 带版本号；OpenAlex 的 DOI 为 `10.48550/arxiv.X`），Then 合并为一条，`sources` 列出三个来源。
- H2 Given 预印本与期刊版（DOI 不同，标题相同），Then 不合并，互相出现在 `similar_to`。
- H3 Given 各来源 `year` 不同，Then 采用规则取值，`conflicts` 列出各来源的值，不静默二选一。
- H4 Given 记录缺作者、缺年份（`[[null]]`）、缺摘要，Then 值为 null/空，字段在 `missing`，不是 0、空字符串或文件名。
- H5 Given 三个以上记录经 DOI 与 arXiv ID 间接相连，Then 合并成一条（连通分量），结果与来源返回顺序无关。
- H6 Given 项目里已有同 DOI 或同 arXiv ID 的文献，Then `in_project` 指向它；已有同标题不同键的文献出现在 `similar_papers`；别的项目的文献不出现。
- H7 Given 同一份输入，Then 结果顺序确定；`ranking` 标明融合方式；引用数分开展示。

**导入**

- I1 Given 有效 DOI（Crossref 命中），When 导入，Then 201，文献无 PDF，字段与来源一致，`metadata_provenance` 标 `crossref`，来源没给的字段是 null 且没有 provenance 键，事件已写。
- I2 Given Crossref 404、OpenAlex 命中，Then 回退到 OpenAlex，provenance 为 `openalex`；两者都 404，Then 404 `doi_not_found`，不创建任何记录。
- I2b Given DOI `10.48550/arXiv.1706.03762`，Then 按 arXiv 路径导入（不查 Crossref）；Given OpenAlex 对某 DOI 返回了 DOI 不同的记录，Then 不采用，结果为 `doi_not_found`。
- I3 Given 有效 arXiv ID（各种写法与旧式 ID），Then 201，`arxiv_id` 去掉版本号，provenance 为 `arxiv`；不存在的 ID，Then 404 `arxiv_not_found`。
- I4 Given 来源超时/限流/故障且没有来源成功，Then 502 `sources_unavailable`，列出各来源状态，**不创建**记录，也不伪造「找不到」。
- I5 Given 来源记录缺标题，Then 422 `record_incomplete`，不创建记录。
- I6 Given 项目已有同 DOI 或同 arXiv ID（含 `10.48550/arxiv.X` 与 `X`），Then 409 `duplicate_paper` 指向已有文献；并发导入同一标识只留一份。
- I7 Given 请求带自拟的标题、作者等字段，Then 422，没有任何字段被采用。
- I8 Given 无 PDF 文献，When 上传 PDF，Then 成为 v1 并被解析；解析出的内嵌元数据只填补未知字段，不覆盖来源给出的字段；对它重解析、取消、取块、下载文件返回 404；删除成功。
- I9 Given 手动把 `arxiv_id` 改成项目里已有的，Then 409 `duplicate_arxiv_id`；大小写与 `arXiv:` 前缀被归一化。

**界面（真实浏览器，假上游服务）**

- J1 Given member，When 在项目页搜索，Then 看到三个来源各自的状态徽标与耗时、融合后的结果、「按各来源名次融合，不是相关性评分」的说明；缺失字段显示「未知」，不是空白或 undefined。
- J2 Given 一个来源被限流、一个超时、一个正常，Then 正常来源的结果显示，两个失败来源旁各有原因和下一步（重试时间或稍后再试）；全部失败时只显示失败原因，没有「没有找到」。
- J3 Given 结果已在项目中，Then 显示「已在项目中」并链接过去，导入按钮不可用；点击「导入」后该条变为「已在项目中」，项目页出现「无 PDF」的文献，刷新后仍在。
- J4 Given viewer，Then 没有搜索框；非成员 not-found。
- J5 Given 无 PDF 的文献页，Then 显示元数据、摘要与外链（`rel="noopener noreferrer"`，`target="_blank"`），不渲染阅读器；上传 PDF 后变成现有阅读器流程。
- J6 Given 搜索进行中，Then 按钮禁用并显示进度；每个来源返回后才显示，不用假进度条。

## 5. 技术决策与风险

决策见 [DECISIONS.md](./DECISIONS.md) 的 D-013 至 D-015。

| 风险 | 处理 |
| --- | --- |
| 第三方条款变化、接口改版 | 适配器按字段防御性解析，缺字段是未知而不是崩溃；在线契约测试（`TEST_LIVE_SOURCES=1`）能发现改版，默认不跑，结果必须在第 7 节注明是否执行过 |
| arXiv 的 200 错误条目、语法错误仍返回无关命中 | 只能识别明确的错误条目。查询词被限制为转义后的 `all:` 词，减少语法错误；这个限制写进「已知限制」，不声称能发现所有 arXiv 错误 |
| OpenAlex 额度被同一出口 IP 的其他程序耗尽 | 如实显示 `quota_exhausted` 与重置时间；建议配置 key。无 key 时界面提示共享额度 |
| 额度和限速状态在多进程间不一致 | 放在 PostgreSQL，咨询锁加原子更新，用两个真实进程测试 |
| 第三方内容进入页面（XSS、链接） | 摘要与标题只当文本；外链只接受 `http`/`https`，`noopener noreferrer`；JATS 标签在适配器里剥除 |
| SSRF | 服务端只访问三个固定主机，用户输入只作为参数值。**不下载 OA 链接指向的 PDF**，所以不引入按用户给定 URL 取文件的路径 |
| 合并错误（把两篇不同论文并成一条） | 只按 DOI 与 arXiv ID 合并；标题相似只提示。仍可能有来源自身的错误 DOI，`conflicts` 与 `sources` 让用户能看到依据 |
| 缓存让用户看到过期数据 | 界面显示 `cached` 与 `fetched_at`；TTL 可配 |
| 注册用户滥用检索消耗额度 | 每用户每分钟上限；**没有全局日额度护栏之外的计费保护**，公开部署前需另做（已知限制） |
| `uq_papers_project_arxiv` 迁移遇到既有重复 | 迁移失败并说明，不静默删数据 |

**未验证的假设**：中文检索质量；Crossref 的 polite pool 效果（需要真实邮箱，本机不配置则只走公共池）；OpenAlex 带 key 时的响应头与无 key 完全一致（本机没有 key，key 路径只有假上游测试，在线未测）。

## 6. 实施计划与完成定义

1. 迁移与模型：`papers` 三列、arXiv 唯一索引、三张状态表。
2. 适配器：标准化数据类型，三个来源的解析与错误分类，各自对录下的真实响应做解析测试。
3. 共享状态：咨询锁、间隔、冷却、额度、缓存、每用户上限。
4. 合并与排序。
5. API：搜索、导入、`arxiv_id` 唯一与归一化、无 PDF 文献的各接口行为。
6. 前端：检索面板、来源状态、导入、无 PDF 文献页。
7. 假上游服务与 e2e。
8. 在线契约测试（可选运行）、文档。

**完成定义**：第 4 节每一条有自动化证据，F/G/H/I 在后端，J 在浏览器；`TEST_LIVE_SOURCES=1` 至少跑过一次并记录结果，没跑就在第 7 节写「在线契约未验证」，且 `LIT-01` 至 `LIT-03` 不标完成；README、`.env.example`、映射表更新；`bash scripts/check.sh` 与 `scripts/e2e.py` 通过。

## 7. 验收证据

记录日期 2026-10-31，本机 macOS arm64，uv，Node 24，Python 3.12。数据库是内嵌 PostgreSQL，没有 Docker。

### 7.1 自动检查

| 命令 | 结果 |
| --- | --- |
| `bash scripts/check.sh` | 退出码 0，`all checks passed`。secrets 116 个文件 0 问题；需求映射 152 个 ID 0 问题；ruff、mypy（34 个源文件）通过；pytest 含迁移漂移检查 214 passed、12 skipped（10 个 Docling 用例未设 `TEST_DOCLING_PYTHON`，2 个在线契约用例未设 `TEST_LIVE_SOURCES`）；前端类型检查与构建通过 |
| `backend/.venv/bin/python scripts/e2e.py` | 28 passed，其中本切片 8 个（`web/e2e/literature.spec.ts`），其余是 P0.1/P0.2 的 20 个，没有回归 |
| `TEST_LIVE_SOURCES=1 uv run pytest tests/test_literature_live.py -s` | 2 passed。三个来源都返回 `ok` 且各 5 条；`arxiv_id=1706.03762` 与 `doi=10.1038/nature14539` 导入成功（见 7.3 的限制） |

### 7.2 标准与证据

后端用例都在真实 PostgreSQL 上，上游是本地假服务（真实 socket，回放 2026-10-31 录下的响应），没有替换 HTTP 库。

| 标准 | 证据 |
| --- | --- |
| F1 | `test_f1_all_sources_ok` |
| F2 | `test_f2_timeout_retries_once_and_others_still_answer`（假上游记录到 2 次请求，其余来源照常，总耗时有上界） |
| F3 | `test_f3_unavailable_retries_once` |
| F4 | `test_f4_rate_limit_sets_a_cooldown_that_expires`、`test_f4_retry_after_as_http_date`、`test_f4_rate_limit_without_retry_after_uses_the_default_cooldown` |
| F5 | `test_f5_openalex_quota_from_headers_blocks_further_requests`、`test_f5_429_with_zero_remaining_means_quota_exhausted`、`test_f5_429_without_quota_headers_is_a_plain_rate_limit` |
| F6 | `test_f6_api_key_is_only_sent_as_a_bearer_header`（扫描假上游收到的 URL、应用日志和响应） |
| F7 | `test_f7_bad_request_carries_the_upstream_reason`、`test_f7_unusable_responses_are_bad_response_not_500`、`test_f7_an_oversize_body_is_bad_response_exactly` |
| F8 | `test_f8_all_failed_is_not_no_match`、`test_f8_all_ok_without_hits_is_no_match`、`test_f8_partial_with_no_hits_is_not_no_match`；界面：J2 |
| F9 | `test_f9_cache_hit_expiry_and_sharing`、`test_f9_failures_are_not_cached`、`test_f9_expired_rows_are_cleaned_when_new_ones_are_written` |
| F10 | `test_f10_arxiv_and_crossref_requests_never_overlap_and_keep_their_spacing`（线程）；`test_f10_two_real_processes_do_not_overlap_or_beat_the_interval`（两个真实进程）。把咨询锁临时去掉后两个用例都失败，已还原 |
| F11 | `test_f11_waiting_longer_than_the_limit_is_throttled_without_a_request`。把等待上限判断去掉后失败，已还原 |
| F12 | `test_f12_per_user_limit_blocks_before_any_upstream_request`、`test_import_counts_against_the_user_limit_and_ledger` |
| G1 | `test_g1_permissions_and_no_upstream_traffic`、`test_g1_unauthenticated_is_refused` |
| G2 | `test_g2_invalid_input_is_422_with_no_upstream_request` |
| G3 | `test_g3_special_characters_arrive_as_one_encoded_parameter`、`test_g3_arxiv_receives_only_escaped_all_terms`、`test_g3_a_query_with_nothing_searchable_on_arxiv_is_reported_not_sent`、`test_g3_user_agent_identifies_the_app_but_not_the_user` |
| G4 | `test_g4_xml_entities_are_refused_not_expanded` |
| G5 | `test_g5_markup_in_titles_and_abstracts_becomes_plain_text`；浏览器：e2e `G5`（页面没有生成 img/script/svg，`window.__pwned` 未设置，没有对话框） |
| H1 至 H7 | `test_h1_h5_one_paper_from_three_sources_with_different_spellings`、`test_h2_…`、`test_h3_…`、`test_h4_…`、`test_h5_result_does_not_depend_on_the_order_sources_answer_in`、`test_h6_in_project_and_similar_papers_use_this_project_only`、`test_h6_same_title_with_a_different_key_is_only_a_similar_paper`、`test_h7_order_is_deterministic_and_rank_based` |
| I1 至 I9 | `test_i1_…` 至 `test_i9_arxiv_id_edits_are_normalized_and_unique`，共 21 个用例，覆盖 Crossref 命中、OpenAlex 回退与 DOI 不符、`10.48550/arxiv.X`、arXiv ID 各写法与旧式 ID、502 不伪造 404、缺标题、重复与并发、客户端自拟字段、无 PDF 文献的上传/重解析/删除 |
| J1 至 J6 | `web/e2e/literature.spec.ts` 的 8 个用例：J1；J2（部分失败，冷却期内 OpenAlex 假上游 0 次新请求；全部失败不出现「没有找到」，全部无命中才出现）；J3 与 J5（导入、刷新后仍在、无 PDF 页、外链 `rel`/`target`、上传 PDF 后进入阅读器且来源标注保留）；J3 重复导入；J4（viewer 无检索框，非成员 not-found，三个假上游 0 次请求）；J6（检索中按钮禁用、没有结果和状态行） |

可失败性检查：把全部失败时的提示改成「没有找到匹配的文献」后，J2 用例失败；还原后通过。

### 7.3 未验证与已知限制

- **在线契约只跑了一次**（2026-10-31，无 OpenAlex key，没有配置 `CONTACT_EMAIL`）。每个来源一次搜索、两次按标识查询。它不覆盖：OpenAlex 带 key 的路径、Crossref polite pool、429 与额度耗尽的真实响应（这些只有假上游回放，其中 OpenAlex 的 429 响应形态来自文档与推断，没有在线触发过）。
- **在线测试发现并修复了一个真实缺陷**：arXiv 的索引不匹配 `is` 之类的停用词，`all:is` 命中 0 条，导致 `attention is all you need` 的 AND 查询在 arXiv 返回 0 条且状态为 `ok`（假上游录的是不含 `is` 的响应，所以先前的测试没有发现）。现在查询词里去掉停用词（全是停用词时照发）；`test_arxiv_query_leaves_out_stop_words_that_arxiv_cannot_match` 覆盖。停用词表是 Lucene 的标准英文停用词；实测 `all:attention AND all:<词>` 命中为 0 的词有 `is`、`are`、`that`、`this`、`their`、`these`、`there`、`they`、`into`，都在表里。其余词没有逐个测过（如 `no`、`such` 未测），表之外仍可能有让 AND 查询落空的词。这种情形界面显示为「正常，返回 0 条」，用户无从区分，是已知限制。
- 中文检索质量、Crossref 对 `type:other` 的数据质量没有评估。
- 全局日额度之外的计费保护没有做，公开部署前需另做。
- e2e 只在 Chromium 上跑过。
