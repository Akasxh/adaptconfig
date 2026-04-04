// APIResponse is the standard backend wrapper for all endpoints
export interface APIResponse<T> {
  success: boolean;
  data: T | null;
  message: string;
  errors: string[];
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  has_next: boolean;
}

export interface AdapterEndpoint {
  path: string;
  method: string;
  description: string;
  request_fields: Record<string, unknown>[];
  response_fields: Record<string, unknown>[];
}

export interface AdapterVersion {
  id: string;
  version: string;
  status: string;
  auth_type: string;
  base_url?: string;
  endpoints: AdapterEndpoint[];
  changelog?: string;
}

// Matches AdapterResponse Pydantic schema
export interface Adapter {
  id: string;
  name: string;
  category: string;
  description?: string;
  is_active: boolean;
  icon?: string;
  versions: AdapterVersion[];
  created_at: string;
  // legacy optional fields kept for backward compat
  type?: string;
  status?: "active" | "inactive" | "error";
  version?: string;
  last_sync?: string;
}

export interface AdapterListResponse {
  adapters: Adapter[];
  total: number;
  categories: string[];
}

// Matches DocumentUploadResponse Pydantic schema
export interface Document {
  id: string;
  filename: string;
  file_type: string;
  doc_type: string;
  status: string;
  created_at: string;
  // legacy optional fields kept for backward compat
  content_type?: string;
  size?: number;
  file_size?: number;
  uploaded_at?: string;
  processed_at?: string;
  parsed_at?: string;
}

export interface DocumentDetail extends Document {
  parsed_result?: Record<string, unknown> | null;
  updated_at: string;
}

export interface FieldMapping {
  source_field: string;
  target_field: string;
  transformation?: string;
  confidence: number;
  is_confirmed: boolean;
}

// Matches ConfigurationResponse Pydantic schema
export interface Configuration {
  id: string;
  name: string;
  adapter_version_id: string;
  document_id?: string;
  status: string;
  version: number;
  field_mappings: FieldMapping[];
  transformation_rules?: Record<string, unknown>[];
  hooks?: Record<string, unknown>[];
  created_at: string;
  updated_at: string;
  // legacy optional fields kept for backward compat
  adapter_type?: string;
  parameters?: Record<string, unknown>;
}

export interface SimulationStepResult {
  step_name: string;
  status: string;
  request_payload: Record<string, unknown>;
  expected_response: Record<string, unknown>;
  actual_response: Record<string, unknown>;
  duration_ms: number;
  confidence_score: number;
  error_message?: string;
  assertions: Record<string, unknown>[];
}

// Matches SimulationResponse Pydantic schema
export interface Simulation {
  id: string;
  configuration_id: string;
  status: string;
  test_type: string;
  total_tests: number;
  passed_tests: number;
  failed_tests: number;
  duration_ms?: number;
  steps?: SimulationStepResult[];
  created_at: string;
  // legacy optional fields kept for backward compat
  name?: string;
  scenario?: string;
  config_id?: string;
  queued_at?: string;
  completed_at?: string;
  results?: SimulationResults;
}

export interface SimulationResults {
  success_rate: number;
  total_records: number;
  processed_records: number;
  errors: number;
  warnings: number;
  duration_ms: number;
}

// Matches AuditLogResponse Pydantic schema
export interface AuditEntry {
  id: string;
  tenant_id: string;
  actor: string;
  action: string;
  resource_type: string;
  resource_id: string;
  details?: Record<string, unknown>;
  created_at: string;
  // legacy optional fields kept for backward compat
  entity_type?: string;
  entity_id?: string;
  user?: string;
  actor_email?: string;
  timestamp?: string;
  status?: "success" | "failure" | "warning";
  outcome?: string;
}

export interface ConfigValidationResult {
  is_valid: boolean;
  errors: string[];
  warnings: string[];
  coverage_score: number;
  missing_required_fields: string[];
  unmapped_source_fields: string[];
}

export interface ConfigTemplateResponse {
  name: string;
  description: string;
  adapter_category: string;
  default_config: Record<string, unknown>;
}

export interface ConfigHistoryEntry {
  id: string;
  configuration_id: string;
  version: number;
  change_type: string;
  changed_by: string;
  previous_value?: Record<string, unknown> | null;
  new_value?: Record<string, unknown> | null;
  created_at: string;
}

export interface HealthStatus {
  status: string;
  version?: string;
  env?: string;
  timestamp?: string;
  checks?: Record<string, unknown>;
}

export interface ConfigSummaryResponse {
  total: number;
  by_status: Record<string, number>;
  by_adapter: Record<string, number>;
  avg_confidence: number;
}

export interface ConfigDiffItem {
  path: string;
  change_type: "added" | "removed" | "modified";
  old_value: unknown;
  new_value: unknown;
  is_breaking: boolean;
}

export interface ConfigDiffResponse {
  config_a_id: string;
  config_b_id: string;
  total_changes: number;
  breaking_changes: number;
  diffs: ConfigDiffItem[];
}

export interface VersionComparisonResponse {
  configuration_id: string;
  version_a: number;
  version_b: number;
  total_changes: number;
  breaking_changes: number;
  diffs: ConfigDiffItem[];
}

export interface DeprecationInfo {
  version: string;
  status: "active" | "deprecated" | "sunset";
  sunset_date: string | null;
  days_until_sunset: number | null;
  replacement_version: string | null;
  migration_guide: Array<{ action: string; description: string }>;
}

export interface SearchResult {
  type: string;
  id: string;
  name: string;
  score: number;
  details: Record<string, unknown>;
}

export interface SearchResponse {
  query: string;
  total: number;
  adapters: SearchResult[];
  configurations: SearchResult[];
  simulations: SearchResult[];
}
