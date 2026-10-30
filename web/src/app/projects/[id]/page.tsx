"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { type ChangeEvent, type FormEvent, useState } from "react";

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
  Member,
  Paper,
  PapersPage,
  Project,
  ResearchEvent,
  Role,
  Workspace,
} from "@/lib/types";
import { useResource } from "@/lib/useResource";

const ROLE_LABELS: Record<Role, string> = {
  owner: "所有者",
  admin: "管理员",
  member: "成员",
  viewer: "只读",
};

const EVENT_LABELS: Record<string, string> = {
  "project.created": "创建项目",
  "project.updated": "修改项目",
  "paper.imported": "导入文献",
  "paper.parse_queued": "解析排队",
  "paper.parsed": "解析成功",
  "paper.parse_failed": "解析失败",
  "paper.parse_cancelled": "解析已取消",
  "paper.metadata_updated": "修改文献信息",
  "paper.deleted": "删除文献",
};

type UploadResult = { name: string; error?: ApiFailure };

function PaperRow({
  paper,
  projectId,
  canWrite,
  onChanged,
}: {
  paper: Paper;
  projectId: string;
  canWrite: boolean;
  onChanged: () => Promise<void>;
}) {
  const [error, setError] = useState<ApiFailure | null>(null);
  const job = paper.parse_job;
  const version = paper.current_version;

  async function run(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      await onChanged();
    } catch (failure) {
      setError(failure as ApiFailure);
    }
  }

  return (
    <li className="list-item" data-testid="paper-row">
      <div className="row-between">
        <Link href={`/projects/${projectId}/papers/${paper.id}`}>
          {paper.title ?? <span>（标题未知）</span>}
        </Link>
        <JobBadge job={job} />
      </div>
      {paper.authors.length > 0 && <span className="muted">{paper.authors.join("，")}</span>}
      {version && (
        <span className="muted small">
          {version.original_filename} · {formatBytes(version.size_bytes)}
          {version.page_count !== null && ` · ${version.page_count} 页`} · v{version.version_no}
        </span>
      )}
      <JobError job={job} />
      {canWrite && (
        <div className="row">
          {job && isActive(job) && !job.cancel_requested && (
            <button
              onClick={() =>
                run(() => sendJson(`/projects/${projectId}/jobs/${job.id}/cancel`, "POST"))
              }
            >
              取消解析
            </button>
          )}
          {version && !isActive(job) && (
            <button
              onClick={() =>
                run(() =>
                  sendJson(
                    `/projects/${projectId}/papers/${paper.id}/versions/${version.id}/parse`,
                    "POST",
                  ),
                )
              }
            >
              重新解析
            </button>
          )}
          {!isActive(job) && (
            <button
              className="danger"
              onClick={() => {
                if (window.confirm("删除这篇文献及其全部版本？此操作不可恢复。")) {
                  void run(() => sendJson(`/projects/${projectId}/papers/${paper.id}`, "DELETE"));
                }
              }}
            >
              删除
            </button>
          )}
        </div>
      )}
      {error && <ErrorNotice error={error} />}
    </li>
  );
}

