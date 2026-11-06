"use client";

import { useParams, useSearchParams } from "next/navigation";
import { type ChangeEvent, type FormEvent, useEffect, useMemo, useState } from "react";

import { Notes } from "@/components/Notes";
import { PdfView } from "@/components/PdfView";
import { SummaryPanel } from "@/components/SummaryPanel";
import { type TranslationLanguage, TranslationPanel } from "@/components/TranslationPanel";
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
import type {
  Block,
  BlocksPage,
  Note,
  NotesPage,
  Paper,
  Project,
  TranslationsResult,
  Version,
  Workspace,
} from "@/lib/types";
import { type Resource, useResource } from "@/lib/useResource";

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

export type BlockSelection = { block: Block; start: number; end: number };

/** Where a point sits in the element's text, counting every text node in order. */
function offsetOf(element: Element, node: Node, offset: number): number | null {
  if (!element.contains(node)) {
    return null;
  }
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  let total = 0;
  for (let current = walker.nextNode(); current; current = walker.nextNode()) {
    if (current === node) {
      return node.nodeType === Node.TEXT_NODE ? total + offset : null;
    }
    total += current.textContent?.length ?? 0;
  }
  return null;
}

/** Character offsets of a selection inside one block, or null when it is not one block. */
function offsetsIn(block: Block, range: Range): [number, number] | null {
  const element = document.querySelector(`[data-block-id="${block.id}"]`);
  if (!element) {
    return null;
  }
  const a = offsetOf(element, range.startContainer, range.startOffset);
  const b = offsetOf(element, range.endContainer, range.endOffset);
  if (a === null || b === null) {
    return null;
  }
  const start = Math.min(a, b);
  const end = Math.max(a, b);
  // The element's text is exactly block.text, so these are the offsets the server checks.
  return end > start && end <= block.text.length ? [start, end] : null;
}

/** Splits the block's text around its annotation ranges; every character is still plain text. */
function marked(block: Block, ranges: { start: number; end: number }[]) {
  const sorted = [...ranges].sort((a, b) => a.start - b.start);
  const parts: { text: string; marked: boolean }[] = [];
  let at = 0;
  for (const { start, end } of sorted) {
    if (start < at || end > block.text.length) {
      continue;
    }
    if (start > at) {
      parts.push({ text: block.text.slice(at, start), marked: false });
    }
    parts.push({ text: block.text.slice(start, end), marked: true });
    at = end;
  }
  if (at < block.text.length) {
    parts.push({ text: block.text.slice(at), marked: false });
  }
  return parts;
}

function BlockList({
  blocks,
  selected,
  annotations,
  translations,
  onSelect,
  onRange,
}: {
  blocks: Block[];
  selected: Block | null;
  annotations: { blockId: string; start: number; end: number }[];
  translations: Map<string, string>;
  onSelect: (block: Block) => void;
  onRange: (selection: BlockSelection | null) => void;
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
              <div key={block.id}>
                <div
                  role="button"
                  tabIndex={0}
                  data-testid="block"
                  data-page={block.page_no}
                  data-located={block.bbox !== null}
                  data-block-id={block.id}
                  className={`block ${selected?.id === block.id ? "selected" : ""}`}
                  onClick={() => onSelect(block)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      onSelect(block);
                    }
                  }}
                  onMouseUp={() => {
                    const selection = window.getSelection();
                    if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
                      onRange(null);
                      return;
                    }
                    const offsets = offsetsIn(block, selection.getRangeAt(0));
                    onRange(offsets ? { block, start: offsets[0], end: offsets[1] } : null);
                  }}
                >
                  {marked(
                    block,
                    annotations.filter((item) => item.blockId === block.id),
                  ).map((part, index) =>
                    part.marked ? (
                      <mark key={index} data-testid="block-mark">
                        {part.text}
                      </mark>
                    ) : (
                      <span key={index}>{part.text}</span>
                    ),
                  )}
                </div>
                {/* Beside the block, never inside it: the block's text must stay exactly the
                    source text, so selection offsets match what the server checks. */}
                {translations.has(block.id) && (
                  <div className="block-translation" data-testid="block-translation">
                    <span className="block-translation-label">机器翻译</span>
                    {translations.get(block.id)}
                  </div>
                )}
              </div>
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
  blocks,
  translations,
  paper,
  version,
  selected,
  focusId,
  notes,
  onSelect,
  onRange,
}: {
  blocks: Resource<BlocksPage>;
  translations: Map<string, string>;
  paper: Paper;
  version: Version;
  selected: Block | null;
  focusId: string | null;
  notes: Note[];
  onSelect: (block: Block) => void;
  onRange: (selection: BlockSelection | null) => void;
}) {
  const job = version.id === paper.current_version?.id ? paper.parse_job : null;
  // Only located, ranged anchors of the version on screen can be drawn; everything else would
  // be a guess about where the text went.
  const annotations = notes.flatMap((note) => {
    const anchor = note.anchor;
    if (
      !anchor ||
      anchor.status !== "located" ||
      !anchor.block_id ||
      !anchor.range ||
      anchor.version_id !== version.id
    ) {
      return [];
    }
    return [{ blockId: anchor.block_id, start: anchor.range[0], end: anchor.range[1] }];
  });

  // A link from an answer (?block=) opens the reader with that block already picked. Waits for a
  // parsed list: the first render can still hold the empty one from before the version loaded.
  const [focused, setFocused] = useState<string | null>(null);
  useEffect(() => {
    if (!focusId || focused === focusId || !blocks.data?.parsed) {
      return;
    }
    const target = blocks.data.items.find((item) => item.id === focusId);
    setFocused(focusId);
    if (target) {
      onSelect(target);
    }
  }, [focusId, focused, blocks.data, onSelect]);

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
      <BlockList
        blocks={blocks.data?.items ?? []}
        selected={selected}
        annotations={annotations}
        translations={translations}
        onSelect={onSelect}
        onRange={onRange}
      />
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
    ({
      user: "手动填写",
      pdf_metadata: "来自 PDF 内嵌信息",
      arxiv: "来自 arXiv",
      openalex: "来自 OpenAlex",
      crossref: "来自 Crossref",
    })[paper.metadata_provenance[field]] ?? "未知";

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
  uploadLabel = "上传新版本",
}: {
  base: string;
  paper: Paper;
  selected: Version | null;
  disabled: boolean;
  onSelect: (version: Version) => void;
  onUploaded: () => Promise<void>;
  uploadLabel?: string;
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
          {uploadLabel}
          <input type="file" accept="application/pdf" onChange={choose} />
        </label>
      )}
    </div>
  );
}

