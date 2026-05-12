import type {
  APIResponse,
  Adapter,
  AdapterListResponse,
  AuditEntry,
  ConfigDiffResponse,
  ConfigHistoryEntry,
  ConfigSummaryResponse,
  ConfigTemplateResponse,
  ConfigValidationResult,
  Configuration,
  DeprecationInfo,
  Document,
  DocumentDetail,
  FieldMapping,
  HealthStatus,
  PaginatedResponse,
  SearchResponse,
  Simulation,
  VersionComparisonResponse,
} from "@/types";
import axios from "axios";
import { clearTokens, getRefreshToken, getToken, setTokens } from "./auth";
import type { AuthUser } from "./auth";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || "",
  timeout: 30_000,
  headers: {
    "Content-Type": "application/json",
    "X-Tenant-ID": "default",
    "X-Tenant-Role": "admin",
  },
});

// Attach Bearer token from localStorage on every request
api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

let _refreshing: Promise<string | null> | null = null;

api.interceptors.response.use(
  (response) => response,
  async (error: unknown) => {
    if (axios.isAxiosError(error)) {
      if (error.code === "ECONNABORTED") {
        console.error("[API] Request timed out");
      }

      const status = error.response?.status;
      const originalRequest = error.config;

      // On 401, try to refresh the token once
      if (status === 401 && originalRequest && !(originalRequest as { _retry?: boolean })._retry) {
        (originalRequest as { _retry?: boolean })._retry = true;

        if (!_refreshing) {
          _refreshing = (async () => {
            const refreshToken = getRefreshToken();
            if (!refreshToken) return null;
            try {
              const res = await axios.post<{ access_token: string; token_type: string }>(
                `${api.defaults.baseURL ?? ""}/api/v1/auth/refresh`,
                { refresh_token: refreshToken }
              );
              const newAccess = res.data.access_token;
              setTokens(newAccess, refreshToken);
              return newAccess;
            } catch {
              clearTokens();
              window.location.href = "/login";
              return null;
            } finally {
              _refreshing = null;
            }
          })();
        }

        const newToken = await _refreshing;
        if (newToken && originalRequest.headers) {
          originalRequest.headers.Authorization = `Bearer ${newToken}`;
          return api(originalRequest);
        }
      }

      if (status === 401) {
        clearTokens();
        window.location.href = "/login";
      }

      console.error("[API Error]", error.response?.status, error.message);
    }
    return Promise.reject(error);
  }
);

// ── Auth API ──────────────────────────────────────────────────────────────────

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: AuthUser;
}

export const authApi = {
  register: (data: { email: string; password: string; name: string }) =>
    api.post<AuthUser>("/api/v1/auth/register", data).then((r) => r.data),

  login: (data: { email: string; password: string }) =>
    api.post<LoginResponse>("/api/v1/auth/login", data).then((r) => r.data),

  refresh: (refresh_token: string) =>
    api
      .post<{ access_token: string; token_type: string }>("/api/v1/auth/refresh", { refresh_token })
      .then((r) => r.data),

  me: () => api.get<AuthUser>("/api/v1/auth/me").then((r) => r.data),
};

export const healthApi = {
  check: () => api.get<HealthStatus>("/health").then((r) => r.data),
};

export const adaptersApi = {
  list: (category?: string) =>
    api
      .get<APIResponse<AdapterListResponse>>("/api/v1/adapters/", {
        params: category ? { category } : undefined,
      })
      .then((r) => r.data),
  get: (id: string) => api.get<APIResponse<Adapter>>(`/api/v1/adapters/${id}`).then((r) => r.data),
  match: (adapterId: string, services: string) =>
    api
      .get<APIResponse<unknown>>(`/api/v1/adapters/${adapterId}/match`, { params: { services } })
      .then((r) => r.data),
  deprecation: (adapterId: string, version: string) =>
    api
      .get<APIResponse<DeprecationInfo>>(
        `/api/v1/adapters/${adapterId}/versions/${version}/deprecation`
      )
      .then((r) => r.data),
  createFromDocument: (documentId: string, name: string, category: string) =>
    api
      .post<APIResponse<Adapter>>("/api/v1/adapters/from-document", null, {
        params: { document_id: documentId, name, category },
      })
      .then((r) => r.data),
};

