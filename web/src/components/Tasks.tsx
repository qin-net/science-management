"use client";

import Link from "next/link";
import { type FormEvent, useState } from "react";

import { ErrorNotice, Loading } from "@/components/ui";
import { api, ApiFailure, sendJson } from "@/lib/api";
import type { Member, PapersPage, Task, TaskPriority, TaskStatus, TasksPage } from "@/lib/types";
import { useResource } from "@/lib/useResource";

const STATUSES: TaskStatus[] = ["todo", "doing", "blocked", "done", "cancelled"];
export const STATUS_LABELS: Record<TaskStatus, string> = {
  todo: "待办",
  doing: "进行中",
  blocked: "受阻",
  done: "完成",
  cancelled: "已取消",
};
const PRIORITY_LABELS: Record<TaskPriority, string> = { low: "低", normal: "普通", high: "高" };

type Choices = {
  members: Member[];
  papers: { id: string; title: string }[];
};

type Fields = {
  title: string;
  description: string;
  priority: TaskPriority;
  assignee_id: string;
  due_date: string;
  paper_id: string;
  blocked_reason: string;
};

const EMPTY: Fields = {
  title: "",
  description: "",
  priority: "normal",
  assignee_id: "",
  due_date: "",
  paper_id: "",
  blocked_reason: "",
};

function fieldsOf(task: Task): Fields {
  return {
    title: task.title,
    description: task.description,
    priority: task.priority,
    assignee_id: task.assignee_id ?? "",
    due_date: task.due_date ?? "",
    paper_id: task.paper_id ?? "",
    blocked_reason: task.blocked_reason ?? "",
  };
}

function FieldInputs({
  value,
  onChange,
  choices,
  task,
  disabled,
}: {
  value: Fields;
  onChange: (next: Fields) => void;
  choices: Choices;
  task?: Task;
  disabled: boolean;
}) {
  const set = <K extends keyof Fields>(key: K, next: Fields[K]) => onChange({ ...value, [key]: next });
  const departed = task?.assignee_id && task.assignee_active === false ? task : null;
  const paperGone = value.paper_id && !choices.papers.some((paper) => paper.id === value.paper_id);
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
      <label>
        说明
        <textarea
          name="description"
          value={value.description}
          maxLength={5000}
          rows={3}
          disabled={disabled}
          onChange={(event) => set("description", event.target.value)}
        />
      </label>
      {task?.status === "blocked" && (
        <label>
          受阻原因
          <input
            name="blocked_reason"
            value={value.blocked_reason}
            maxLength={500}
            required
            disabled={disabled}
            onChange={(event) => set("blocked_reason", event.target.value)}
          />
        </label>
      )}
      <div className="row">
        <label>
          优先级
          <select
            name="priority"
            value={value.priority}
            disabled={disabled}
            onChange={(event) => set("priority", event.target.value as TaskPriority)}
          >
            {(Object.keys(PRIORITY_LABELS) as TaskPriority[]).map((key) => (
              <option key={key} value={key}>
                {PRIORITY_LABELS[key]}
              </option>
            ))}
          </select>
        </label>
        <label>
          负责人
          <select
            name="assignee"
            value={value.assignee_id}
            disabled={disabled}
            onChange={(event) => set("assignee_id", event.target.value)}
          >
            <option value="">未分配</option>
            {departed && (
              <option value={departed.assignee_id ?? ""}>
                {departed.assignee_name}（已不是成员）
              </option>
            )}
            {choices.members.map((member) => (
              <option key={member.user_id} value={member.user_id}>
                {member.display_name}
              </option>
            ))}
          </select>
        </label>
        <label>
          期限
          <input
            name="due_date"
            type="date"
            value={value.due_date}
            disabled={disabled}
            onChange={(event) => set("due_date", event.target.value)}
          />
        </label>
        <label>
          关联文献
          <select
            name="paper"
            value={value.paper_id}
            disabled={disabled}
            onChange={(event) => set("paper_id", event.target.value)}
          >
            <option value="">无</option>
            {paperGone && <option value={value.paper_id}>（当前关联）</option>}
            {choices.papers.map((paper) => (
              <option key={paper.id} value={paper.id}>
                {paper.title}
              </option>
            ))}
          </select>
        </label>
      </div>
    </>
  );
}

