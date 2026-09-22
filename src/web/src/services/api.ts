import type { JsonMap } from "@/types";

const SESSION_KEY = "purslyx-web-session-v1";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public action?: string,
    public requestId?: string,
  ) {
    super(requestId ? `${message}（请求 ID：${requestId}）` : message);
  }
}

export class TaskExecutionError extends Error {
  constructor(
    message: string,
    public task: JsonMap,
  ) {
    super(message);
    this.name = "TaskExecutionError";
  }
}

export interface TaskWaitOptions {
  timeoutMs?: number;
  signal?: AbortSignal;
  onUpdate?: (task: JsonMap) => void;
  pollIntervalMs?: number;
}

export function idempotencyKey(scope: string): string {
  return `${scope}-${crypto.randomUUID()}`;
}

function readSession(): JsonMap {
  try {
    return JSON.parse(localStorage.getItem(SESSION_KEY) || "{}");
  } catch {
    return {};
  }
}

function readCookie(name: string): string {
  if (typeof document === "undefined") return "";
  const prefix = `${encodeURIComponent(name)}=`;
  const item = document.cookie.split(";").map((value) => value.trim()).find((value) => value.startsWith(prefix));
  if (!item) return "";
  try {
    return decodeURIComponent(item.slice(prefix.length));
  } catch {
    return "";
  }
}

export async function api<T = JsonMap>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    formData?: FormData;
    idempotencyKey?: string;
    headers?: Record<string, string>;
    raw?: boolean;
    redirect?: RequestRedirect;
  } = {},
): Promise<T> {
  const session = readSession();
  const headers: Record<string, string> = { Accept: "application/json", ...(options.headers || {}) };
  if (session.token) headers.Authorization = `Bearer ${session.token}`;
  const csrf = String(session.csrf || readCookie("purslyx_csrf"));
  if (csrf && !["GET", "HEAD", "OPTIONS"].includes(options.method || "GET")) {
    headers["X-CSRF-Token"] = csrf;
  }
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method: options.method || "GET",
    credentials: "same-origin",
    redirect: options.redirect,
    headers,
    body: options.formData || (options.body !== undefined ? JSON.stringify(options.body) : undefined),
  });
  if (options.raw) return response as T;
  if (response.status === 204) return undefined as T;
  let payload: JsonMap = {};
  try {
    payload = await response.json();
  } catch {
    if (!response.ok) throw new ApiError(response.status, "INVALID_RESPONSE", `服务返回了无法读取的响应（HTTP ${response.status}）`);
  }
  if (!response.ok) {
    const error = payload.error || {};
    const rawRequestId = String(error.request_id || payload.meta?.request_id || response.headers.get("X-Request-ID") || "");
    const requestId = /^[A-Za-z0-9._:-]{1,64}$/.test(rawRequestId) ? rawRequestId : undefined;
    throw new ApiError(response.status, error.code || "REQUEST_FAILED", error.message || "请求失败", error.action, requestId);
  }
  return (payload.data ?? payload) as T;
}

export async function deleteWithImpact(
  path: string,
  prompt: (impact: JsonMap) => string,
): Promise<boolean> {
  const impact = await api<JsonMap>(`${path}/deletion-impact`);
  if (!window.confirm(prompt(impact))) return false;
  await api(path, {
    method: "DELETE",
    headers: { "If-Match": `"${impact.impact_version}"` },
  });
  return true;
}

function wait(delayMs: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("任务等待已取消", "AbortError"));
      return;
    }
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(new DOMException("任务等待已取消", "AbortError"));
    };
    const timer = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, delayMs);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/**
 * 等待服务端异步任务完成。页面只轮询任务摘要，成功后再按结果引用读取业务资源，
 * 因而在 inline 与独立 Worker 两种部署方式下使用同一套交互。
 */
export async function waitForTask(initialTask: JsonMap, options: TaskWaitOptions = {}): Promise<JsonMap> {
  if (!initialTask?.id) throw new TaskExecutionError("服务没有返回可跟踪的任务", initialTask || {});
  const timeoutMs = options.timeoutMs ?? 10 * 60 * 1000;
  const startedAt = Date.now();
  let task = initialTask;

  while (["queued", "running", "retry_wait"].includes(String(task.status))) {
    options.onUpdate?.(task);
    if (Date.now() - startedAt >= timeoutMs) {
      throw new TaskExecutionError("任务仍在后台执行，可稍后从发起页面或任务记录继续查看", task);
    }
    const serverDelay = Number(task.poll_after_ms || 2000);
    const delayMs = options.pollIntervalMs ?? Math.min(10_000, Math.max(250, serverDelay));
    await wait(delayMs, options.signal);
    task = await api<JsonMap>(`/api/v1/tasks/${encodeURIComponent(task.id)}`);
  }

  options.onUpdate?.(task);
  if (task.status === "succeeded") return task;
  const failure = task.failure || {};
  const required = Array.isArray(task.required_actions)
    ? task.required_actions.map((item: JsonMap) => item.message || item.action).filter(Boolean).join("；")
    : "";
  throw new TaskExecutionError(
    failure.message || required || (task.status === "cancelled" ? "任务已取消" : "任务未能完成"),
    task,
  );
}

export { SESSION_KEY };
