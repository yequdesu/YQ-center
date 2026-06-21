import type { ApiClientConfig } from "./types";

let _config: ApiClientConfig | null = null;

export function configureClient(config: ApiClientConfig): void {
  _config = config;
}

export function getConfig(): ApiClientConfig {
  if (!_config) {
    throw new Error("API client not configured. Call configureClient() first.");
  }
  return _config;
}

export function clearConfig(): void {
  _config = null;
}

class ApiError extends Error {
  status: number;
  detail: Record<string, unknown> | null;

  constructor(status: number, message: string, detail?: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail ?? null;
  }
}

async function request<T>(
  method: string,
  path: string,
  options?: {
    body?: unknown;
    params?: Record<string, string | number | undefined>;
  },
): Promise<T> {
  const config = getConfig();
  const url = new URL(`${config.baseUrl}${path}`);

  if (options?.params) {
    for (const [key, value] of Object.entries(options.params)) {
      if (value !== undefined) {
        url.searchParams.set(key, String(value));
      }
    }
  }

  const headers: Record<string, string> = {
    Authorization: `Bearer ${config.token}`,
  };
  if (options?.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(url.toString(), {
    method,
    headers,
    body: options?.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  if (response.status === 401) {
    config.onUnauthorized();
    throw new ApiError(401, "未授权 — Token 无效或已过期");
  }

  if (response.status === 403) {
    throw new ApiError(403, "权限不足 — Token scope 不匹配");
  }

  if (!response.ok) {
    let detail: Record<string, unknown> | undefined;
    try {
      detail = await response.json();
    } catch {
      // response may not be JSON
    }
    const message =
      typeof detail?.detail === "string"
        ? detail.detail
        : detail?.detail
          ? JSON.stringify(detail.detail)
          : `请求失败: ${response.status}`;
    throw new ApiError(response.status, message, detail);
  }

  // Handle 204 No Content
  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

export const api = {
  get<T>(path: string, params?: Record<string, string | number | undefined>) {
    return request<T>("GET", path, { params });
  },
  post<T>(path: string, body?: unknown) {
    return request<T>("POST", path, { body });
  },
  put<T>(path: string, body?: unknown) {
    return request<T>("PUT", path, { body });
  },
  patch<T>(path: string, body?: unknown) {
    return request<T>("PATCH", path, { body });
  },
  delete<T>(path: string) {
    return request<T>("DELETE", path);
  },
};

export { ApiError };
