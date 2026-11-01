import Link from "next/link";

import { STATUS_LABELS } from "@/components/Tasks";
import { formatTime } from "@/components/ui";
import { EVENT_LABELS } from "@/lib/events";
import type {
  Overview as OverviewData,
  OverviewProject,
  TaskStatus,
} from "@/lib/types";

const STATUSES: TaskStatus[] = [
  "todo",
  "doing",
  "blocked",
  "done",
  "cancelled",
];

// Every number here is computed by the server; this file only lays them out.
export function Totals({ data }: { data: OverviewData }) {
  const { totals } = data;
  const cells: [string, string, number][] = [
    ["projects", "活跃项目", totals.active_projects],
    ["papers", "文献", totals.papers],
    ["open", "未完成任务", totals.open_tasks],
    ["overdue", "逾期任务", totals.overdue_tasks],
  ];
  return (
    <div className="totals" data-testid="overview-totals">
      {cells.map(([key, label, value]) => (
        <div
          className="total"
          key={key}
          data-testid="overview-total"
          data-key={key}
        >
          <span className="total-value">{value}</span>
          <span className="muted small">{label}</span>
        </div>
      ))}
      {totals.archived_projects > 0 && (
        <span className="muted small" data-testid="overview-archived">
          另有 {totals.archived_projects} 个已归档项目（不计入任务数）
        </span>
      )}
    </div>
  );
}

export function ProjectCounts({
  project,
}: {
  project: OverviewProject | undefined;
}) {
  if (!project) return null;
  return (
    <span className="muted small" data-testid="project-counts">
      文献 {project.paper_count}（已解析 {project.parsed_paper_count}）
      {STATUSES.map(
        (status) => ` · ${STATUS_LABELS[status]} ${project.tasks[status]}`,
      )}
      {` · 逾期 ${project.tasks.overdue}`}
      {project.last_activity_at
        ? ` · 最近活动 ${formatTime(project.last_activity_at)}`
        : " · 暂无活动"}
    </span>
  );
}

export function MyTasks({ data }: { data: OverviewData }) {
  const { total, items } = data.my_tasks;
  return (
    <section className="panel" data-testid="my-tasks">
      <h2>我的待办</h2>
      {items.length === 0 ? (
        <div className="empty" data-testid="my-tasks-empty">
          没有分给你的未完成任务
        </div>
      ) : (
        <ul className="list">
          {items.map((task) => (
            <li className="list-item" key={task.id} data-testid="my-task">
              <div className="row-between">
                <Link
                  href={`/projects/${task.project_id}`}
                  data-testid="my-task-title"
                >
                  {task.title}
                </Link>
                {task.overdue && (
                  <span className="badge failed" data-testid="my-task-overdue">
                    已逾期
                  </span>
                )}
              </div>
              <span className="muted small">
                {task.project_name} · {STATUS_LABELS[task.status]}
                {task.due_date ? ` · 期限 ${task.due_date}` : " · 无期限"}
              </span>
            </li>
          ))}
        </ul>
      )}
      {total > items.length && (
        <p className="muted small" data-testid="my-tasks-more">
          共 {total} 个，只显示前 {items.length} 个
        </p>
      )}
    </section>
  );
}

export function RecentEvents({ data }: { data: OverviewData }) {
  return (
    <section className="panel" data-testid="recent-events">
      <h2>最近动态</h2>
      {data.recent_events.length === 0 ? (
        <div className="empty" data-testid="events-empty">
          还没有动态
        </div>
      ) : (
        <ul className="list">
          {data.recent_events.map((event) => (
            <li className="small" key={event.id} data-testid="overview-event">
              {event.actor_name ?? "未知用户"} 在{" "}
              {event.project_name ?? "已删除的项目"}：
              {EVENT_LABELS[event.event_type] ?? event.event_type}{" "}
              <span className="muted">{formatTime(event.created_at)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function Basis({ today }: { today: string }) {
  return (
    <details className="panel" data-testid="overview-basis">
      <summary>统计口径</summary>
      <ul className="small">
        <li>
          逾期：期限早于服务器的 UTC 日期（今天是 {today}
          ），且状态是待办、进行中或受阻的任务。
          完成、取消和没有期限的任务不算逾期。
        </li>
        <li>
          已解析：文献的当前版本有可读的解析结果。仅元数据、解析失败或还在解析的不算。
        </li>
        <li>文献数是导入的条数，不代表读过。</li>
        <li>
          未完成任务数和逾期任务数只含活跃项目；已归档项目的任务只在它自己的那一行里显示。
        </li>
        <li>
          我的待办：分给你、未完成、所属项目活跃的任务；逾期的在前，其次按期限，最多
          20 条。
        </li>
        <li>最近动态只显示谁在哪个项目做了什么类型的操作，不含标题和正文。</li>
        <li>
          这里只有计数，没有进度百分比：系统里没有可以据以计算进度的定义。
        </li>
      </ul>
    </details>
  );
}
