import type { ApiClientConfig } from "./types";

let config: ApiClientConfig | null = null;

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail ?? null;
  }
}

export function configureClient(nextConfig: ApiClientConfig): void {
  config = nextConfig;
}

export function clearConfig(): void {
  config = null;
}

export function getConfig(): ApiClientConfig {
  if (!config) {
    throw new Error("API client is not configured");
  }
  return config;
}

async function request<T>(
  method: string,
  path: string,
  options?: {
    body?: unknown;
    params?: Record<string, string | number | boolean | undefined>;
  },
): Promise<T> {
  const cfg = getConfig();
  const url = new URL(`${cfg.baseUrl}${path}`);

  for (const [key, value] of Object.entries(options?.params ?? {})) {
    if (value !== undefined) {
      url.searchParams.set(key, String(value));
    }
  }

  const headers: Record<string, string> = {
    Authorization: `Bearer ${cfg.token}`,
  };
  if (options?.body !== undefined) {
    headers["Content-Type"] = "application/json; charset=utf-8";
  }

  const response = await fetch(url.toString(), {
    method,
    headers,
    body: options?.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  if (response.status === 401) {
    cfg.onUnauthorized();
    throw new ApiError(401, "Unauthorized. Token is missing or invalid.");
  }
  if (response.status === 403) {
    throw new ApiError(403, "Forbidden. Token scope does not allow this API.");
  }
  if (!response.ok) {
    let detail: unknown;
    try {
      detail = await response.json();
    } catch {
      detail = await response.text().catch(() => undefined);
    }
    const message =
      typeof detail === "object" &&
      detail !== null &&
      "detail" in detail &&
      typeof (detail as { detail?: unknown }).detail === "string"
        ? String((detail as { detail: string }).detail)
        : `Request failed: ${response.status}`;
    throw new ApiError(response.status, message, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export const api = {
  get<T>(path: string, params?: Record<string, string | number | boolean | undefined>) {
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