function TaskCard({
  task,
  projectId,
  choices,
  onChanged,
}: {
  task: Task;
  projectId: string;
  choices: Choices;
  onChanged: () => Promise<void>;
}) {
  const [mode, setMode] = useState<"view" | "edit" | "block">("view");
  const [draft, setDraft] = useState<Fields>(fieldsOf(task));
  const [reason, setReason] = useState("");
  const [error, setError] = useState<ApiFailure | null>(null);
  const [busy, setBusy] = useState(false);
  const url = `/projects/${projectId}/tasks/${task.id}`;

  async function send(method: "PATCH" | "DELETE", body?: Record<string, unknown>): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      await sendJson(url, method, body);
      await onChanged();
      return true;
    } catch (failure) {
      // Drafts stay where they are: a conflict must not cost the user what they typed.
      setError(failure as ApiFailure);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function changeStatus(next: TaskStatus) {
    if (next === task.status) {
      return;
    }
    if (next === "blocked") {
      setReason("");
      setMode("block");
      return;
    }
    await send("PATCH", { expected_version: task.version, status: next });
  }

  async function saveBlock(event: FormEvent) {
    event.preventDefault();
    if (await send("PATCH", { expected_version: task.version, status: "blocked", blocked_reason: reason })) {
      setMode("view");
    }
  }

  async function saveEdit(event: FormEvent) {
    event.preventDefault();
    const body: Record<string, unknown> = {
      expected_version: task.version,
      title: draft.title,
      description: draft.description,
      priority: draft.priority,
      assignee_id: draft.assignee_id || null,
      due_date: draft.due_date || null,
      paper_id: draft.paper_id || null,
    };
    if (task.status === "blocked") {
      body.blocked_reason = draft.blocked_reason;
    }
    if (await send("PATCH", body)) {
      setMode("view");
    }
  }

  async function remove() {
    if (window.confirm("删除这个任务？此操作不可恢复。")) {
      await send("DELETE");
    }
  }

  return (
    <li className="list-item task" data-testid="task" data-status={task.status}>
      {mode === "edit" ? (
        <form className="stack" onSubmit={(event) => void saveEdit(event)}>
          <FieldInputs value={draft} onChange={setDraft} choices={choices} task={task} disabled={busy} />
          <div className="row">
            <button className="primary" disabled={busy || draft.title.trim() === ""}>
              保存
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setMode("view");
                setDraft(fieldsOf(task));
                setError(null);
              }}
            >
              取消
            </button>
          </div>
        </form>
      ) : (
        <>
          <strong className="note-body" data-testid="task-title">
            {task.title}
          </strong>
          <div className="row small">
            {task.priority !== "normal" && (
              <span className={`badge ${task.priority === "high" ? "failed" : "queued"}`}>
                优先级：{PRIORITY_LABELS[task.priority]}
              </span>
            )}
            {task.overdue && (
              <span className="badge failed" data-testid="task-overdue">
                已逾期
              </span>
            )}
            {task.due_date && (
              <span className="muted" data-testid="task-due">
                期限 {task.due_date}
              </span>
            )}
            <span className="muted" data-testid="task-assignee">
              {task.assignee_id
                ? `${task.assignee_name ?? "未知用户"}${task.assignee_active === false ? "（已不是成员）" : ""}`
                : "未分配"}
            </span>
          </div>
          {task.paper_id && (
            <Link href={`/projects/${projectId}/papers/${task.paper_id}`} data-testid="task-paper">
              {task.paper_title ?? "（标题未知）"}
            </Link>
          )}
          {task.status === "blocked" && task.blocked_reason && (
            <span className="note-body small" data-testid="task-blocked-reason">
              受阻：{task.blocked_reason}
            </span>
          )}
          {task.description && (
            <p className="note-body small" data-testid="task-description">
              {task.description}
            </p>
          )}
        </>
      )}
      {mode === "block" && (
        <form className="stack" onSubmit={(event) => void saveBlock(event)}>
          <label>
            受阻原因
            <input
              name="blocked_reason"
              value={reason}
              maxLength={500}
              required
              autoFocus
              disabled={busy}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <div className="row">
            <button className="primary" disabled={busy || reason.trim() === ""}>
              标为受阻
            </button>
            <button type="button" disabled={busy} onClick={() => setMode("view")}>
              取消
            </button>
          </div>
        </form>
      )}
      {error && <ErrorNotice error={error} />}
      {task.editable && mode === "view" && (
        <div className="row">
          <select
            aria-label="状态"
            data-testid="task-status"
            value={task.status}
            disabled={busy}
            onChange={(event) => void changeStatus(event.target.value as TaskStatus)}
          >
            {STATUSES.map((status) => (
              <option key={status} value={status}>
                {STATUS_LABELS[status]}
              </option>
            ))}
          </select>
          <button
            disabled={busy}
            data-testid="task-edit"
            onClick={() => {
              setDraft(fieldsOf(task));
              setMode("edit");
            }}
          >
            编辑
          </button>
          {task.deletable && (
            <button className="danger" disabled={busy} data-testid="task-delete" onClick={() => void remove()}>
              删除
            </button>
          )}
        </div>
      )}
    </li>
  );
}