function Papers({ project, canWrite }: { project: Project; canWrite: boolean }) {
  const papers = useResource(
    () => api<PapersPage>(`/projects/${project.id}/papers?limit=200`),
    project.id,
    (page) => page.items.some((paper) => isActive(paper.parse_job)),
  );
  const [uploading, setUploading] = useState<string | null>(null);
  const [results, setResults] = useState<UploadResult[]>([]);
  const writable = canWrite && project.status === "active";
  const waiting = papers.data?.items.some((paper) => paper.parse_job?.status === "queued");

  async function choose(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    const outcome: UploadResult[] = [];
    // The API takes one file per request (D-011), so a batch is a sequence of uploads.
    for (const file of files) {
      setUploading(file.name);
      try {
        await uploadPdf(`/projects/${project.id}/papers`, file);
        outcome.push({ name: file.name });
      } catch (failure) {
        outcome.push({ name: file.name, error: failure as ApiFailure });
      }
      setResults([...outcome]);
      await papers.reload();
    }
    setUploading(null);
  }

  return (
    <section className="panel">
      <div className="row-between">
        <h2>文献{papers.data ? `（${papers.data.total}）` : ""}</h2>
        {writable && (
          <label style={{ width: "auto" }}>
            <input
              data-testid="upload-input"
              type="file"
              accept="application/pdf"
              multiple
              onChange={choose}
              disabled={uploading !== null}
            />
          </label>
        )}
      </div>
      {uploading && <p className="muted">正在上传 {uploading}…</p>}
      {results
        .filter((result) => result.error)
        .map((result) => (
          <div className="stack" key={result.name} data-testid="upload-error">
            <span className="small muted">{result.name}</span>
            <ErrorNotice error={result.error!} />
          </div>
        ))}
      {waiting && papers.data?.worker_online === false && (
        <div className="notice warn" data-testid="worker-offline">
          <strong>有任务在排队，但没有 Worker 在运行</strong>
          <span className="muted">启动 Worker 后任务会继续，排队中的文献不会丢失。</span>
        </div>
      )}
      {papers.loading && <Loading />}
      {papers.error && <ErrorNotice error={papers.error} onRetry={papers.reload} />}
      {papers.data?.items.length === 0 && (
        <div className="empty">
          {writable ? "还没有文献，上传第一篇 PDF 吧。" : "这个项目还没有文献。"}
        </div>
      )}
      <ul className="list">
        {papers.data?.items.map((paper) => (
          <PaperRow
            key={paper.id}
            paper={paper}
            projectId={project.id}
            canWrite={writable}
            onChanged={papers.reload}
          />
        ))}
      </ul>
    </section>
  );
}

