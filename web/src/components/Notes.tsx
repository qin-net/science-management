"use client";

import Link from "next/link";
import { type FormEvent, useState } from "react";

import { ErrorNotice, formatTime, Loading } from "@/components/ui";
import { api, ApiFailure, sendJson } from "@/lib/api";
import type { Note, NotesPage } from "@/lib/types";
import { useResource } from "@/lib/useResource";

const KIND_LABELS = { project: "项目笔记", paper: "无来源", annotation: "批注" } as const;

// What the reader hands over when a block is selected: the notes panel never decides which block
// or version to anchor to, and the server checks both again.
export type AnnotationTarget = { versionId: string; blockId: string; pageNo: number };

// A selection inside one block (specs/011). Offsets are characters of that block's own text, and
// the server cuts the quote from the block itself.
export type TextSelection = AnnotationTarget & { start: number; end: number };

function Anchor({
  note,
  currentVersionNo,
  onJump,
}: {
  note: Note;
  currentVersionNo: number | null;
  onJump?: (note: Note) => void;
}) {
  const anchor = note.anchor;
  if (!anchor) {
    return null;
  }
  const where = `第 ${anchor.page_no} 页 · v${anchor.version_no}`;
  const older = currentVersionNo !== null && anchor.version_no !== currentVersionNo;
  // A ranged note quotes only its range; a whole-block one still quotes the whole block.
  const quote = anchor.quote ?? anchor.text;
  return (
    <div className="stack" data-testid="note-anchor" data-status={anchor.status}>
      <span className="muted small" data-testid="note-where">
        {where}
        {older ? "（来自旧版本）" : ""}
        {anchor.range ? " · 部分文字" : ""}
      </span>
      {anchor.status === "located" && onJump ? (
        <button className="note-quote jump" data-testid="note-jump" onClick={() => onJump(note)}>
          {quote}
        </button>
      ) : (
        <span
          className={`note-quote ${anchor.status === "located" ? "" : "stale"}`}
          data-testid="note-quote"
        >
          {quote}
        </span>
      )}
      {anchor.status === "stale" && (
        <span className="small" data-testid="note-stale">
          原文位置已变化，上面是记录时的引用，无法定位到当前解析结果。
        </span>
      )}
      {anchor.status === "unparsed" && (
        <span className="small" data-testid="note-unparsed">
          该版本当前没有解析结果，上面是记录时的引用。
        </span>
      )}
    </div>
  );
}

function NoteItem({
  note,
  projectId,
  showPaper,
  currentVersionNo,
  onJump,
  onChanged,
}: {
  note: Note;
  projectId: string;
  showPaper: boolean;
  currentVersionNo: number | null;
  onJump?: (note: Note) => void;
  onChanged: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(note.body);
  const [error, setError] = useState<ApiFailure | null>(null);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await sendJson(`/projects/${projectId}/notes/${note.id}`, "PATCH", {
        body: draft,
        expected_version: note.version,
      });
      setEditing(false);
      await onChanged();
    } catch (failure) {
      // The draft stays in the box: a conflict must not cost the user what they typed.
      setError(failure as ApiFailure);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!window.confirm("删除这条笔记？此操作不可恢复。")) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await sendJson(`/projects/${projectId}/notes/${note.id}`, "DELETE");
      await onChanged();
    } catch (failure) {
      setError(failure as ApiFailure);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="list-item" data-testid="note" data-kind={note.kind}>
      <div className="row-between">
        <span className="badge queued" data-testid="note-kind">
          {KIND_LABELS[note.kind]}
        </span>
        <span className="muted small">
          {note.author_name || "未知用户"} · {formatTime(note.updated_at)}
          {note.version > 1 ? "（已编辑）" : ""}
        </span>
      </div>
      {showPaper && note.paper_id && (
        <Link href={`/projects/${projectId}/papers/${note.paper_id}`} data-testid="note-paper">
          {note.paper_title ?? "（标题未知）"}
        </Link>
      )}
      <Anchor note={note} currentVersionNo={currentVersionNo} onJump={onJump} />
      {editing ? (
        <>
          <textarea
            aria-label="编辑笔记"
            value={draft}
            maxLength={20000}
            rows={4}
            onChange={(event) => setDraft(event.target.value)}
          />
          <div className="row">
            <button
              className="primary"
              disabled={busy || (note.kind !== "annotation" && draft.trim() === "")}
              onClick={() => void save()}
            >
              保存
            </button>
            <button
              disabled={busy}
              onClick={() => {
                setEditing(false);
                setDraft(note.body);
                setError(null);
              }}
            >
              取消
            </button>
          </div>
        </>
      ) : (
        note.body && (
          <p className="note-body" data-testid="note-body">
            {note.body}
          </p>
        )
      )}
      {error && <ErrorNotice error={error} />}
      {!editing && (note.editable || note.deletable) && (
        <div className="row">
          {note.editable && (
            <button
              disabled={busy}
              onClick={() => {
                setDraft(note.body);
                setEditing(true);
              }}
            >
              编辑
            </button>
          )}
          {note.deletable && (
            <button className="danger" disabled={busy} onClick={() => void remove()}>
              删除
            </button>
          )}
        </div>
      )}
    </li>
  );
}

