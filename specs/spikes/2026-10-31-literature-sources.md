# 文献来源接口实测（P0.3 规格依据）

2026-10-31 从本机直接访问三个真实来源，无 OpenAlex key，Crossref 公共池，未带 mailto。响应样本将作为测试夹具保存在 `backend/tests/fixtures/sources/`（只含搜索命中的公开元数据）。

## 实测结果

| 来源 | 检查 | 结果 |
| --- | --- | --- |
| arXiv | 搜索 `all:"attention is all you need"` | 200，Atom；`opensearch:totalResults` 为命中总数 |
| arXiv | `id_list=1706.03762` | 200，返回 `1706.03762v7`，8 位作者，`published` 2017-06-12；条目**没有** `arxiv:doi`、`arxiv:journal_ref` |
| arXiv | 不带 `search_query` 与 `id_list` | **HTTP 200**，正文是一个标题为 `Error` 的条目，`summary` 为原因 |
| arXiv | `search_query=bad:::` | 200，仍返回 5024 条无关命中；语法错误不一定报错 |
| arXiv | `submittedDate:[201701010000 TO 201712312359]` | 200，可用 |
| OpenAlex | 搜索，无 key | 200；`x-ratelimit-limit-usd: 0.1`，`x-ratelimit-remaining-usd`、`x-ratelimit-reset`（秒）、`x-ratelimit-cost-usd: 0.001`；`meta.notice` 提示共享额度 |
| OpenAlex | 无效过滤器 | 400，正文含合法字段列表 |
| OpenAlex | `/works/doi:…`、`filter=publication_year:2017-2018` | 200，可用 |
| OpenAlex | 同一篇论文 | 预印本与期刊版是不同记录，DOI 不同；`locations[]` 里有 arXiv 的 `landing_page_url`，其 DOI 形如 `10.48550/arxiv.1706.03762` |
| OpenAlex | `/works/doi:10.48550/arXiv.1706.03762` | 200，但返回的记录 DOI 是 `10.65215/2q58a426`，不是所请求的 DOI；Crossref 对该 DOI 返回 404（DataCite 注册） |
| OpenAlex | 中文查询 `注意力机制` | 200，有命中，质量未评估 |
| Crossref | 搜索 `query.bibliographic`，`select=` | 200；`x-rate-limit-limit: 1`、`x-rate-limit-interval: 1s`、`x-api-pool: public-array` |
| Crossref | `/works/{doi}` | 存在 200；`10.9999/nonexistent` 404 |
| Crossref | 无效过滤器 | 400，正文列出合法过滤器 |
| Crossref | 40 条 `type:other` 记录 | 39 条没有 `author`，2 条 `issued.date-parts` 为 `[[null]]`；摘要是 JATS XML 片段 |

## 来自官方文档（读过页面，未逐项触发）

- arXiv（info.arxiv.org/help/api/tou.html）：legacy API 每 3 秒不超过一次请求，同一时间一个连接，不得靠增加机器绕过；元数据 CC0，可保存；不得未经许可存储并提供 e-print 内容。
- OpenAlex（help.openalex.org 速率限制页）：429 有两种原因，超出每日预算和超出每秒限制；每秒 429 不计费，稍候重试；`X-RateLimit-Reset` 是距午夜 UTC 的秒数；key 可用 `api_key` 参数或 `Authorization: Bearer`。页面没有描述 429 的响应体，也没有提到 `Retry-After`。
- Crossref（access-and-authentication 页）：公共池 5 次/并发 1，polite 池 10/并发 3（时间单位由响应头 `x-rate-limit-interval` 给出，实测公共池为 `1 次 / 1 秒`，与页面的 5 不一致，所以以响应头为准）；429 无 `Retry-After` 的说明。

## 未验证

- 带 OpenAlex key 的响应头；真实的 429 响应（没有主动耗尽额度，也没有压测第三方）。
- Crossref polite 池；arXiv 的 429 行为。
- 中文查询的质量；OpenAlex 与 Crossref 对同一 DOI 的字段差异的系统性统计。
