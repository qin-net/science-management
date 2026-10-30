/** Every failure reaches the UI as an ApiFailure carrying the server's code and request ID. */

const MESSAGES: Record<string, string> = {
  not_authenticated: "请先登录",
  session_expired: "登录已过期，请重新登录",
  invalid_credentials: "邮箱或密码不正确",
  email_taken: "该邮箱已注册",
  registration_closed: "此服务器已关闭注册",
  forbidden: "你的角色无权执行此操作",
  not_found: "内容不存在，或你无权访问",
  csrf_rejected: "请求来源未被允许，请检查 ALLOWED_ORIGINS",
  version_conflict: "内容已被他人修改，请刷新后再试",
  project_archived: "项目已归档，只读",
  file_too_large: "文件超过上传上限",
  not_a_pdf: "这不是有效的 PDF 文件",
  duplicate_file: "项目里已有相同文件",
  duplicate_doi: "项目里已有相同 DOI 的文献",
  invalid_doi: "DOI 格式不正确，应形如 10.1234/abc",
  job_active: "有解析任务正在进行，请先取消",
  job_finished: "任务已结束",
  parser_unavailable: "此解析器在服务器上不可用",
  user_not_found: "没有使用此邮箱的注册用户",
  already_member: "此用户已是成员",
  last_owner: "工作区至少要保留一位所有者",
  file_missing: "存储的文件已丢失，请上传新版本",
  database_unavailable: "数据库不可用，请稍后重试",
  database_not_configured: "服务器未配置数据库",
  validation_error: "输入内容不符合要求",
  backend_unreachable: "无法连接后端服务",
  pdf_unreadable: "PDF 已损坏或被加密，无法读取",
  no_extractable_text: "未找到文本层，疑似扫描件，需要 OCR 解析器",
  parse_timeout: "解析超时",
  parser_crashed: "解析进程异常退出",
  parser_empty_output: "解析器没有产出有效内容",
  parser_bad_output: "解析器产出的数据不合法",
  worker_lost: "Worker 在解析中途停止",
  worker_error: "Worker 内部错误",
};

export function describeCode(code: string | null, fallback?: string | null): string {
  return (code && MESSAGES[code]) || fallback || code || "未知错误";
}

export class ApiFailure extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
    readonly requestId: string | null = null,
  ) {
    super(describeCode(code, message));
  }
}

async function failureFrom(response: Response): Promise<ApiFailure> {
  const body = await response.json().catch(() => null);
  const error = body?.error;
  if (!error) {
    // The Next proxy answers with plain text when the API process is down.
    return new ApiFailure(response.status, "backend_unreachable", `HTTP ${response.status}`);
  }
  return new ApiFailure(
    response.status,
    error.code,
    error.message,
    error.details ?? {},
    error.request_id ?? null,
  );
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, init);
  } catch {
    throw new ApiFailure(0, "backend_unreachable", "network error");
  }
  if (!response.ok) {
    throw await failureFrom(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json();
}

export function sendJson<T>(path: string, method: string, body?: unknown): Promise<T> {
  return api<T>(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export function uploadPdf<T>(path: string, file: File): Promise<T> {
  const query = new URLSearchParams({ filename: file.name });
  return api<T>(`${path}?${query}`, {
    method: "POST",
    headers: { "Content-Type": "application/pdf" },
    body: file,
  });
}
