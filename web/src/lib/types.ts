export type User = { id: string; email: string; display_name: string };

export type Role = "owner" | "admin" | "member" | "viewer";

export type Workspace = { id: string; name: string; role: Role };

export type Member = { user_id: string; email: string; display_name: string; role: Role };

export type Project = {
  id: string;
  workspace_id: string;
  name: string;
  goal: string;
  background: string;
  status: "active" | "archived";
  version: number;
  created_at: string;
  updated_at: string;
};

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export type Job = {
  id: string;
  subject_id: string;
  status: JobStatus;
  stage: string | null;
  attempts: number;
  max_attempts: number;
  parser: string;
  cancel_requested: boolean;
  error_code: string | null;
  error_message: string | null;
  result: {
    parser?: string;
    parser_version?: string;
    block_count?: number;
    warnings?: string[];
  };
  created_at: string;
  finished_at: string | null;
};

export type Version = {
  id: string;
  version_no: number;
  sha256: string;
  size_bytes: number;
  original_filename: string;
  page_count: number | null;
  current_parse_job_id: string | null;
  created_at: string;
};

export type Paper = {
  id: string;
  project_id: string;
  workspace_id: string;
  title: string | null;
  authors: string[];
  year: number | null;
  doi: string | null;
  arxiv_id: string | null;
  abstract: string | null;
  venue: string | null;
  source_links: { open_access_url?: string; landing_url?: string } | null;
  metadata_provenance: Record<string, string>;
  current_version: Version | null;
  parse_job: Job | null;
  created_at: string;
};

export type PapersPage = {
  items: Paper[];
  total: number;
  offset: number;
  limit: number;
  worker_online: boolean;
};

export type Block = {
  id: string;
  seq: number;
  page_no: number;
  kind: string;
  text: string;
  bbox: [number, number, number, number] | null;
  granularity: "block" | "page";
  content_hash: string;
};

export type BlocksPage = {
  items: Block[];
  total: number;
  offset: number;
  limit: number;
  parsed: boolean;
};

export type ResearchEvent = {
  id: string;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type SourceName = "arxiv" | "openalex" | "crossref";

export type SourceStatus =
  | "ok"
  | "timeout"
  | "unavailable"
  | "rate_limited"
  | "quota_exhausted"
  | "cooling_down"
  | "throttled"
  | "bad_request"
  | "bad_response";

export type SourceReport = {
  status: SourceStatus;
  count: number;
  total: number | null;
  cached: boolean;
  fetched_at: string | null;
  duration_ms: number | null;
  message: string | null;
  retry_after?: number;
  reset_at?: string;
  until?: string;
  quota?: { remaining_usd: number | null; reset_at: string | null; shared: boolean };
};

export type SearchResult = {
  title: string | null;
  authors: string[];
  year: number | null;
  venue: string | null;
  doi: string | null;
  arxiv_id: string | null;
  abstract: string | null;
  sources: SourceName[];
  provenance: Record<string, string>;
  conflicts: Record<string, Record<string, number>>;
  missing: string[];
  citations: Record<string, number>;
  open_access_url: string | null;
  landing_url: string | null;
  in_project: string | null;
  similar_to: string[];
  similar_papers: string[];
};

export type SearchResponse = {
  outcome: "results" | "no_match" | "partial" | "all_failed";
  ranking: string;
  sources: Partial<Record<SourceName, SourceReport>>;
  results: SearchResult[];
};

export type NoteKind = "project" | "paper" | "annotation";

export type NoteAnchor = {
  version_id: string;
  version_no: number;
  page_no: number;
  text: string;
  status: "located" | "stale" | "unparsed";
  block_id: string | null;
  bbox: [number, number, number, number] | null;
  granularity: "block" | "page";
};

export type Note = {
  id: string;
  kind: NoteKind;
  paper_id: string | null;
  paper_title: string | null;
  author_id: string;
  author_name: string;
  body: string;
  version: number;
  editable: boolean;
  deletable: boolean;
  anchor: NoteAnchor | null;
  created_at: string;
  updated_at: string;
};

export type NotesPage = { items: Note[]; total: number; offset: number; limit: number };

export type TaskStatus = "todo" | "doing" | "blocked" | "done" | "cancelled";
export type TaskPriority = "low" | "normal" | "high";

export type Task = {
  id: string;
  title: string;
  description: string;
  status: TaskStatus;
  priority: TaskPriority;
  blocked_reason: string | null;
  assignee_id: string | null;
  assignee_name: string | null;
  assignee_active: boolean | null;
  due_date: string | null;
  overdue: boolean;
  paper_id: string | null;
  paper_title: string | null;
  created_by: string;
  created_by_name: string;
  completed_at: string | null;
  version: number;
  editable: boolean;
  deletable: boolean;
  created_at: string;
  updated_at: string;
};

export type TasksPage = {
  items: Task[];
  total: number;
  offset: number;
  limit: number;
  counts: Record<TaskStatus | "overdue", number>;
};

export type OverviewProject = {
  id: string;
  name: string;
  status: "active" | "archived";
  paper_count: number;
  parsed_paper_count: number;
  noted_paper_count: number;
  tasks: Record<TaskStatus | "overdue", number>;
  last_activity_at: string | null;
};

export type OverviewTask = {
  id: string;
  title: string;
  status: TaskStatus;
  priority: TaskPriority;
  due_date: string | null;
  overdue: boolean;
  project_id: string;
  project_name: string;
};

export type OverviewEvent = {
  id: string;
  event_type: string;
  project_id: string | null;
  project_name: string | null;
  actor_name: string | null;
  created_at: string;
};

export type OverviewDay = {
  date: string;
  total: number;
  papers: number;
  notes: number;
  tasks: number;
  projects: number;
  meetings: number;
};

export type Meeting = {
  id: string;
  title: string;
  starts_at: string;
  location: string | null;
  past: boolean;
  created_by: string;
  created_by_name: string;
  version: number;
  editable: boolean;
  deletable: boolean;
  created_at: string;
  updated_at: string;
};

export type MeetingsPage = { items: Meeting[]; total: number };

export type OverviewMeeting = {
  id: string;
  title: string;
  starts_at: string;
  location: string | null;
  project_id: string;
  project_name: string;
  seconds_until: number;
};

export type Overview = {
  today: string;
  totals: {
    active_projects: number;
    archived_projects: number;
    papers: number;
    open_tasks: number;
    overdue_tasks: number;
    parsed_papers: number;
    noted_papers: number;
  };
  projects: OverviewProject[];
  my_tasks: { total: number; items: OverviewTask[] };
  recent_events: OverviewEvent[];
  activity: OverviewDay[];
  next_meeting: OverviewMeeting | null;
};

export type Evidence = {
  block_id: string;
  paper_id: string;
  paper_title: string | null;
  version_id: string;
  seq: number;
  page_no: number;
  text: string;
};

export type AnswerSegment = { text: string; supported: boolean; evidence: Evidence[] };

export type Answer = {
  answerable: boolean;
  segments: AnswerSegment[];
  unavailable: { paper_id: string; paper_title: string | null; reason: string }[];
  dropped_citations: number;
  model: string;
  usage: { input_tokens: number | null; output_tokens: number | null };
};
