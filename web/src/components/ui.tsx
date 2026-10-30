"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { ReactNode } from "react";

import { ApiFailure, describeCode, sendJson } from "@/lib/api";
import type { Job, JobStatus } from "@/lib/types";

export function Header({ children }: { children?: ReactNode }) {
  const router = useRouter();

  async function logout() {
    await sendJson("/auth/logout", "POST").catch(() => undefined);
    router.replace("/login");
  }

  return (
    <header className="shell-header">
      <Link className="brand" href="/">
        Research<span>OS</span>
      </Link>
      {children}
      <div className="spacer" />
      <button onClick={logout}>退出</button>
    </header>
  );
}

export function ErrorNotice({ error, onRetry }: { error: ApiFailure; onRetry?: () => void }) {
  return (
    <div className="notice error" role="alert">
      <strong>{error.status === 403 ? "拒绝访问" : "操作失败"}</strong>
      <span>{error.message}</span>
      {error.requestId && <span className="mono muted">request_id: {error.requestId}</span>}
      {onRetry && (
        <div>
          <button onClick={onRetry}>重试</button>
        </div>
      )}
    </div>
  );
}

export function Loading({ label = "加载中…" }: { label?: string }) {
  return <p className="muted">{label}</p>;
}

const STATUS_LABELS: Record<JobStatus, string> = {
  queued: "排队中",
  running: "解析中",
  succeeded: "解析成功",
  failed: "解析失败",
  cancelled: "已取消",
};

export function JobBadge({ job }: { job: Job | null }) {
  if (!job) {
    return <span className="badge queued">未解析</span>;
  }
  const retrying = job.status === "queued" && job.attempts > 0;
  const label = job.cancel_requested && job.status === "running"
    ? "已请求取消"
    : retrying
      ? `等待重试（已尝试 ${job.attempts}/${job.max_attempts}）`
      : job.status === "running"
        ? `解析中（第 ${job.attempts}/${job.max_attempts} 次）`
        : STATUS_LABELS[job.status];
  return (
    <span className={`badge ${job.status}`} data-testid="job-status" data-status={job.status}>
      {label}
    </span>
  );
}

export function JobError({ job }: { job: Job | null }) {
  if (!job?.error_code) {
    return null;
  }
  return (
    <div className="notice error" data-testid="job-error" data-code={job.error_code}>
      <strong>{describeCode(job.error_code, job.error_message)}</strong>
      <span className="mono muted">{job.error_code}</span>
    </div>
  );
}

export function isActive(job: Job | null | undefined): boolean {
  return job?.status === "queued" || job?.status === "running";
}

export function formatBytes(bytes: number): string {
  return bytes < 1024 * 1024
    ? `${(bytes / 1024).toFixed(0)} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("zh-CN", { hour12: false });
}
