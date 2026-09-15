import type { JsonMap } from "@/types";

const SESSION_KEY = "purslyx-web-session-v1";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public action?: string,
  ) {
    super(message);
  }
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
  if (session.csrf && !["GET", "HEAD", "OPTIONS"].includes(options.method || "GET")) {
    headers["X-CSRF-Token"] = session.csrf;
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
    if (!response.ok) throw new ApiError(response.status, "INVALID_RESPONSE", "服务返回了无法读取的响应");
  }
  if (!response.ok) {
    const error = payload.error || {};
    throw new ApiError(response.status, error.code || "REQUEST_FAILED", error.message || "请求失败", error.action);
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

export { SESSION_KEY };