export function Notes({
  projectId,
  paperId,
  canWrite,
  target,
  selection = null,
  currentVersionNo = null,
  onJump,
  onAnnotated,
  onNotesChanged,
  refresh = 0,
}: {
  projectId: string;
  /** Only this paper's notes; omitted means the whole project. */
  paperId?: string;
  canWrite: boolean;
  /** The block currently selected in the reader; enables "annotate the selected block". */
  target?: AnnotationTarget | null;
  /** Text selected inside that block; enables "annotate the selected text" (specs/011). */
  selection?: TextSelection | null;
  currentVersionNo?: number | null;
  onJump?: (note: Note) => void;
  /** Called once a ranged note exists, so the reader can drop the stale selection. */
  onAnnotated?: () => void;
  /** Called after any change, for the reader that draws the marks from the same list. */
  onNotesChanged?: () => void;
  /** Changes when something outside (a deleted paper) may have removed notes. */
  refresh?: number;
}) {
  const query = paperId ? `?paper_id=${paperId}&limit=200` : "?limit=200";
  const notes = useResource(
    () => api<NotesPage>(`/projects/${projectId}/notes${query}`),
    `${projectId}:${paperId ?? "all"}:${refresh}`,
  );
  const [body, setBody] = useState("");
  const [error, setError] = useState<ApiFailure | null>(null);
  const [busy, setBusy] = useState(false);

  async function create(anchored: "block" | "selection" | false) {
    setBusy(true);
    setError(null);
    try {
      const payload: Record<string, unknown> = { body };
      if (paperId) {
        payload.paper_id = paperId;
      }
      if (anchored === "selection" && selection) {
        payload.anchor = {
          version_id: selection.versionId,
          block_id: selection.blockId,
          start: selection.start,
          end: selection.end,
        };
      } else if (anchored === "block" && target) {
        payload.anchor = { version_id: target.versionId, block_id: target.blockId };
      }
      await sendJson(`/projects/${projectId}/notes`, "POST", payload);
      setBody("");
      await notes.reload();
      onNotesChanged?.();
      if (anchored === "selection") {
        onAnnotated?.();
      }
    } catch (failure) {
      setError(failure as ApiFailure);
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void create(false);
  }

  return (
    <section className="stack" data-testid="notes-panel">
      <h2>笔记{notes.data ? `（${notes.data.total}）` : ""}</h2>
      {canWrite && (
        <form className="stack" onSubmit={submit}>
          <label>
            笔记内容
            <textarea
              name="note"
              value={body}
              maxLength={20000}
              rows={3}
              disabled={busy}
              onChange={(event) => setBody(event.target.value)}
            />
          </label>
          <div className="row">
            <button className="primary" disabled={busy || body.trim() === ""}>
              {paperId ? "添加文献笔记（无来源）" : "添加项目笔记"}
            </button>
            {selection && (
              <button
                type="button"
                data-testid="annotate-selection"
                disabled={busy}
                onClick={() => void create("selection")}
              >
                批注所选文字（第 {selection.pageNo} 页）
              </button>
            )}
            {target && (
              <button
                type="button"
                data-testid="annotate-selected"
                disabled={busy}
                onClick={() => void create("block")}
              >
                批注所选文本块（第 {target.pageNo} 页）
              </button>
            )}
          </div>
        </form>
      )}
      {error && <ErrorNotice error={error} />}
      {notes.loading && <Loading />}
      {notes.error && <ErrorNotice error={notes.error} onRetry={notes.reload} />}
      {notes.data?.items.length === 0 && (
        <div className="empty" data-testid="notes-empty">
          还没有笔记。
        </div>
      )}
      <ul className="list">
        {notes.data?.items.map((note) => (
          <NoteItem
            key={note.id}
            note={note}
            projectId={projectId}
            showPaper={!paperId}
            currentVersionNo={currentVersionNo}
            onJump={onJump}
            onChanged={async () => {
              await notes.reload();
              onNotesChanged?.();
            }}
          />
        ))}
      </ul>
    </section>
  );
}