function ProjectForm({
  project,
  onSaved,
}: {
  project: Project;
  onSaved: () => Promise<void>;
}) {
  const [error, setError] = useState<ApiFailure | null>(null);
  const [saved, setSaved] = useState(false);

  async function save(body: Record<string, unknown>) {
    setError(null);
    setSaved(false);
    try {
      await sendJson(`/projects/${project.id}`, "PATCH", {
        expected_version: project.version,
        ...body,
      });
      setSaved(true);
      await onSaved();
    } catch (failure) {
      setError(failure as ApiFailure);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    void save({
      name: form.get("name"),
      goal: form.get("goal"),
      background: form.get("background"),
    });
  }

  const archived = project.status === "archived";
  return (
    <form className="stack" onSubmit={submit} key={project.version}>
      <label>
        项目名称
        <input name="name" defaultValue={project.name} required disabled={archived} />
      </label>
      <label>
        研究目标
        <textarea name="goal" defaultValue={project.goal} disabled={archived} />
      </label>
      <label>
        研究背景
        <textarea name="background" defaultValue={project.background} disabled={archived} />
      </label>
      {error && <ErrorNotice error={error} />}
      {saved && <span className="muted small">已保存</span>}
      <div className="row">
        {!archived && <button className="primary">保存</button>}
        <button
          type="button"
          onClick={() => void save({ status: archived ? "active" : "archived" })}
        >
          {archived ? "恢复项目" : "归档项目"}
        </button>
      </div>
    </form>
  );
}

function Members({ workspace }: { workspace: Workspace }) {
  const members = useResource(
    () => api<Member[]>(`/workspaces/${workspace.id}/members`),
    workspace.id,
  );
  const [error, setError] = useState<ApiFailure | null>(null);
  const canManage = workspace.role === "owner" || workspace.role === "admin";

  async function run(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      await members.reload();
    } catch (failure) {
      setError(failure as ApiFailure);
    }
  }

  function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const target = event.currentTarget;
    const form = new FormData(target);
    void run(async () => {
      await sendJson(`/workspaces/${workspace.id}/members`, "POST", {
        email: form.get("email"),
        role: form.get("role"),
      });
      target.reset();
    });
  }

  return (
    <section className="panel">
      <h2>工作区成员</h2>
      <p className="muted small">权限在工作区一级：成员可见此工作区的全部项目。</p>
      {members.loading && <Loading />}
      {members.error && <ErrorNotice error={members.error} onRetry={members.reload} />}
      <ul className="list">
        {members.data?.map((member) => (
          <li className="list-item" key={member.user_id}>
            <span>{member.display_name}</span>
            <span className="muted small">{member.email}</span>
            {canManage ? (
              <div className="row">
                <select
                  style={{ width: 110 }}
                  value={member.role}
                  onChange={(event) =>
                    void run(() =>
                      sendJson(
                        `/workspaces/${workspace.id}/members/${member.user_id}`,
                        "PATCH",
                        { role: event.target.value },
                      ),
                    )
                  }
                >
                  {Object.entries(ROLE_LABELS).map(([role, label]) => (
                    <option key={role} value={role}>
                      {label}
                    </option>
                  ))}
                </select>
                <button
                  className="danger"
                  onClick={() =>
                    void run(() =>
                      sendJson(
                        `/workspaces/${workspace.id}/members/${member.user_id}`,
                        "DELETE",
                      ),
                    )
                  }
                >
                  移除
                </button>
              </div>
            ) : (
              <span className="muted small">{ROLE_LABELS[member.role]}</span>
            )}
          </li>
        ))}
      </ul>
      {error && <ErrorNotice error={error} />}
      {canManage && (
        <form className="stack" onSubmit={add}>
          <label>
            添加已注册用户（邮箱）
            <input name="email" type="email" required />
          </label>
          <label>
            角色
            <select name="role" defaultValue="member">
              {Object.entries(ROLE_LABELS).map(([role, label]) => (
                <option key={role} value={role}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <div>
            <button>添加成员</button>
          </div>
        </form>
      )}
    </section>
  );
}

function Activity({ project }: { project: Project }) {
  const events = useResource(
    () => api<ResearchEvent[]>(`/projects/${project.id}/events?limit=20`),
    project.id,
  );
  return (
    <section className="panel">
      <div className="row-between">
        <h2>活动</h2>
        <button onClick={() => void events.reload()}>刷新</button>
      </div>
      {events.loading && <Loading />}
      {events.error && <ErrorNotice error={events.error} onRetry={events.reload} />}
      {events.data?.length === 0 && <div className="empty">还没有活动。</div>}
      <ul className="list">
        {events.data?.map((event) => (
          <li key={event.id} className="small">
            <span>{EVENT_LABELS[event.event_type] ?? event.event_type}</span>{" "}
            <span className="muted">{formatTime(event.created_at)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const project = useResource(() => api<Project>(`/projects/${id}`), id);
  const workspaces = useResource(() => api<Workspace[]>("/workspaces"), "workspaces");
  const workspace = workspaces.data?.find((item) => item.id === project.data?.workspace_id);
  const canWrite = workspace !== undefined && workspace.role !== "viewer";

  return (
    <>
      <Header />
      <main className="page">
        {project.loading && <Loading />}
        {project.error?.status === 404 ? (
          <div className="empty" data-testid="not-found">
            项目不存在，或你无权访问。
          </div>
        ) : (
          project.error && <ErrorNotice error={project.error} onRetry={project.reload} />
        )}
        {project.data && (
          <>
            <div className="row">
              <h1>{project.data.name}</h1>
              {project.data.status === "archived" && (
                <span className="badge cancelled">已归档，只读</span>
              )}
            </div>
            <div className="grid-2">
              <div className="stack">
                <Papers project={project.data} canWrite={canWrite} />
                <Activity project={project.data} />
              </div>
              <div className="stack">
                <section className="panel">
                  <h2>项目信息</h2>
                  {canWrite ? (
                    <ProjectForm project={project.data} onSaved={project.reload} />
                  ) : (
                    <>
                      <p className="muted">{project.data.goal || "未填写研究目标"}</p>
                      <p className="muted">{project.data.background || "未填写研究背景"}</p>
                    </>
                  )}
                </section>
                {workspace && <Members workspace={workspace} />}
              </div>
            </div>
          </>
        )}
      </main>
    </>
  );
}
