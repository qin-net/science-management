"use client";

import { useParams } from "next/navigation";
import { type ChangeEvent, type FormEvent, useMemo, useState } from "react";

import { PdfView } from "@/components/PdfView";
import {
  ErrorNotice,
  formatBytes,
  formatTime,
  Header,
  isActive,
  JobBadge,
  JobError,
  Loading,
} from "@/components/ui";
import { api, ApiFailure, sendJson, uploadPdf } from "@/lib/api";
import type { Block, BlocksPage, Paper, Project, Version, Workspace } from "@/lib/types";
import { useResource } from "@/lib/useResource";

const BLOCK_PAGE_SIZE = 2000;

async function loadAllBlocks(base: string): Promise<BlocksPage> {
  const first = await api<BlocksPage>(`${base}/blocks?limit=${BLOCK_PAGE_SIZE}`);
  const items = [...first.items];
  while (items.length < first.total) {
    const next = await api<BlocksPage>(
      `${base}/blocks?limit=${BLOCK_PAGE_SIZE}&offset=${items.length}`,
    );
    if (next.items.length === 0) {
      break;
    }
    items.push(...next.items);
  }
  return { ...first, items };
}

function BlockList({
  blocks,
  selected,
  onSelect,
}: {
  blocks: Block[];
  selected: Block | null;
  onSelect: (block: Block) => void;
}) {
  const pages = useMemo(() => {
    const grouped = new Map<number, Block[]>();
    for (const block of blocks) {
      grouped.set(block.page_no, [...(grouped.get(block.page_no) ?? []), block]);
    }
    return [...grouped.entries()];
  }, [blocks]);

  return (
    <>
      {pages.map(([page, items]) => (
        <div key={page}>
          <div className="block-page-label">第 {page} 页</div>
          {items.map((block) =>
            block.text ? (
              <button
                key={block.id}
                data-testid="block"
                data-page={block.page_no}
                data-located={block.bbox !== null}
                className={`block ${selected?.id === block.id ? "selected" : ""}`}
                onClick={() => onSelect(block)}
              >
                {block.text}
              </button>
            ) : (
              <p className="muted small" key={block.id} data-testid="block-empty-page">
                这一页没有可提取的文本（可能是扫描页）。
              </p>
            ),
          )}
        </div>
      ))}
    </>
  );
}

function Blocks({
  base,
  paper,
  version,
  selected,
  onSelect,
}: {
  base: string;
  paper: Paper;
  version: Version;
  selected: Block | null;
  onSelect: (block: Block) => void;
}) {
  const job = version.id === paper.current_version?.id ? paper.parse_job : null;
  const blocks = useResource(
    () => loadAllBlocks(`${base}/versions/${version.id}`),
    `${version.id}:${version.current_parse_job_id}`,
  );

  return (
    <>
      <h2>解析出的文本块{blocks.data?.parsed ? `（${blocks.data.total}）` : ""}</h2>
      {blocks.loading && <Loading />}
      {blocks.error && <ErrorNotice error={blocks.error} onRetry={blocks.reload} />}
      {blocks.data && !blocks.data.parsed && (
        <div className="empty" data-testid="blocks-unavailable">
          {isActive(job)
            ? "解析完成后，文本块会显示在这里。PDF 现在就可以阅读。"
            : "这个版本没有可用的文本块。"}
        </div>
      )}
      {blocks.data?.parsed && job?.result.warnings?.map((warning) => (
        <div className="notice warn small" key={warning}>
          {warning}
        </div>
      ))}
      {selected && selected.bbox === null && (
        <div className="notice info small" data-testid="page-level-note">
          这个文本块只有页级定位，无法在页面上标出准确位置。
        </div>
      )}
      <BlockList blocks={blocks.data?.items ?? []} selected={selected} onSelect={onSelect} />
    </>
  );
}

function MetadataForm({
  paper,
  base,
  disabled,
  onSaved,
}: {
  paper: Paper;
  base: string;
  disabled: boolean;
  onSaved: () => Promise<void>;
}) {
  const [error, setError] = useState<ApiFailure | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const year = String(form.get("year") ?? "").trim();
    setError(null);
    try {
      await sendJson(base, "PATCH", {
        title: form.get("title"),
        authors: String(form.get("authors") ?? "").split(/[,，;；]/),
        year: year ? Number(year) : null,
        doi: form.get("doi"),
        arxiv_id: form.get("arxiv_id"),
      });
      await onSaved();
    } catch (failure) {
      setError(failure as ApiFailure);
    }
  }

  const source = (field: string) =>
    ({ user: "手动填写", pdf_metadata: "来自 PDF 内嵌信息" })[paper.metadata_provenance[field]] ??
    "未知";

  return (
    <form className="meta-table" onSubmit={submit} key={JSON.stringify(paper.metadata_provenance)}>
      <label>
        标题（{source("title")}）
        <input name="title" defaultValue={paper.title ?? ""} disabled={disabled} />
      </label>
      <label>
        作者，逗号分隔（{source("authors")}）
        <input name="authors" defaultValue={paper.authors.join(", ")} disabled={disabled} />
      </label>
      <label>
        年份
        <input name="year" type="number" defaultValue={paper.year ?? ""} disabled={disabled} />
      </label>
      <label>
        DOI
        <input name="doi" defaultValue={paper.doi ?? ""} disabled={disabled} />
      </label>
      <label>
        arXiv ID
        <input name="arxiv_id" defaultValue={paper.arxiv_id ?? ""} disabled={disabled} />
      </label>
      {error && <ErrorNotice error={error} />}
      {!disabled && (
        <div>
          <button>保存信息</button>
        </div>
      )}
    </form>
  );
}