export function Tasks({
  projectId,
  workspaceId,
  canWrite,
  refresh = 0,
}: {
  projectId: string;
  workspaceId: string;
  canWrite: boolean;
  /** Changes when something outside (a deleted paper) may have changed the tasks. */
  refresh?: number;
}) {
  const [assignee, setAssignee] = useState("");
  const members = useResource(() => api<Member[]>(`/workspaces/${workspaceId}/members`), workspaceId);
  const papers = useResource(
    () => api<PapersPage>(`/projects/${projectId}/papers?limit=200`),
    `${projectId}:${refresh}`,
  );
  const page = useResource(
    () => api<TasksPage>(`/projects/${projectId}/tasks?limit=200${assignee ? `&assignee=${assignee}` : ""}`),
    `${projectId}:${assignee}:${refresh}`,
  );
  const [draft, setDraft] = useState<Fields>(EMPTY);
  const [error, setError] = useState<ApiFailure | null>(null);
  const [busy, setBusy] = useState(false);

  const choices: Choices = {
    members: members.data ?? [],
    papers: (papers.data?.items ?? []).map((paper) => ({
      id: paper.id,
      title: paper.title ?? "（标题未知）",
    })),
  };

  async function create(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await sendJson(`/projects/${projectId}/tasks`, "POST", {
        title: draft.title,
        description: draft.description,
        priority: draft.priority,
        assignee_id: draft.assignee_id || null,
        due_date: draft.due_date || null,
        paper_id: draft.paper_id || null,
      });
      setDraft(EMPTY);
      await page.reload();
    } catch (failure) {
      setError(failure as ApiFailure);
    } finally {
      setBusy(false);
    }
  }

  const counts = page.data?.counts;
  return (
    <section className="panel" data-testid="tasks-panel">
      <div className="row-between">
        <h2>任务</h2>
        <label className="row small">
          负责人
          <select
            data-testid="tasks-filter"
            value={assignee}
            onChange={(event) => setAssignee(event.target.value)}
          >
            <option value="">全部</option>
            <option value="none">未分配</option>
            {choices.members.map((member) => (
              <option key={member.user_id} value={member.user_id}>
                {member.display_name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {counts && counts.overdue > 0 && (
        <span className="small" data-testid="tasks-overdue-count">
          全项目有 {counts.overdue} 个逾期任务
        </span>
      )}
      {canWrite && (
        <form className="stack" data-testid="task-form" onSubmit={(event) => void create(event)}>
          <FieldInputs value={draft} onChange={setDraft} choices={choices} disabled={busy} />
          <div>
            <button className="primary" disabled={busy || draft.title.trim() === ""}>
              新建任务
            </button>
          </div>
        </form>
      )}
      {error && <ErrorNotice error={error} />}
      {page.loading && <Loading />}
      {page.error && <ErrorNotice error={page.error} onRetry={page.reload} />}
      {page.data && (
        <div className="board">
          {STATUSES.map((status) => {
            const items = page.data?.items.filter((task) => task.status === status) ?? [];
            return (
              <div className="column stack" key={status} data-testid="task-column" data-status={status}>
                <h3>
                  {STATUS_LABELS[status]}{" "}
                  <span className="muted" data-testid="task-count">
                    {counts?.[status] ?? 0}
                  </span>
                </h3>
                <ul className="list">
                  {items.map((task) => (
                    <TaskCard
                      key={task.id}
                      task={task}
                      projectId={projectId}
                      choices={choices}
                      onChanged={page.reload}
                    />
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      )}
      {page.data && page.data.total > page.data.items.length && (
        <span className="muted small">只显示最近的 {page.data.items.length} 个任务。</span>
      )}
      {page.data && page.data.counts.todo + page.data.counts.doing + page.data.counts.blocked +
        page.data.counts.done + page.data.counts.cancelled === 0 && (
        <div className="empty" data-testid="tasks-empty">
          还没有任务。
        </div>
      )}
    </section>
  );
}
