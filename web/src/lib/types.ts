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
