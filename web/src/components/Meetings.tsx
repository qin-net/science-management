"use client";

import { type FormEvent, useState } from "react";

import { ErrorNotice, formatTime, Loading } from "@/components/ui";
import { api, ApiFailure, sendJson } from "@/lib/api";
import type { Meeting, MeetingsPage } from "@/lib/types";
import { useResource } from "@/lib/useResource";

type Fields = { title: string; starts_at: string; location: string };

const EMPTY: Fields = { title: "", starts_at: "", location: "" };

/** `datetime-local` wants "YYYY-MM-DDTHH:mm" in the browser's own time zone. */
function toLocalInput(iso: string): string {
  const date = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** An absolute instant with an offset; the server refuses times without one. */
function toInstant(local: string): string {
  return new Date(local).toISOString();
}

function FieldInputs({
  value,
  onChange,
  disabled,
}: {
  value: Fields;
  onChange: (next: Fields) => void;
  disabled: boolean;
}) {
  const set = <K extends keyof Fields>(key: K, next: Fields[K]) => onChange({ ...value, [key]: next });
  return (
    <>
      <label>
        标题
        <input
          name="title"
          value={value.title}
          maxLength={200}
          required
          disabled={disabled}
          onChange={(event) => set("title", event.target.value)}
        />
      </label>
      <div className="row">
        <label>
          开始时间（本地时间）
          <input
            name="starts_at"
            type="datetime-local"
            value={value.starts_at}
            required
            disabled={disabled}
            onChange={(event) => set("starts_at", event.target.value)}
          />
        </label>
        <label>
          地点（文字）
          <input
            name="location"
            value={value.location}
            maxLength={200}
            disabled={disabled}
            onChange={(event) => set("location", event.target.value)}
          />
        </label>
      </div>
    </>
  );
}

function MeetingRow({
  meeting,
  projectId,
  onChanged,
}: {
  meeting: Meeting;
  projectId: string;
  onChanged: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Fields>(EMPTY);
  const [error, setError] = useState<ApiFailure | null>(null);
  const [busy, setBusy] = useState(false);
  const url = `/projects/${projectId}/meetings/${meeting.id}`;

  async function send(method: "PATCH" | "DELETE", body?: Record<string, unknown>): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      await sendJson(url, method, body);
      await onChanged();
      return true;
    } catch (failure) {
      // The draft stays: a conflict must not cost the user what they typed.
      setError(failure as ApiFailure);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const done = await send("PATCH", {
      expected_version: meeting.version,
      title: draft.title,
      starts_at: toInstant(draft.starts_at),
      location: draft.location.trim() === "" ? null : draft.location,
    });
    if (done) {
      setEditing(false);
    }
  }

  async function remove() {
    if (window.confirm("删除这个组会？此操作不可恢复。")) {
      await send("DELETE");
    }
  }

  return (
    <li className="list-item" data-testid="meeting" data-past={meeting.past}>
      {editing ? (
        <form className="stack" onSubmit={(event) => void save(event)}>
          <FieldInputs value={draft} onChange={setDraft} disabled={busy} />
          <div className="row">
            <button className="primary" disabled={busy || draft.title.trim() === "" || draft.starts_at === ""}>
              保存
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setEditing(false);
                setError(null);
              }}
            >
              取消
            </button>
          </div>
        </form>
      ) : (
        <>
          <div className="row">
            <strong className="note-body" data-testid="meeting-title">
              {meeting.title}
            </strong>
            {meeting.past && (
              <span className="badge queued" data-testid="meeting-past">
                已结束
              </span>
            )}
          </div>
          <span className="muted small" data-testid="meeting-time">
            {formatTime(meeting.starts_at)}
          </span>
          {meeting.location && (
            <span className="note-body small" data-testid="meeting-location">
              地点：{meeting.location}
            </span>
          )}
          <span className="muted small">登记人 {meeting.created_by_name}</span>
        </>
      )}
      {error && <ErrorNotice error={error} />}
      {meeting.editable && !editing && (
        <div className="row">
          <button
            disabled={busy}
            data-testid="meeting-edit"
            onClick={() => {
              setDraft({
                title: meeting.title,
                starts_at: toLocalInput(meeting.starts_at),
                location: meeting.location ?? "",
              });
              setEditing(true);
            }}
          >
            编辑
          </button>
          {meeting.deletable && (
            <button className="danger" disabled={busy} data-testid="meeting-delete" onClick={() => void remove()}>
              删除
            </button>
          )}
        </div>
      )}
    </li>
  );
}

export function Meetings({ projectId, canWrite }: { projectId: string; canWrite: boolean }) {
  // canWrite is part of the key: archiving the project on this page changes every row's editable flag.
  const page = useResource(
    () => api<MeetingsPage>(`/projects/${projectId}/meetings`),
    `${projectId}:${canWrite}`,
  );
  const [draft, setDraft] = useState<Fields>(EMPTY);
  const [error, setError] = useState<ApiFailure | null>(null);
  const [busy, setBusy] = useState(false);

  async function create(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await sendJson(`/projects/${projectId}/meetings`, "POST", {
        title: draft.title,
        starts_at: toInstant(draft.starts_at),
        location: draft.location.trim() === "" ? null : draft.location,
      });
      setDraft(EMPTY);
      await page.reload();
    } catch (failure) {
      setError(failure as ApiFailure);
    } finally {
      setBusy(false);
    }
  }

  // Upcoming first, soonest on top; finished ones after, newest on top.
  const items = page.data?.items ?? [];
  const upcoming = items.filter((meeting) => !meeting.past);
  const finished = items.filter((meeting) => meeting.past).reverse();

  return (
    <section className="panel" data-testid="meetings-panel">
      <h2>组会</h2>
      {canWrite && (
        <form className="stack" data-testid="meeting-form" onSubmit={(event) => void create(event)}>
          <FieldInputs value={draft} onChange={setDraft} disabled={busy} />
          <div>
            <button className="primary" disabled={busy || draft.title.trim() === "" || draft.starts_at === ""}>
              登记组会
            </button>
          </div>
        </form>
      )}
      {error && <ErrorNotice error={error} />}
      {page.loading && <Loading />}
      {page.error && <ErrorNotice error={page.error} onRetry={page.reload} />}
      {page.data && items.length === 0 && (
        <div className="empty" data-testid="meetings-empty">
          还没有登记组会。
        </div>
      )}
      {page.data && items.length > 0 && (
        <ul className="list">
          {[...upcoming, ...finished].map((meeting) => (
            <MeetingRow key={meeting.id} meeting={meeting} projectId={projectId} onChanged={page.reload} />
          ))}
        </ul>
      )}
    </section>
  );
}