function externalLink(value: string | undefined): string | null {
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

function NoPdfView({
  paper,
  base,
  projectId,
  projectName,
  readOnly,
  onChanged,
}: {
  paper: Paper;
  base: string;
  projectId: string;
  projectName: string;
  readOnly: boolean;
  onChanged: () => Promise<void>;
}) {
  const links = [
    ["开放获取链接", externalLink(paper.source_links?.open_access_url)],
    ["来源页面", externalLink(paper.source_links?.landing_url)],
  ] as const;
  return (
    <>
      <Header>
        <a href={`/projects/${projectId}`}>{projectName}</a>
      </Header>
      <main className="page" data-testid="no-pdf-page">
        <h1>{paper.title ?? "（标题未知）"}</h1>
        <div className="notice info">
          <strong>这篇文献还没有 PDF</strong>
          <span className="muted small">
            只导入了元数据，没有阅读器和文本块。上传 PDF 后会成为 v1 并开始解析。
          </span>
        </div>
        <div className="grid-2">
          <section className="panel">
            <h2>摘要</h2>
            {paper.abstract ? (
              <p data-testid="paper-abstract">{paper.abstract}</p>
            ) : (
              <p className="muted">摘要未知</p>
            )}
            <p className="muted small" data-testid="paper-venue">
              {paper.venue ?? "期刊/会议未知"}
            </p>
            <div className="row">
              {links.map(
                ([label, href]) =>
                  href && (
                    <a key={label} href={href} target="_blank" rel="noopener noreferrer">
                      {label}
                    </a>
                  ),
              )}
            </div>
          </section>
          <section className="panel">
            <h2>文献信息</h2>
            <MetadataForm paper={paper} base={base} disabled={readOnly} onSaved={onChanged} />
            <Versions
              base={base}
              paper={paper}
              selected={null}
              disabled={readOnly}
              onSelect={() => undefined}
              onUploaded={onChanged}
              uploadLabel="上传 PDF"
            />
          </section>
        </div>
        <section className="panel">
          <Notes projectId={projectId} paperId={paper.id} canWrite={!readOnly} />
        </section>
      </main>
    </>
  );
}

export default function ReaderPage() {
  const { id: projectId, paperId } = useParams<{ id: string; paperId: string }>();
  const focusId = useSearchParams().get("block");
  const base = `/projects/${projectId}/papers/${paperId}`;
  const paper = useResource(() => api<Paper>(base), base, (data) => isActive(data.parse_job));
  const project = useResource(() => api<Project>(`/projects/${projectId}`), projectId);
  const workspaces = useResource(() => api<Workspace[]>("/workspaces"), "workspaces");
  const [chosenVersion, setChosenVersion] = useState<Version | null>(null);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Block | null>(null);
  const [range, setRange] = useState<BlockSelection | null>(null);
  const [actionError, setActionError] = useState<ApiFailure | null>(null);
  const [notesRefresh, setNotesRefresh] = useState(0);
  const versions = useResource(
    () => api<Version[]>(`${base}/versions`),
    `${base}:${paper.data?.current_version?.id ?? "none"}`,
  );
  // The same notes the panel below shows: one read, so the marks and the list never disagree.
  const notes = useResource(
    () => api<NotesPage>(`/projects/${projectId}/notes?paper_id=${paperId}&limit=200`),
    `${projectId}:${paperId}:${notesRefresh}`,
  );

  const role = workspaces.data?.find((item) => item.id === project.data?.workspace_id)?.role;
  const readOnly = !role || role === "viewer" || project.data?.status !== "active";
  // The current version carries live job state; an older pick is only a read-only view.
  const current = paper.data?.current_version ?? null;
  const version = chosenVersion && chosenVersion.id !== current?.id ? chosenVersion : current;
  const job = paper.data?.parse_job ?? null;
  const parseKey = `${version?.id}:${version?.current_parse_job_id}`;
  const blocks = useResource(
    () =>
      version
        ? loadAllBlocks(`${base}/versions/${version.id}`)
        : Promise.resolve({ items: [], total: 0, offset: 0, limit: 0, parsed: false }),
    parseKey,
  );
  const [language, setLanguage] = useState<TranslationLanguage>("zh");
  const translations = useResource(
    () =>
      version
        ? api<TranslationsResult>(
            `${base}/versions/${version.id}/translations?language=${language}`,
          )
        : Promise.resolve(null),
    `${parseKey}:${language}`,
  );
  const translated = useMemo(
    () => new Map((translations.data?.items ?? []).map((item) => [item.block_id, item.text])),
    [translations.data],
  );

  function pick(block: Block) {
    setSelected(block);
    setPage(block.page_no);
  }

  // The note carries what it needs to light up the block: the server already matched it to a
  // block of the version's current parse, so no second lookup is made here.
  function jumpTo(note: Note, versions: Version[] | null) {
    const anchor = note.anchor;
    if (!anchor || anchor.status !== "located" || !anchor.block_id || !anchor.bbox) {
      return;
    }
    const target = versions?.find((item) => item.id === anchor.version_id);
    if (target && target.id !== version?.id) {
      switchVersion(target);
    }
    pick({
      id: anchor.block_id,
      seq: 0,
      page_no: anchor.page_no,
      kind: "text_line",
      text: anchor.text,
      bbox: anchor.bbox,
      granularity: anchor.granularity,
      content_hash: "",
    });
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

  if (paper.data && !paper.data.current_version && !paper.loading) {
    return (
      <NoPdfView
        paper={paper.data}
        base={base}
        projectId={projectId}
        projectName={project.data?.name ?? "返回项目"}
        readOnly={readOnly}
        onChanged={async () => {
          setChosenVersion(null);
          await paper.reload();
        }}
      />
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
              blocks={blocks}
              translations={translated}
              paper={paper.data}
              version={version}
              selected={selected}
              focusId={focusId}
              notes={notes.data?.items ?? []}
              onSelect={pick}
              onRange={setRange}
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
            {blocks.data?.parsed && (
              <TranslationPanel
                base={`${base}/versions/${version.id}`}
                page={page}
                blocks={blocks.data.items}
                translated={new Set(translated.keys())}
                language={language}
                canTranslate={role === "member" || role === "admin" || role === "owner"}
                onLanguage={setLanguage}
                onTranslated={translations.reload}
              />
            )}
            <SummaryPanel
              projectId={projectId}
              paperId={paperId}
              canAsk={role === "member" || role === "admin" || role === "owner"}
              canWrite={!readOnly}
              onSaved={() => setNotesRefresh((count) => count + 1)}
            />
            <Notes
              refresh={notesRefresh}
              projectId={projectId}
              paperId={paperId}
              canWrite={!readOnly}
              target={
                selected
                  ? { versionId: version.id, blockId: selected.id, pageNo: selected.page_no }
                  : null
              }
              selection={
                range
                  ? {
                      versionId: version.id,
                      blockId: range.block.id,
                      pageNo: range.block.page_no,
                      start: range.start,
                      end: range.end,
                    }
                  : null
              }
              onAnnotated={() => setRange(null)}
              onNotesChanged={() => setNotesRefresh((count) => count + 1)}
              currentVersionNo={version.version_no}
              onJump={(note) => jumpTo(note, versions.data)}
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