function Versions({
  base,
  paper,
  selected,
  disabled,
  onSelect,
  onUploaded,
}: {
  base: string;
  paper: Paper;
  selected: Version | null;
  disabled: boolean;
  onSelect: (version: Version) => void;
  onUploaded: () => Promise<void>;
}) {
  const versions = useResource(
    () => api<Version[]>(`${base}/versions`),
    paper.current_version?.id ?? "none",
  );
  const [error, setError] = useState<ApiFailure | null>(null);

  async function choose(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) {
      return;
    }
    setError(null);
    try {
      await uploadPdf(`${base}/versions`, file);
      await onUploaded();
    } catch (failure) {
      setError(failure as ApiFailure);
    }
  }

  return (
    <div className="stack">
      <h2>版本</h2>
      {versions.error && <ErrorNotice error={versions.error} onRetry={versions.reload} />}
      <ul className="list">
        {versions.data?.map((version) => (
          <li className="list-item small" key={version.id}>
            <button disabled={version.id === selected?.id} onClick={() => onSelect(version)}>
              v{version.version_no}
              {version.id === paper.current_version?.id ? "（当前）" : ""}
            </button>
            <span className="muted">
              {version.original_filename} · {formatBytes(version.size_bytes)}
            </span>
            <span className="muted">{formatTime(version.created_at)}</span>
            <span className="mono muted" title={version.sha256}>
              {version.sha256.slice(0, 16)}…
            </span>
          </li>
        ))}
      </ul>
      {error && <ErrorNotice error={error} />}
      {!disabled && (
        <label>
          上传新版本
          <input type="file" accept="application/pdf" onChange={choose} />
        </label>
      )}
    </div>
  );
}

export default function ReaderPage() {
  const { id: projectId, paperId } = useParams<{ id: string; paperId: string }>();
  const base = `/projects/${projectId}/papers/${paperId}`;
  const paper = useResource(() => api<Paper>(base), base, (data) => isActive(data.parse_job));
  const project = useResource(() => api<Project>(`/projects/${projectId}`), projectId);
  const workspaces = useResource(() => api<Workspace[]>("/workspaces"), "workspaces");
  const [chosenVersion, setChosenVersion] = useState<Version | null>(null);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Block | null>(null);
  const [actionError, setActionError] = useState<ApiFailure | null>(null);

  const role = workspaces.data?.find((item) => item.id === project.data?.workspace_id)?.role;
  const readOnly = !role || role === "viewer" || project.data?.status !== "active";
  // The current version carries live job state; an older pick is only a read-only view.
  const current = paper.data?.current_version ?? null;
  const version = chosenVersion && chosenVersion.id !== current?.id ? chosenVersion : current;
  const job = paper.data?.parse_job ?? null;

  function pick(block: Block) {
    setSelected(block);
    setPage(block.page_no);
  }

  function switchVersion(next: Version) {
    setChosenVersion(next);
    setSelected(null);
    setPage(1);
  }

  async function run(action: () => Promise<unknown>) {
    setActionError(null);
    try {
      await action();
      await paper.reload();
    } catch (failure) {
      setActionError(failure as ApiFailure);
    }
  }

  if (paper.error) {
    return (
      <>
        <Header />
        <main className="page">
          {paper.error.status === 404 ? (
            <div className="empty" data-testid="not-found">
              文献不存在，或你无权访问。
            </div>
          ) : (
            <ErrorNotice error={paper.error} onRetry={paper.reload} />
          )}
        </main>
      </>
    );
  }

  return (
    <>
      <Header>
        <a href={`/projects/${projectId}`}>{project.data?.name ?? "返回项目"}</a>
      </Header>
      {paper.loading || !paper.data || !version ? (
        <main className="page">
          <Loading />
        </main>
      ) : (
        <div className="reader">
          <aside className="reader-pane left">
            <Blocks
              base={base}
              paper={paper.data}
              version={version}
              selected={selected}
              onSelect={pick}
            />
          </aside>
          <main className="reader-pane center">
            <PdfView
              url={`/api/v1${base}/versions/${version.id}/file`}
              page={page}
              highlight={selected}
              onPageChange={setPage}
            />
          </main>
          <aside className="reader-pane right">
            <div className="row-between">
              <h2>解析状态</h2>
              <JobBadge job={job} />
            </div>
            <JobError job={job} />
            {job?.result.parser_version && (
              <span className="muted small">{job.result.parser_version}</span>
            )}
            {actionError && <ErrorNotice error={actionError} />}
            {!readOnly && job && isActive(job) && !job.cancel_requested && (
              <button
                onClick={() =>
                  void run(() => sendJson(`/projects/${projectId}/jobs/${job.id}/cancel`, "POST"))
                }
              >
                取消解析
              </button>
            )}
            {!readOnly && paper.data.current_version && !isActive(job) && (
              <button
                onClick={() =>
                  void run(() =>
                    sendJson(
                      `${base}/versions/${paper.data!.current_version!.id}/parse`,
                      "POST",
                    ),
                  )
                }
              >
                重新解析
              </button>
            )}
            <h2>文献信息</h2>
            <MetadataForm
              paper={paper.data}
              base={base}
              disabled={readOnly}
              onSaved={paper.reload}
            />
            <Versions
              base={base}
              paper={paper.data}
              selected={version}
              disabled={readOnly}
              onSelect={switchVersion}
              onUploaded={async () => {
                setChosenVersion(null);
                setSelected(null);
                setPage(1);
                await paper.reload();
              }}
            />
          </aside>
        </div>
      )}
    </>
  );
}
