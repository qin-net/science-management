"use client";

import Link from "next/link";
import { type FormEvent, useState } from "react";

import { ErrorNotice, formatTime } from "@/components/ui";
import { ApiFailure, sendJson } from "@/lib/api";
import type {
  Paper,
  SearchResponse,
  SearchResult,
  SourceName,
  SourceReport,
  SourceStatus,
} from "@/lib/types";

const SOURCE_NAMES: Record<SourceName, string> = {
  arxiv: "arXiv",
  openalex: "OpenAlex",
  crossref: "Crossref",
};

const STATUS_LABELS: Record<SourceStatus, string> = {
  ok: "正常",
  timeout: "超时",
  unavailable: "不可用",
  rate_limited: "被限流",
  quota_exhausted: "额度用完",
  cooling_down: "冷却中",
  throttled: "本地限速",
  bad_request: "请求被拒",
  bad_response: "响应不可用",
};

const FIELD_LABELS: Record<string, string> = {
  title: "标题",
  authors: "作者",
  year: "年份",
  venue: "期刊/会议",
  doi: "DOI",
  arxiv_id: "arXiv ID",
  abstract: "摘要",
};

// Abstracts and titles come from third parties; they are only ever rendered as text, and links
// are followed only when they are plain http(s).
function httpUrl(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function sourceHint(report: SourceReport): string {
  if (report.status === "ok") {
    const total = report.total !== null ? `，来源共报告 ${report.total} 条` : "";
    return `返回 ${report.count} 条${total}${report.cached ? "（缓存）" : ""}`;
  }
  return report.message ?? STATUS_LABELS[report.status];
}

function SourceBadges({ sources }: { sources: SearchResponse["sources"] }) {
  const entries = Object.entries(sources) as [SourceName, SourceReport][];
  return (
    <ul className="list" data-testid="source-statuses">
      {entries.map(([name, report]) => (
        <li
          key={name}
          className="row small"
          data-testid="source-status"
          data-source={name}
          data-status={report.status}
        >
          <span className={`badge ${report.status === "ok" ? "succeeded" : "failed"}`}>
            {SOURCE_NAMES[name]}：{STATUS_LABELS[report.status]}
          </span>
          <span className="muted" data-testid="source-message">
            {sourceHint(report)}
          </span>
          {report.duration_ms !== null && (
            <span className="muted" data-testid="source-duration">
              {report.duration_ms} ms
            </span>
          )}
          {name === "openalex" && report.quota?.shared && (
            <span className="muted">
              未配置 OpenAlex key，与同一出口 IP 的所有请求共用每日额度
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

function ResultItem({
  result,
  projectId,
  imported,
  onImport,
}: {
  result: SearchResult;
  projectId: string;
  imported: string | null;
  onImport: (result: SearchResult) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiFailure | null>(null);
  const inProject = imported ?? result.in_project;
  const identifiable = Boolean(result.doi || result.arxiv_id);
  const links = [
    ["开放获取链接", httpUrl(result.open_access_url)],
    ["来源页面", httpUrl(result.landing_url)],
  ] as const;

  async function importIt() {
    setBusy(true);
    setError(null);
    try {
      await onImport(result);
    } catch (failure) {
      setError(failure as ApiFailure);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="list-item" data-testid="search-result">
      <strong data-testid="result-title">{result.title ?? "（标题未知）"}</strong>
      <span className="muted">
        {result.authors.length > 0 ? result.authors.join("，") : "作者未知"}
        {" · "}
        {result.year ?? "年份未知"}
        {" · "}
        {result.venue ?? "期刊/会议未知"}
      </span>
      <span className="muted small">
        来源：{result.sources.map((name) => SOURCE_NAMES[name]).join("、")}
        {result.doi && ` · DOI ${result.doi}`}
        {result.arxiv_id && ` · arXiv ${result.arxiv_id}`}
      </span>
      {Object.keys(result.citations).length > 0 && (
        <span className="muted small">
          被引（各来源口径不同，不相加）：
          {Object.entries(result.citations)
            .map(([name, count]) => `${SOURCE_NAMES[name as SourceName] ?? name} ${count}`)
            .join("，")}
        </span>
      )}
      {Object.entries(result.conflicts).map(([field, values]) => (
        <span className="muted small" key={field} data-testid="conflict">
          {FIELD_LABELS[field] ?? field}各来源不一致：
          {Object.entries(values)
            .map(([name, value]) => `${SOURCE_NAMES[name as SourceName] ?? name} ${value}`)
            .join("，")}
        </span>
      ))}
      {result.missing.length > 0 && (
        <span className="muted small" data-testid="missing">
          未知：{result.missing.map((field) => FIELD_LABELS[field] ?? field).join("、")}
        </span>
      )}
      {result.similar_to.length > 0 && (
        <span className="muted small">另有标题相同但标识不同的结果，可能是预印本与正式版。</span>
      )}
      {result.similar_papers.length > 0 && (
        <span className="muted small" data-testid="similar-papers">
          项目里已有标题相同的文献，
          {result.similar_papers.map((id, index) => (
            <Link key={id} href={`/projects/${projectId}/papers/${id}`}>
              查看{result.similar_papers.length > 1 ? index + 1 : ""}
            </Link>
          ))}
        </span>
      )}
      {result.abstract ? (
        <details>
          <summary className="small">摘要</summary>
          <p className="small" data-testid="result-abstract">
            {result.abstract}
          </p>
        </details>
      ) : null}
      <div className="row">
        {links.map(
          ([label, href]) =>
            href && (
              <a key={label} href={href} target="_blank" rel="noopener noreferrer">
                {label}
              </a>
            ),
        )}
        {inProject ? (
          <Link href={`/projects/${projectId}/papers/${inProject}`} data-testid="in-project">
            已在项目中
          </Link>
        ) : (
          <button
            disabled={busy || !identifiable}
            onClick={() => void importIt()}
            title={identifiable ? undefined : "没有 DOI 或 arXiv ID，无法按标识导入"}
          >
            {busy ? "导入中…" : "导入"}
          </button>
        )}
      </div>
      {error && <ErrorNotice error={error} />}
    </li>
  );
}

function Outcome({ response }: { response: SearchResponse }) {
  if (response.outcome === "no_match") {
    return (
      <div className="empty" data-testid="outcome-no-match">
        没有找到匹配的文献
      </div>
    );
  }
  if (response.outcome === "all_failed") {
    return (
      <div className="notice error" data-testid="outcome-all-failed">
        <strong>所有来源都没有返回结果</strong>
        <span className="muted">这不代表没有相关文献。原因见上方各来源状态。</span>
      </div>
    );
  }
  if (response.outcome === "partial" && response.results.length === 0) {
    return (
      <div className="notice warn" data-testid="outcome-partial-empty">
        <strong>正常的来源没有命中，其余来源失败</strong>
        <span className="muted">可能漏掉失败来源里的文献。</span>
      </div>
    );
  }
  return null;
}

export function LiteratureSearch({
  projectId,
  onImported,
}: {
  projectId: string;
  onImported: () => Promise<void>;
}) {
  const [searching, setSearching] = useState(false);
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [asked, setAsked] = useState<string[]>([]);
  const [error, setError] = useState<ApiFailure | null>(null);
  const [imported, setImported] = useState<Record<number, string>>({});

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const body: Record<string, unknown> = { query: String(form.get("query") ?? "").trim() };
    for (const field of ["year_from", "year_to"]) {
      const value = String(form.get(field) ?? "").trim();
      if (value) {
        body[field] = Number(value);
      }
    }
    setSearching(true);
    setError(null);
    setResponse(null);
    setImported({});
    setAsked(Object.values(SOURCE_NAMES));
    try {
      setResponse(await sendJson<SearchResponse>(`/projects/${projectId}/papers/search`, "POST", body));
    } catch (failure) {
      setError(failure as ApiFailure);
    } finally {
      setSearching(false);
    }
  }

  async function importResult(index: number, result: SearchResult) {
    const body = result.doi ? { doi: result.doi } : { arxiv_id: result.arxiv_id };
    try {
      const paper = await sendJson<Paper>(`/projects/${projectId}/papers/import`, "POST", body);
      setImported((current) => ({ ...current, [index]: paper.id }));
    } catch (failure) {
      const known = failure instanceof ApiFailure && failure.code === "duplicate_paper"
        ? failure.details.paper_id
        : null;
      if (typeof known === "string") {
        setImported((current) => ({ ...current, [index]: known }));
      } else {
        throw failure;
      }
    }
    await onImported();
  }

  return (
    <section className="panel" data-testid="literature-search">
      <h2>检索文献</h2>
      <form className="stack" onSubmit={(event) => void submit(event)}>
        <label>
          关键词
          <input name="query" required maxLength={300} disabled={searching} />
        </label>
        <div className="row">
          <label style={{ width: 110 }}>
            起始年份
            <input name="year_from" type="number" min={1500} disabled={searching} />
          </label>
          <label style={{ width: 110 }}>
            截止年份
            <input name="year_to" type="number" min={1500} disabled={searching} />
          </label>
          <button className="primary" disabled={searching}>
            {searching ? "检索中…" : "检索"}
          </button>
        </div>
      </form>
      {searching && (
        <p className="muted small" data-testid="search-progress">
          正在向 {asked.join("、")} 发起检索，所有来源答复后一并显示。
        </p>
      )}
      {error && <ErrorNotice error={error} />}
      {response && (
        <div className="stack" data-testid="search-response" data-outcome={response.outcome}>
          <SourceBadges sources={response.sources} />
          <Outcome response={response} />
          {response.results.length > 0 && (
            <p className="muted small" data-testid="ranking-note">
              按各来源名次融合，不是相关性评分。
            </p>
          )}
          <ul className="list">
            {response.results.map((result, index) => (
              <ResultItem
                key={`${result.doi ?? ""}|${result.arxiv_id ?? ""}|${index}`}
                result={result}
                projectId={projectId}
                imported={imported[index] ?? null}
                onImport={(item) => importResult(index, item)}
              />
            ))}
          </ul>
          {Object.values(response.sources).some((report) => report.fetched_at) && (
            <span className="muted small">
              最早的取数时间：
              {formatTime(
                Object.values(response.sources)
                  .map((report) => report.fetched_at)
                  .filter((value): value is string => value !== null)
                  .sort()[0],
              )}
            </span>
          )}
        </div>
      )}
    </section>
  );
}
