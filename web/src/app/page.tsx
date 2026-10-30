"use client";

import Link from "next/link";
import { type FormEvent, useState } from "react";

import { ErrorNotice, formatTime, Header, Loading } from "@/components/ui";
import { api, ApiFailure, sendJson } from "@/lib/api";
import type { Project, Workspace } from "@/lib/types";
import { useResource } from "@/lib/useResource";

function Projects({ workspace }: { workspace: Workspace }) {
  const projects = useResource(
    () => api<Project[]>(`/workspaces/${workspace.id}/projects`),
    workspace.id,
  );
  const [error, setError] = useState<ApiFailure | null>(null);
  const canWrite = workspace.role !== "viewer";

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const target = event.currentTarget;
    const form = new FormData(target);
    setError(null);
    try {
      await sendJson(`/workspaces/${workspace.id}/projects`, "POST", {
        name: form.get("name"),
        goal: form.get("goal"),
      });
      target.reset();
      await projects.reload();
    } catch (failure) {
      setError(failure as ApiFailure);
    }
  }

  return (
    <>
      {canWrite && (
        <section className="panel">
          <h2>新建研究项目</h2>
          <form className="stack" onSubmit={create}>
            <label>
              项目名称
              <input name="name" required maxLength={200} />
            </label>
            <label>
              研究目标（可选）
              <textarea name="goal" maxLength={20000} />
            </label>
            {error && <ErrorNotice error={error} />}
            <div>
              <button className="primary">创建项目</button>
            </div>
          </form>
        </section>
      )}
      <section className="panel">
        <h2>项目</h2>
        {projects.loading && <Loading />}
        {projects.error && <ErrorNotice error={projects.error} onRetry={projects.reload} />}
        {projects.data?.length === 0 && (
          <div className="empty">
            {canWrite ? "还没有项目，先创建一个吧。" : "这个工作区还没有项目。"}
          </div>
        )}
        <ul className="list">
          {projects.data?.map((project) => (
            <li className="list-item" key={project.id}>
              <div className="row-between">
                <Link href={`/projects/${project.id}`}>{project.name}</Link>
                {project.status === "archived" && <span className="badge cancelled">已归档</span>}
              </div>
              {project.goal && <span className="muted">{project.goal}</span>}
              <span className="muted small">更新于 {formatTime(project.updated_at)}</span>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}

export default function HomePage() {
  const workspaces = useResource(() => api<Workspace[]>("/workspaces"), "workspaces");
  const [selected, setSelected] = useState<string | null>(null);
  const workspace =
    workspaces.data?.find((item) => item.id === selected) ?? workspaces.data?.[0] ?? null;

  return (
    <>
      <Header>
        {workspaces.data && workspaces.data.length > 1 && (
          <select
            style={{ width: 220 }}
            value={workspace?.id}
            onChange={(event) => setSelected(event.target.value)}
          >
            {workspaces.data.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        )}
      </Header>
      <main className="page">
        {workspaces.loading && <Loading />}
        {workspaces.error && <ErrorNotice error={workspaces.error} onRetry={workspaces.reload} />}
        {workspace && (
          <>
            <h1>{workspace.name}</h1>
            <Projects workspace={workspace} key={workspace.id} />
          </>
        )}
      </main>
    </>
  );
}
