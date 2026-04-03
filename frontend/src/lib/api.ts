import type {
  APIResponse,
  Adapter,
  AdapterListResponse,
  AuditEntry,
  ConfigTemplateResponse,
  ConfigValidationResult,
  Configuration,
  Document,
  HealthStatus,
  PaginatedResponse,
  Simulation,
} from "@/types";
import axios from "axios";

const api = axios.create({
  baseURL: "",
  headers: {
    "Content-Type": "application/json",
    "X-Tenant-ID": "default",
  },
});

api.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    if (axios.isAxiosError(error)) {
      console.error("[API Error]", error.response?.status, error.message);
    }
    return Promise.reject(error);
  }
);

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
};

export const documentsApi = {
  list: () => api.get<APIResponse<Document[]>>("/api/v1/documents/").then((r) => r.data),
  get: (id: string) =>
    api.get<APIResponse<Document>>(`/api/v1/documents/${id}`).then((r) => r.data),
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
    api.delete<APIResponse<{ message: string }>>(`/api/v1/documents/${id}`).then((r) => r.data),
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
      .post<APIResponse<Configuration>>(`/api/v1/configurations/${id}/transition`, {
        target_state: targetState,
        reason,
      })
      .then((r) => r.data),
  getTemplates: () =>
    api
      .get<APIResponse<ConfigTemplateResponse[]>>("/api/v1/configurations/templates")
      .then((r) => r.data),
};

export const simulationsApi = {
  list: () => api.get<APIResponse<Simulation[]>>("/api/v1/simulations/").then((r) => r.data),
  run: (params: { name?: string; configuration_id: string; test_type?: string }) =>
    api.post<APIResponse<Simulation>>("/api/v1/simulations/run", params).then((r) => r.data),
  get: (id: string) =>
    api.get<APIResponse<Simulation>>(`/api/v1/simulations/${id}`).then((r) => r.data),
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

export default api;