export const documentsApi = {
  list: () => api.get<APIResponse<Document[]>>("/api/v1/documents/").then((r) => r.data),
  get: (id: string) =>
    api.get<APIResponse<DocumentDetail>>(`/api/v1/documents/${id}`).then((r) => r.data),
  upload: (file: File) => {
    const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
    const docType = ["yaml", "yml", "json"].includes(ext) ? "api_spec" : "brd";
    const form = new FormData();
    form.append("file", file);
    return api
      .post<APIResponse<Document>>(`/api/v1/documents/upload?doc_type=${docType}`, form, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then((r) => r.data);
  },
  delete: (id: string) =>
    api
      .delete<APIResponse<{ id: string; deleted: boolean }>>(`/api/v1/documents/${id}`)
      .then((r) => r.data),
};

export const configurationsApi = {
  list: () => api.get<APIResponse<Configuration[]>>("/api/v1/configurations/").then((r) => r.data),
  get: (id: string) =>
    api.get<APIResponse<Configuration>>(`/api/v1/configurations/${id}`).then((r) => r.data),
  generate: (params: {
    document_id: string;
    adapter_version_id: string;
    name: string;
    auto_map?: boolean;
  }) =>
    api
      .post<APIResponse<Configuration>>("/api/v1/configurations/generate", params)
      .then((r) => r.data),
  validate: (id: string) =>
    api
      .post<APIResponse<ConfigValidationResult>>(`/api/v1/configurations/${id}/validate`)
      .then((r) => r.data),
  transition: (id: string, targetState: string, reason?: string) =>
    api
      .post<
        APIResponse<{
          id: string;
          previous_state: string;
          new_state: string;
          available_transitions: string[];
        }>
      >(`/api/v1/configurations/${id}/transition`, {
        target_state: targetState,
        reason,
      })
      .then((r) => r.data),
  getTemplates: () =>
    api
      .get<APIResponse<ConfigTemplateResponse[]>>("/api/v1/configurations/templates")
      .then((r) => r.data),
  export: (id: string, format: "json" | "yaml" = "json") =>
    api
      .get(`/api/v1/configurations/${id}/export?format=${format}`, { responseType: "blob" })
      .then((r) => r.data as Blob),
  history: (id: string) =>
    api
      .get<APIResponse<ConfigHistoryEntry[]>>(`/api/v1/configurations/${id}/history`)
      .then((r) => r.data),
  rollback: (id: string, targetVersion: number) =>
    api
      .post<
        APIResponse<{
          id: string;
          name: string;
          previous_version: number;
          restored_version: number;
          status: string;
        }>
      >(`/api/v1/configurations/${id}/rollback`, { target_version: targetVersion })
      .then((r) => r.data),
  getSummary: () =>
    api
      .get<APIResponse<ConfigSummaryResponse>>("/api/v1/configurations/summary")
      .then((r) => r.data),
  diff: (configAId: string, configBId: string) =>
    api
      .get<APIResponse<ConfigDiffResponse>>(`/api/v1/configurations/${configAId}/diff/${configBId}`)
      .then((r) => r.data),
  compareVersions: (configId: string, v1: number, v2: number) =>
    api
      .get<APIResponse<VersionComparisonResponse>>(
        `/api/v1/configurations/${configId}/history/compare`,
        {
          params: { v1, v2 },
        }
      )
      .then((r) => r.data),
  batchValidate: (configIds: string[]) =>
    api
      .post<APIResponse<unknown>>("/api/v1/configurations/batch-validate", {
        config_ids: configIds,
      })
      .then((r) => r.data),
  batchSimulate: (configIds: string[]) =>
    api
      .post<APIResponse<unknown>>("/api/v1/configurations/batch-simulate", {
        config_ids: configIds,
      })
      .then((r) => r.data),
  update: (id: string, data: { name?: string; field_mappings?: FieldMapping[]; notes?: string }) =>
    api.patch<APIResponse<Configuration>>(`/api/v1/configurations/${id}`, data).then((r) => r.data),
  delete: (id: string) =>
    api
      .delete<APIResponse<{ id: string; deleted: boolean }>>(`/api/v1/configurations/${id}`)
      .then((r) => r.data),
};

export const simulationsApi = {
  list: () => api.get<APIResponse<Simulation[]>>("/api/v1/simulations/").then((r) => r.data),
  run: (params: { configuration_id: string; test_type?: string }) =>
    api.post<APIResponse<Simulation>>("/api/v1/simulations/run", params).then((r) => r.data),
  get: (id: string) =>
    api.get<APIResponse<Simulation>>(`/api/v1/simulations/${id}`).then((r) => r.data),
  delete: (id: string) =>
    api
      .delete<APIResponse<{ id: string; deleted: boolean }>>(`/api/v1/simulations/${id}`)
      .then((r) => r.data),
};

export interface AuditListFilters {
  resource_type?: string;
  resource_id?: string;
  action?: string;
  page?: number;
  page_size?: number;
}

export const auditApi = {
  list: (filters?: AuditListFilters) =>
    api
      .get<APIResponse<PaginatedResponse<AuditEntry>>>("/api/v1/audit/", {
        params: filters,
      })
      .then((r) => r.data),
};

export interface DashboardAnalytics {
  // Backend chart fields
  weekly_activity?: Array<{ name: string; documents: number; simulations: number }>;
  throughput?: Array<{ hour: string; records: number }>;
  total_processed?: number;
  total_warnings?: number;
  // Backend aggregate fields
  configurations?: {
    total: number;
    by_status: Record<string, number>;
    active: number;
    draft: number;
  };
  simulations?: {
    total: number;
    passed: number;
    failed: number;
    pass_rate: number;
    avg_duration_ms: number;
  };
  documents?: { total: number; by_status: Record<string, number> };
  audit_entries?: number;
  health_score?: number;
}

export const analyticsApi = {
  dashboard: () =>
    api.get<APIResponse<DashboardAnalytics>>("/api/v1/analytics/dashboard").then((r) => r.data),
  health: () => api.get<Record<string, unknown>>("/api/v1/analytics/health").then((r) => r.data),
};

export const metricsApi = {
  get: () => api.get<Record<string, unknown>>("/metrics").then((r) => r.data),
};

export const searchApi = {
  search: (q: string) =>
    api
      .get<APIResponse<SearchResponse>>(`/api/v1/search/?q=${encodeURIComponent(q)}`)
      .then((r) => r.data),
};

interface WebhookEntry {
  id: string;
  tenant_id: string;
  url: string;
  events: string[];
  is_active: boolean;
  created_at: string;
}
interface WebhookTestResult {
  id: string;
  webhook_id: string;
  event_type: string;
  status: string;
  response_code: number | null;
  attempts: number;
  created_at: string;
}

export const webhooksApi = {
  list: () => api.get<APIResponse<WebhookEntry[]>>("/api/v1/webhooks/").then((r) => r.data),
  create: (data: { url: string; events: string[]; secret: string }) =>
    api.post<APIResponse<WebhookEntry>>("/api/v1/webhooks/", data).then((r) => r.data),
  delete: (id: string) =>
    api.delete<APIResponse<null>>(`/api/v1/webhooks/${id}`).then((r) => r.data),
  test: (id: string) =>
    api.post<APIResponse<WebhookTestResult>>(`/api/v1/webhooks/${id}/test`, {}).then((r) => r.data),
};

export default api;
