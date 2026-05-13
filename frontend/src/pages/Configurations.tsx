import { useToast } from "@/components/Toast";
import { adaptersApi, configurationsApi, documentsApi, simulationsApi } from "@/lib/api";
import type {
  Adapter,
  AdapterEndpoint,
  ConfigDiffItem,
  ConfigDiffResponse,
  ConfigHistoryEntry,
  ConfigTemplateResponse,
  ConfigValidationResult,
  Configuration,
  FieldMapping,
} from "@/types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowDown,
  BarChart3,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Download,
  GitCompare,
  History,
  Link2,
  Loader2,
  PlayCircle,
  Plus,
  Rocket,
  RotateCcw,
  Save,
  Settings,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";

// ── Adapter categories for "create from document" form ───────────────────────
const ADAPTER_CATEGORIES = [
  "custom", "bureau", "kyc", "gst", "payment", "fraud", "notification", "open_banking",
] as const;

// ── Match score helper (client-side, mirrors backend logic) ──────────────────
function computeAdapterMatchScore(
  parsedResult: Record<string, unknown>,
  adapters: Adapter[]
): number {
  const endpoints = (parsedResult.endpoints as Array<{ path: string }> | undefined) ?? [];
  const docEndpoints = new Set(endpoints.map((e) => e.path));

  let best = 0;
  for (const adapter of adapters) {
    for (const av of adapter.versions) {
      const avEndpoints = new Set(av.endpoints.map((e) => e.path));
      const endpointUnion = new Set([...docEndpoints, ...avEndpoints]);
      const endpointIntersect = [...docEndpoints].filter((p) => avEndpoints.has(p)).length;
      const endpointScore = endpointUnion.size > 0 ? endpointIntersect / endpointUnion.size : 0;

      // field overlap not easily computed client-side (request_schema not exposed), use 0
      const score = endpointScore * 0.6;
      if (score > best) best = score;
    }
  }
  return best;
}

// ── Constants ────────────────────────────────────────────────────────────────

const STATUS_STEPS = ["draft", "configured", "validating", "testing", "active"] as const;
type StatusStep = (typeof STATUS_STEPS)[number];

const STATUS_CONFIG: Record<string, { label: string; cls: string }> = {
  draft: { label: "Draft", cls: "badge-yellow" },
  configured: { label: "Configured", cls: "badge-blue" },
  validating: { label: "Validating", cls: "badge-blue" },
  testing: { label: "Testing", cls: "badge-blue" },
  active: { label: "Active", cls: "badge-green" },
  deprecated: { label: "Deprecated", cls: "badge-gray" },
};

const TRANSITION_BUTTONS: Record<
  string,
  { label: string; icon: React.ElementType; targetState: string }[]
> = {
  draft: [{ label: "Mark Configured", icon: CheckCircle2, targetState: "configured" }],
  configured: [{ label: "Validate & Run Tests", icon: PlayCircle, targetState: "__pipeline__" }],
  validating: [{ label: "Run Tests", icon: PlayCircle, targetState: "__pipeline__" }],
  testing: [{ label: "Deploy", icon: Rocket, targetState: "active" }],
};

const TRANSFORM_OPTIONS = [
  "none", "upper", "lower", "parse_number", "parse_date",
  "normalize_phone", "validate_email", "to_string", "format_date", "parse_boolean",
];

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtDate(s: string) {
  return new Date(s).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function fmtDateTime(s: string) {
  return new Date(s).toLocaleString("en-US", {
    month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SkeletonRow() {
  return (
    <div className="card animate-pulse overflow-hidden">
      <div style={{ display: "flex", alignItems: "center", gap: 16, padding: "16px 24px" }}>
        <div style={{ width: 36, height: 36, borderRadius: 8, background: "var(--color-bg-raised)" }} />
        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ height: 14, width: 192, borderRadius: 4, background: "var(--color-border-strong)" }} />
          <div style={{ height: 11, width: 128, borderRadius: 4, background: "var(--color-border)" }} />
        </div>
      </div>
    </div>
  );
}

function ConfidenceBar({ value, hasTarget }: { value: number; hasTarget?: boolean }) {
  const pct = Math.round(value * 100);
  // If no target field mapped, show "No match" instead of a 0% bar
  if (!hasTarget && pct === 0) {
    return (
      <span style={{ fontSize: 11, fontStyle: "italic", color: "var(--color-text-muted)" }}>
        No match
      </span>
    );
  }
  const barColor = pct >= 70 ? "var(--color-success)" : pct >= 50 ? "var(--color-warning)" : "var(--color-error)";
  const textColor = pct >= 70 ? "var(--color-success-text)" : pct >= 50 ? "var(--color-warning-text)" : "var(--color-error-text)";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div style={{ flex: 1, height: 5, borderRadius: 9999, background: "var(--color-border-strong)", overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", borderRadius: 9999, background: barColor, transition: "width 300ms ease" }} />
      </div>
      <span style={{ fontSize: 11, fontVariantNumeric: "tabular-nums", fontWeight: 500, color: textColor, minWidth: 28, textAlign: "right" }}>
        {pct}%
      </span>
    </div>
  );
}

function StatusStepper({ status }: { status: string }) {
  const currentIdx = STATUS_STEPS.indexOf(status as StatusStep);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 0 }}>
      {STATUS_STEPS.map((step, idx) => {
        const isPast = idx < currentIdx;
        const isCurrent = idx === currentIdx;
        const dotColor = isPast ? "var(--color-teal)" : isCurrent ? "var(--color-brand-light)" : "var(--color-border-strong)";
        const labelColor = isPast ? "var(--color-teal)" : isCurrent ? "var(--color-brand-light)" : "var(--color-text-muted)";
        const lineColor = isPast ? "var(--color-teal)" : "var(--color-border)";
        return (
          <div key={step} style={{ display: "flex", alignItems: "center" }}>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
              <div style={{
                width: 10, height: 10, borderRadius: "50%", background: dotColor,
                boxShadow: isCurrent ? `0 0 0 3px rgba(45, 143, 206, 0.2)` : "none",
                transition: "all 150ms ease",
              }} />
              <span style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", color: labelColor, whiteSpace: "nowrap" }}>
                {step}
              </span>
            </div>
            {idx < STATUS_STEPS.length - 1 && (
              <div style={{ width: 32, height: 1, background: lineColor, margin: "0 4px", marginBottom: 16, transition: "background 150ms ease" }} />
            )}
          </div>
        );
      })}
    </div>
  );
}

function ValidationPanel({ configId }: { configId: string }) {
  const [result, setResult] = useState<ConfigValidationResult | null>(null);
  const [ran, setRan] = useState(false);

  const validateMutation = useMutation({
    mutationFn: () => configurationsApi.validate(configId),
    onSuccess: (resp) => {
      if (resp.data) { setResult(resp.data); setRan(true); }
    },
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button
          type="button"
          className="btn-secondary"
          style={{ fontSize: 12, padding: "6px 12px" }}
          onClick={() => validateMutation.mutate()}
          disabled={validateMutation.isPending}
        >
          <BarChart3 style={{ width: 13, height: 13 }} />
          {validateMutation.isPending ? "Validating..." : ran ? "Re-validate" : "Run Validation"}
        </button>
        {ran && result && (
          <span style={{ fontSize: 12, fontWeight: 600, color: result.is_valid ? "var(--color-teal)" : "var(--color-error-text)" }}>
            {result.is_valid ? "Valid" : "Invalid"}
          </span>
        )}
      </div>

      {ran && result && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontSize: 11, color: "var(--color-text-muted)", minWidth: 60 }}>Coverage</span>
            <div style={{ flex: 1, maxWidth: 200 }}>
              <ConfidenceBar value={result.coverage_score} />
            </div>
          </div>

          {result.errors.length > 0 && (
            <div style={{ borderRadius: 6, border: "1px solid rgba(220,38,38,0.2)", background: "rgba(220,38,38,0.05)", padding: 12 }}>
              <p style={{ fontSize: 11, fontWeight: 600, color: "var(--color-error-text)", marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.05em" }}>
                Errors ({result.errors.length})
              </p>
              {result.errors.map((e) => (
                <div key={e} style={{ display: "flex", alignItems: "flex-start", gap: 6, marginBottom: 4 }}>
                  <AlertCircle style={{ width: 12, height: 12, color: "var(--color-error-text)", marginTop: 1, flexShrink: 0 }} />
                  <span style={{ fontSize: 12, color: "var(--color-error-text)" }}>{e}</span>
                </div>
              ))}
            </div>
          )}

          {result.warnings.length > 0 && (
            <div style={{ borderRadius: 6, border: "1px solid rgba(217,119,6,0.2)", background: "rgba(217,119,6,0.05)", padding: 12 }}>
              <p style={{ fontSize: 11, fontWeight: 600, color: "var(--color-warning-text)", marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.05em" }}>
                Warnings ({result.warnings.length})
              </p>
              {result.warnings.map((w) => (
                <p key={w} style={{ fontSize: 12, color: "var(--color-warning-text)", marginBottom: 2 }}>{w}</p>
              ))}
            </div>
          )}

          {result.missing_required_fields.length > 0 && (
            <p style={{ fontSize: 11, color: "var(--color-text-muted)" }}>
              Missing required: <span style={{ fontFamily: "monospace", color: "var(--color-text-secondary)" }}>{result.missing_required_fields.join(", ")}</span>
            </p>
          )}
        </div>
      )}

      {validateMutation.isError && (
        <p style={{ fontSize: 12, color: "var(--color-error-text)" }}>Validation request failed. Check connection.</p>
      )}
    </div>
  );
}

function HistoryPanel({ configId, currentVersion }: { configId: string; currentVersion: number }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const { data, isLoading, isError } = useQuery({
    queryKey: ["config-history", configId],
    queryFn: () => configurationsApi.history(configId),
  });

  const rollbackMutation = useMutation({
    mutationFn: (v: number) => configurationsApi.rollback(configId, v),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["configurations"] });
      queryClient.invalidateQueries({ queryKey: ["config-history", configId] });
      toast("Rolled back successfully.", "success");
    },
    onError: () => { toast("Rollback failed.", "error"); },
  });

  const handleRollback = (entry: ConfigHistoryEntry) => {
    if (window.confirm(`Roll back to version ${entry.version}? This creates a new version from that snapshot.`)) {
      rollbackMutation.mutate(entry.version);
    }
  };

  if (isLoading) return <p style={{ fontSize: 12, color: "var(--color-text-muted)", padding: "8px 0" }}>Loading history...</p>;
  if (isError) return <p style={{ fontSize: 12, color: "var(--color-error-text)", padding: "8px 0" }}>Failed to load history.</p>;

  const entries: ConfigHistoryEntry[] = data?.data ?? [];
  if (entries.length === 0) return <p style={{ fontSize: 12, color: "var(--color-text-muted)", padding: "8px 0" }}>No history available.</p>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {entries.map((entry) => (
        <div
          key={entry.version}
          style={{
            display: "flex", alignItems: "center", gap: 12,
            borderRadius: 6, border: "1px solid var(--color-border)",
            background: "var(--color-bg-base)", padding: "8px 12px",
          }}
        >
          <div style={{
            width: 28, height: 28, borderRadius: "50%", flexShrink: 0,
            background: "var(--color-brand-subtle)", display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 10, fontWeight: 700, color: "var(--color-brand-light)",
          }}>
            v{entry.version}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <p style={{ fontSize: 12, fontWeight: 500, color: "var(--color-text-primary)", textTransform: "capitalize" }}>
              {entry.change_type.replace(/_/g, " ")}
            </p>
            <p style={{ fontSize: 11, color: "var(--color-text-muted)" }}>
              {entry.changed_by} · {fmtDateTime(entry.created_at)}
            </p>
          </div>
          {entry.version === currentVersion ? (
            <span style={{ fontSize: 11, fontWeight: 600, color: "var(--color-teal)" }}>current</span>
          ) : (
            <button
              type="button"
              className="btn-secondary"
              style={{ fontSize: 11, padding: "4px 8px" }}
              disabled={rollbackMutation.isPending}
              onClick={() => handleRollback(entry)}
            >
              <RotateCcw style={{ width: 11, height: 11 }} />
              Rollback
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

function MappingsTable({ cfg }: { cfg: Configuration }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [mappings, setMappings] = useState<FieldMapping[]>(() => cfg.field_mappings.map((fm) => ({ ...fm })));
  const [isDirty, setIsDirty] = useState(false);

  useEffect(() => {
    setMappings(cfg.field_mappings.map((fm) => ({ ...fm })));
    setIsDirty(false);
  }, [cfg.id, cfg.version]);

  const saveMutation = useMutation({
    mutationFn: (fms: FieldMapping[]) => configurationsApi.update(cfg.id, { field_mappings: fms }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["configurations"] });
      setIsDirty(false);
      toast("Mappings saved.", "success");
    },
    onError: () => { toast("Failed to save mappings.", "error"); },
  });

  const updateTarget = (idx: number, value: string) => {
    setMappings((prev) => { const next = [...prev]; next[idx] = { ...next[idx], target_field: value }; return next; });
    setIsDirty(true);
  };

  const updateTransform = (idx: number, value: string) => {
    setMappings((prev) => { const next = [...prev]; next[idx] = { ...next[idx], transformation: value === "none" ? undefined : value }; return next; });
    setIsDirty(true);
  };

  if (mappings.length === 0) {
    return <p style={{ fontSize: 12, color: "var(--color-text-muted)" }}>No field mappings configured.</p>;
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
        <span style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--color-text-muted)" }}>
          {mappings.length} Mappings
        </span>
        {isDirty && (
          <button
            type="button"
            className="btn-primary"
            style={{ fontSize: 11, padding: "4px 10px" }}
            onClick={() => saveMutation.mutate(mappings)}
            disabled={saveMutation.isPending}
          >
            <Save style={{ width: 11, height: 11 }} />
            {saveMutation.isPending ? "Saving..." : "Save"}
          </button>
        )}
      </div>
      <div style={{ overflowX: "auto", borderRadius: 6, border: "1px solid var(--color-border)" }}>
        <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--color-border)", background: "var(--color-bg-base)" }}>
              {["Source", "→", "Target", "Confidence", "Transform"].map((h) => (
                <th key={h} style={{ padding: "8px 12px", textAlign: h === "→" ? "center" : "left", fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--color-text-muted)" }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {mappings.map((fm, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: field mappings have no stable id
              <tr key={i} style={{ borderBottom: "1px solid var(--color-border)" }}>
                <td style={{ padding: "8px 12px" }}>
                  <span className="mono" style={{ color: "var(--color-text-primary)", fontSize: 12 }}>{fm.source_field}</span>
                </td>
                <td style={{ padding: "8px 12px", textAlign: "center", color: "var(--color-text-muted)" }}>
                  <ChevronRight style={{ width: 12, height: 12, margin: "0 auto" }} />
                </td>
                <td style={{ padding: "8px 12px", minWidth: 160 }}>
                  <input
                    type="text"
                    value={fm.target_field}
                    placeholder="target field..."
                    onChange={(e) => updateTarget(i, e.target.value)}
                    style={{
                      width: "100%", borderRadius: 4, border: "1px solid var(--color-border-strong)",
                      background: "var(--color-bg-raised)", padding: "4px 8px",
                      fontFamily: "monospace", fontSize: 12, color: "var(--color-text-primary)",
                      outline: "none", boxSizing: "border-box",
                    }}
                    onFocus={(e) => { e.currentTarget.style.borderColor = "var(--color-brand-light)"; }}
                    onBlur={(e) => { e.currentTarget.style.borderColor = "var(--color-border-strong)"; }}
                  />
                </td>
                <td style={{ padding: "8px 12px", minWidth: 120 }}>
                  <ConfidenceBar value={fm.confidence} hasTarget={!!fm.target_field} />
                </td>
                <td style={{ padding: "8px 12px" }}>
                  <select
                    value={fm.transformation ?? "none"}
                    onChange={(e) => updateTransform(i, e.target.value)}
                    style={{
                      borderRadius: 4, border: "1px solid var(--color-border-strong)",
                      background: "var(--color-bg-raised)", padding: "4px 8px",
                      fontSize: 12, color: "var(--color-text-primary)", outline: "none",
                    }}
                    onFocus={(e) => { e.currentTarget.style.borderColor = "var(--color-brand-light)"; }}
                    onBlur={(e) => { e.currentTarget.style.borderColor = "var(--color-border-strong)"; }}
                  >
                    {TRANSFORM_OPTIONS.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

type DetailTab = "mappings" | "chain" | "history" | "validation";

function ConfigDetail({ cfg }: { cfg: Configuration }) {
  const [activeTab, setActiveTab] = useState<DetailTab>("mappings");
  const { toast } = useToast();

  const { data: historyData } = useQuery({
    queryKey: ["config-history", cfg.id],
    queryFn: () => configurationsApi.history(cfg.id),
  });
  const historyEntries: ConfigHistoryEntry[] = historyData?.data ?? [];
  const hasRollbackTargets = historyEntries.some((e) => e.version !== cfg.version);

  const handleExport = async (format: "json" | "yaml") => {
    try {
      const blob = await configurationsApi.export(cfg.id, format);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${cfg.name.replace(/\s+/g, "_")}_v${cfg.version}.${format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      toast("Export failed.", "error");
    }
  };

  const chainEndpoints = (cfg.endpoints ?? []).filter((ep) => !!ep.id);
  const hasChain = chainEndpoints.length >= 2 && chainEndpoints.some((ep) => !!ep.depends_on);

  const tabs: { id: DetailTab; label: string; icon: React.ElementType }[] = [
    { id: "mappings", label: "Mappings", icon: Settings },
    ...(hasChain ? [{ id: "chain" as DetailTab, label: "Chain", icon: Link2 }] : []),
    { id: "history", label: "History", icon: History },
    { id: "validation", label: "Validation", icon: BarChart3 },
  ];

  return (
    <div className="animate-fade-in" style={{ borderTop: "1px solid var(--color-border)", background: "var(--color-bg-base)", padding: "20px 24px", display: "flex", flexDirection: "column", gap: 20 }}>

      {/* Status pipeline */}
      <div>
        <p style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--color-text-muted)", marginBottom: 12 }}>
          Lifecycle
        </p>
        <StatusStepper status={cfg.status} />
      </div>

      {/* Export */}
      <div>
        <p style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--color-text-muted)", marginBottom: 8 }}>
          Export
        </p>
        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" className="btn-secondary" style={{ fontSize: 12, padding: "5px 12px" }} onClick={() => handleExport("json")}>
            <Download style={{ width: 13, height: 13 }} />
            JSON
          </button>
          <button type="button" className="btn-secondary" style={{ fontSize: 12, padding: "5px 12px" }} onClick={() => handleExport("yaml")}>
            <Download style={{ width: 13, height: 13 }} />
            YAML
          </button>
        </div>
      </div>

      {/* Version history banner */}
      {hasRollbackTargets && (
        <div style={{
          display: "flex", alignItems: "center", gap: 8,
          padding: "8px 12px", borderRadius: 6,
          background: "rgba(56,229,205,0.06)", border: "1px solid rgba(56,229,205,0.15)",
        }}>
          <RotateCcw style={{ width: 14, height: 14, color: "var(--color-teal)" }} />
          <span style={{ fontSize: 12, color: "var(--color-text-secondary)", flex: 1 }}>
            {historyEntries.length} version(s) in history — rollback available
          </span>
          <button
            type="button"
            className="btn-secondary"
            style={{ fontSize: 11, padding: "3px 8px" }}
            onClick={() => setActiveTab("history")}
          >
            View History
          </button>
        </div>
      )}

      {/* Tabs */}
      <div>
        <div style={{ display: "flex", borderBottom: "1px solid var(--color-border)", marginBottom: 16, gap: 0 }}>
          {tabs.map(({ id, label, icon: Icon }) => {
            const isActive = activeTab === id;
            return (
              <button
                key={id}
                type="button"
                onClick={() => setActiveTab(id)}
                style={{
                  display: "inline-flex", alignItems: "center", gap: 6,
                  padding: "8px 14px", fontSize: 12, fontWeight: 500,
                  borderBottom: isActive ? "2px solid var(--color-brand-light)" : "2px solid transparent",
                  marginBottom: -1, color: isActive ? "var(--color-brand-light)" : "var(--color-text-muted)",
                  background: "transparent", cursor: "pointer", transition: "color 120ms ease",
                }}
              >
                <Icon style={{ width: 12, height: 12 }} />
                {label}
              </button>
            );
          })}
        </div>

        {activeTab === "mappings" && <MappingsTable key={cfg.id} cfg={cfg} />}
        {activeTab === "chain" && <ChainFlowPanel endpoints={cfg.endpoints ?? []} />}
        {activeTab === "history" && <HistoryPanel configId={cfg.id} currentVersion={cfg.version} />}
        {activeTab === "validation" && <ValidationPanel configId={cfg.id} />}
      </div>

      {/* Meta */}
      <p style={{ fontSize: 11, color: "var(--color-text-muted)" }}>
        Adapter version: <code style={{ fontFamily: "monospace", color: "var(--color-text-secondary)" }}>{cfg.adapter_version_id}</code>
        {" · "}Created {fmtDate(cfg.created_at)}
      </p>
    </div>
  );
}

// ── Chain Flow Panel (issue #109 MVP slice) ──────────────────────────────────

/**
 * Topological sort of chain endpoints by `depends_on`.  Mirrors the backend's
 * Kahn's-algorithm implementation so the UI shows the same execution order
 * the simulator will produce.  Returns the endpoints in original order with
 * a `hasCycle` flag if a cycle is detected -- the panel renders a warning
 * in that case rather than re-ordering arbitrarily.
 */
function sortChainEndpoints(
  endpoints: AdapterEndpoint[],
): { sorted: AdapterEndpoint[]; hasCycle: boolean } {
  const chained = endpoints.filter((ep) => !!ep.id);
  const byId = new Map<string, AdapterEndpoint>();
  for (const ep of chained) {
    if (ep.id) byId.set(ep.id, ep);
  }

  const inDegree = new Map<string, number>();
  const dependents = new Map<string, string[]>();
  for (const id of byId.keys()) {
    inDegree.set(id, 0);
    dependents.set(id, []);
  }

  for (const ep of chained) {
    if (!ep.id || !ep.depends_on) continue;
    const deps = Array.isArray(ep.depends_on) ? ep.depends_on : [ep.depends_on];
    for (const dep of deps) {
      if (!byId.has(dep)) continue;  // unknown dep -- skip, surfaces elsewhere
      dependents.get(dep)?.push(ep.id);
      inDegree.set(ep.id, (inDegree.get(ep.id) ?? 0) + 1);
    }
  }

  const queue: string[] = [];
  for (const [id, deg] of inDegree) {
    if (deg === 0) queue.push(id);
  }

  const ordered: string[] = [];
  while (queue.length > 0) {
    const current = queue.shift()!;
    ordered.push(current);
    for (const next of dependents.get(current) ?? []) {
      const nextDeg = (inDegree.get(next) ?? 0) - 1;
      inDegree.set(next, nextDeg);
      if (nextDeg === 0) queue.push(next);
    }
  }

  if (ordered.length !== byId.size) {
    return { sorted: chained, hasCycle: true };
  }

  const sorted: AdapterEndpoint[] = [];
  for (const id of ordered) {
    const ep = byId.get(id);
    if (ep) sorted.push(ep);
  }
  return { sorted, hasCycle: false };
}

/**
 * Renders the chain as a vertical `[A] -> [B] -> [C]` flow with the
 * extract/inject pairs shown between cards (issue #109 MVP slice).
 *
 * No-chain configs get a neutral placeholder so the tab is still
 * informative when toggled on an adapter that doesn't use chaining.
 */
function ChainFlowPanel({ endpoints }: { endpoints: AdapterEndpoint[] }) {
  const { sorted, hasCycle } = sortChainEndpoints(endpoints);

  if (sorted.length === 0) {
    return (
      <div
        style={{
          padding: "24px 16px",
          textAlign: "center",
          borderRadius: 8,
          border: "1px dashed var(--color-border)",
          background: "var(--color-bg-base)",
        }}
      >
        <p style={{ fontSize: 13, color: "var(--color-text-muted)", lineHeight: 1.5 }}>
          This configuration doesn't use API chaining.  Endpoints run independently.
        </p>
      </div>
    );
  }

  // Build a map of extracted keys per upstream step so the arrow between
  // two cards can label exactly which fields flow forward.
  const extractsBySource = new Map<string, string[]>();
  for (const ep of sorted) {
    if (ep.id && ep.extract) {
      extractsBySource.set(ep.id, Object.keys(ep.extract));
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
      {hasCycle && (
        <div
          style={{
            padding: "8px 12px",
            marginBottom: 12,
            borderRadius: 6,
            background: "rgba(217,119,6,0.08)",
            border: "1px solid rgba(217,119,6,0.2)",
            fontSize: 12,
            color: "var(--color-warning-text, #d97706)",
          }}
        >
          Cycle detected in dependency graph.  The simulator will reject this
          chain with HTTP 400 until the cycle is broken.
        </div>
      )}

      {sorted.map((ep, idx) => {
        const deps = ep.depends_on
          ? Array.isArray(ep.depends_on)
            ? ep.depends_on
            : [ep.depends_on]
          : [];
        const injectEntries = ep.inject ? Object.entries(ep.inject) : [];
        const extractEntries = ep.extract ? Object.entries(ep.extract) : [];

        // Collect extract keys from upstream dependencies so we can label
        // the arrow connecting the previous card to this one.
        const upstreamExtractKeys: string[] = [];
        for (const depId of deps) {
          const keys = extractsBySource.get(depId);
          if (keys) upstreamExtractKeys.push(...keys);
        }

        return (
          <div key={ep.id ?? idx}>
            {/* Arrow connector from previous endpoint */}
            {idx > 0 && (
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  padding: "6px 0",
                }}
              >
                {upstreamExtractKeys.length > 0 && (
                  <div
                    style={{
                      padding: "3px 10px",
                      borderRadius: 4,
                      background: "rgba(56,229,205,0.08)",
                      border: "1px solid rgba(56,229,205,0.18)",
                      fontSize: 10,
                      color: "var(--color-brand-light)",
                      whiteSpace: "nowrap",
                      marginBottom: 2,
                      fontFamily: "monospace",
                    }}
                  >
                    extracts: {upstreamExtractKeys.join(", ")}
                  </div>
                )}
                <ArrowDown
                  style={{
                    width: 14,
                    height: 14,
                    color: "var(--color-brand-light)",
                  }}
                />
              </div>
            )}

            {/* Endpoint card */}
            <div
              style={{
                borderRadius: 8,
                border: "1px solid var(--color-border)",
                background: "var(--color-bg-raised)",
                padding: "12px 16px",
                display: "flex",
                flexDirection: "column",
                gap: 6,
              }}
            >
              {/* Method + path */}
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    textTransform: "uppercase",
                    padding: "2px 6px",
                    borderRadius: 4,
                    background: "rgba(56,229,205,0.1)",
                    color: "var(--color-brand-light)",
                    fontFamily: "monospace",
                    letterSpacing: "0.04em",
                  }}
                >
                  {ep.method}
                </span>
                <span
                  style={{
                    fontSize: 13,
                    fontWeight: 500,
                    fontFamily: "monospace",
                    color: "var(--color-text-primary)",
                  }}
                >
                  {ep.path}
                </span>
              </div>

              {/* ID label */}
              <div style={{ fontSize: 11, color: "var(--color-text-muted)" }}>
                id:{" "}
                <code
                  style={{
                    fontFamily: "monospace",
                    color: "var(--color-text-secondary)",
                  }}
                >
                  {ep.id}
                </code>
              </div>

              {/* depends_on */}
              {deps.length > 0 && (
                <div style={{ fontSize: 11, color: "var(--color-text-muted)" }}>
                  depends on:{" "}
                  {deps.map((d, di) => (
                    <span key={d}>
                      {di > 0 && ", "}
                      <code
                        style={{
                          fontFamily: "monospace",
                          color: "var(--color-brand-light)",
                        }}
                      >
                        {d}
                      </code>
                    </span>
                  ))}
                </div>
              )}

              {/* Extract rules (what this step contributes downstream) */}
              {extractEntries.length > 0 && (
                <div
                  style={{
                    marginTop: 4,
                    padding: "6px 10px",
                    borderRadius: 6,
                    background: "rgba(56,229,205,0.04)",
                    border: "1px solid rgba(56,229,205,0.1)",
                    display: "flex",
                    flexDirection: "column",
                    gap: 3,
                  }}
                >
                  <span
                    style={{
                      fontSize: 10,
                      fontWeight: 600,
                      textTransform: "uppercase",
                      letterSpacing: "0.05em",
                      color: "var(--color-brand-light)",
                    }}
                  >
                    Extracts
                  </span>
                  {extractEntries.map(([key, path]) => (
                    <div
                      key={key}
                      style={{ fontSize: 11, fontFamily: "monospace" }}
                    >
                      <span style={{ color: "var(--color-text-primary)" }}>
                        {key}
                      </span>
                      <span
                        style={{
                          color: "var(--color-text-muted)",
                          margin: "0 4px",
                        }}
                      >
                        &larr;
                      </span>
                      <span style={{ color: "var(--color-text-secondary)" }}>
                        {path}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {/* Inject rules (what this step pulls from upstream) */}
              {injectEntries.length > 0 && (
                <div
                  style={{
                    marginTop: 4,
                    padding: "6px 10px",
                    borderRadius: 6,
                    background: "rgba(56,229,205,0.04)",
                    border: "1px solid rgba(56,229,205,0.1)",
                    display: "flex",
                    flexDirection: "column",
                    gap: 3,
                  }}
                >
                  <span
                    style={{
                      fontSize: 10,
                      fontWeight: 600,
                      textTransform: "uppercase",
                      letterSpacing: "0.05em",
                      color: "var(--color-brand-light)",
                    }}
                  >
                    Injects
                  </span>
                  {injectEntries.map(([target, template]) => (
                    <div
                      key={target}
                      style={{ fontSize: 11, fontFamily: "monospace" }}
                    >
                      <span style={{ color: "var(--color-text-secondary)" }}>
                        {target}
                      </span>
                      <span
                        style={{
                          color: "var(--color-text-muted)",
                          margin: "0 4px",
                        }}
                      >
                        &larr;
                      </span>
                      <span style={{ color: "var(--color-brand-light)" }}>
                        {template}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Templates Section ─────────────────────────────────────────────────────────

function TemplatesSection({ onSelect }: { onSelect: (name: string) => void }) {
  const [open, setOpen] = useState(false);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["config-templates"],
    queryFn: () => configurationsApi.getTemplates(),
    enabled: open,
  });

  const templates: ConfigTemplateResponse[] = data?.data ?? [];

  const CATEGORY_BADGE: Record<string, string> = {
    banking: "badge-blue",
    payments: "badge-green",
    credit: "badge-yellow",
    insurance: "badge-blue",
    investments: "badge-teal",
  };

  return (
    <div style={{ marginBottom: 4 }}>
      <button
        type="button"
        className="btn-secondary"
        style={{ fontSize: 12, padding: "5px 12px", marginBottom: open ? 14 : 0 }}
        onClick={() => setOpen((v) => !v)}
      >
        <Sparkles style={{ width: 13, height: 13 }} />
        {open ? "Hide Templates" : "Browse Templates"}
        <ChevronDown style={{
          width: 12, height: 12, marginLeft: 2,
          transform: open ? "rotate(180deg)" : "rotate(0deg)", transition: "transform 150ms ease",
        }} />
      </button>

      {open && (
        <div className="animate-fade-in">
          {isLoading && (
            <p style={{ fontSize: 12, color: "var(--color-text-muted)", padding: "8px 0" }}>Loading templates...</p>
          )}
          {isError && (
            <p style={{ fontSize: 12, color: "var(--color-error-text)", padding: "8px 0" }}>Failed to load templates.</p>
          )}
          {!isLoading && !isError && templates.length === 0 && (
            <p style={{ fontSize: 12, color: "var(--color-text-muted)", padding: "8px 0" }}>No templates available.</p>
          )}
          {templates.length > 0 && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 10 }}>
              {templates.map((tpl) => {
                const badgeCls = CATEGORY_BADGE[tpl.adapter_category.toLowerCase()] ?? "badge-gray";
                return (
                  <button
                    key={tpl.name}
                    type="button"
                    className="card-hover"
                    onClick={() => { onSelect(tpl.name); setOpen(false); }}
                    style={{
                      display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 8,
                      padding: 14, borderRadius: 8, cursor: "pointer", textAlign: "left",
                      border: "1px solid var(--color-border)",
                      background: "var(--color-bg-elevated)",
                      transition: "border-color 120ms ease, background 120ms ease",
                    }}
                    onMouseEnter={(e) => { e.currentTarget.style.borderColor = "var(--color-brand-light)"; }}
                    onMouseLeave={(e) => { e.currentTarget.style.borderColor = "var(--color-border)"; }}
                  >
                    <span className={badgeCls} style={{ fontSize: 10, textTransform: "capitalize" }}>
                      {tpl.adapter_category}
                    </span>
                    <div>
                      <p style={{ fontSize: 13, fontWeight: 600, color: "var(--color-text-primary)", marginBottom: 3 }}>
                        {tpl.name}
                      </p>
                      <p style={{ fontSize: 11, color: "var(--color-text-muted)", lineHeight: 1.4 }}>
                        {tpl.description}
                      </p>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Compare Modal ─────────────────────────────────────────────────────────────

const CHANGE_TYPE_STYLE: Record<ConfigDiffItem["change_type"], { color: string; bg: string; label: string }> = {
  added:    { color: "#22c55e", bg: "rgba(34,197,94,0.08)",   label: "Added" },
  removed:  { color: "#ef4444", bg: "rgba(239,68,68,0.08)",   label: "Removed" },
  modified: { color: "#d97706", bg: "rgba(217,119,6,0.08)",   label: "Modified" },
};

function CompareModal({ configs, onClose }: { configs: Configuration[]; onClose: () => void }) {
  const [configAId, setConfigAId] = useState(configs[0]?.id ?? "");
  const [configBId, setConfigBId] = useState(configs[1]?.id ?? "");
  const [result, setResult] = useState<ConfigDiffResponse | null>(null);
  const [diffError, setDiffError] = useState<string | null>(null);

  const diffMutation = useMutation({
    mutationFn: () => configurationsApi.diff(configAId, configBId),
    onSuccess: (resp) => { setResult(resp.data ?? null); setDiffError(null); },
    onError: () => { setDiffError("Failed to fetch diff. Check your selection."); },
  });

  const selectStyle: React.CSSProperties = {
    width: "100%", borderRadius: 6, border: "1px solid var(--color-border-strong)",
    background: "var(--color-bg-raised)", padding: "8px 12px",
    fontSize: 13, color: "var(--color-text-primary)", outline: "none",
  };

  return (
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 50,
        background: "rgba(0,0,0,0.6)", backdropFilter: "blur(4px)",
        display: "flex", alignItems: "center", justifyContent: "center", padding: 24,
      }}
      role="presentation"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}
    >
      <div
        style={{
          background: "var(--color-bg-elevated)", border: "1px solid var(--color-border-strong)",
          borderRadius: 12, padding: 24, width: "100%", maxWidth: 680,
          maxHeight: "80vh", overflowY: "auto", display: "flex", flexDirection: "column", gap: 20,
        }}
      >
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <GitCompare style={{ width: 16, height: 16, color: "var(--color-brand-light)" }} />
            <h2 style={{ fontSize: 15, fontWeight: 600, color: "var(--color-text-primary)", margin: 0 }}>
              Compare Configurations
            </h2>
          </div>
          <button type="button" onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--color-text-muted)", padding: 4 }}>
            <X style={{ width: 16, height: 16 }} />
          </button>
        </div>

        {/* Selectors */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          <div>
            <label htmlFor="diff-config-a" style={{ display: "block", fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--color-text-muted)", marginBottom: 6 }}>
              Config A (Base)
            </label>
            <select id="diff-config-a" value={configAId} onChange={(e) => { setConfigAId(e.target.value); setResult(null); }} style={selectStyle}>
              {configs.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="diff-config-b" style={{ display: "block", fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--color-text-muted)", marginBottom: 6 }}>
              Config B (Compare)
            </label>
            <select id="diff-config-b" value={configBId} onChange={(e) => { setConfigBId(e.target.value); setResult(null); }} style={selectStyle}>
              {configs.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
        </div>

        <button
          type="button"
          className="btn-primary"
          disabled={!configAId || !configBId || configAId === configBId || diffMutation.isPending}
          onClick={() => diffMutation.mutate()}
          style={{ alignSelf: "flex-start" }}
        >
          {diffMutation.isPending
            ? <><Loader2 style={{ width: 14, height: 14, animation: "spin 1s linear infinite" }} /> Comparing…</>
            : <><GitCompare style={{ width: 14, height: 14 }} /> Run Diff</>
          }
        </button>

        {configAId === configBId && configAId && (
          <p style={{ fontSize: 12, color: "var(--color-warning-text)" }}>Select two different configurations to compare.</p>
        )}

        {diffError && (
          <p style={{ fontSize: 12, color: "var(--color-error-text)" }}>{diffError}</p>
        )}

        {/* Results */}
        {result && (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ display: "flex", gap: 16 }}>
              <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
                <strong style={{ color: "var(--color-text-primary)" }}>{result.total_changes}</strong> total changes
              </span>
              {result.breaking_changes > 0 && (
                <span style={{ fontSize: 12, fontWeight: 600, color: "#ef4444" }}>
                  ⚠ {result.breaking_changes} breaking
                </span>
              )}
            </div>

            {result.diffs.length === 0 ? (
              <p style={{ fontSize: 13, color: "var(--color-text-muted)" }}>No differences found.</p>
            ) : (
              <div style={{ overflowX: "auto", borderRadius: 6, border: "1px solid var(--color-border)" }}>
                <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
                  <thead>
                    <tr style={{ borderBottom: "1px solid var(--color-border)", background: "var(--color-bg-base)" }}>
                      {["Path", "Type", "Old Value", "New Value", "Breaking"].map((h) => (
                        <th key={h} style={{ padding: "8px 12px", textAlign: "left", fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--color-text-muted)", whiteSpace: "nowrap" }}>
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {result.diffs.map((diff, i) => {
                      const ct = CHANGE_TYPE_STYLE[diff.change_type];
                      return (
                        // biome-ignore lint/suspicious/noArrayIndexKey: diff items have no stable id
                        <tr key={i} style={{ borderBottom: "1px solid var(--color-border)", background: ct.bg }}>
                          <td style={{ padding: "8px 12px" }}>
                            <code style={{ fontFamily: "monospace", fontSize: 11, color: "var(--color-text-primary)" }}>{diff.path}</code>
                          </td>
                          <td style={{ padding: "8px 12px" }}>
                            <span style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", color: ct.color, background: `${ct.color}18`, border: `1px solid ${ct.color}40`, borderRadius: 4, padding: "2px 7px" }}>
                              {ct.label}
                            </span>
                          </td>
                          <td style={{ padding: "8px 12px", maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            <code style={{ fontFamily: "monospace", fontSize: 11, color: "#ef4444" }}>
                              {diff.old_value !== null && diff.old_value !== undefined ? String(diff.old_value) : "—"}
                            </code>
                          </td>
                          <td style={{ padding: "8px 12px", maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            <code style={{ fontFamily: "monospace", fontSize: 11, color: "#22c55e" }}>
                              {diff.new_value !== null && diff.new_value !== undefined ? String(diff.new_value) : "—"}
                            </code>
                          </td>
                          <td style={{ padding: "8px 12px", textAlign: "center" }}>
                            {diff.is_breaking && <span style={{ fontSize: 11, color: "#ef4444", fontWeight: 700 }}>Yes</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Generate Form ─────────────────────────────────────────────────────────────

const inputStyle: React.CSSProperties = {
  width: "100%", borderRadius: 6, border: "1px solid var(--color-border-strong)",
  background: "var(--color-bg-raised)", padding: "8px 12px",
  fontSize: 13, color: "var(--color-text-primary)", outline: "none", boxSizing: "border-box",
};

function GenerateForm({ onDone }: { onDone: () => void }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [documentId, setDocumentId] = useState("");
  const [selectedAdapterId, setSelectedAdapterId] = useState("");
  const [adapterVersionId, setAdapterVersionId] = useState("");
  const [error, setError] = useState<string | null>(null);

  // "create adapter from document" inline form state
  const [showCreateAdapter, setShowCreateAdapter] = useState(false);
  const [newAdapterName, setNewAdapterName] = useState("");
  const [newAdapterCategory, setNewAdapterCategory] = useState<string>("custom");
  const [noMatchBanner, setNoMatchBanner] = useState(false);

  const { data: docsData } = useQuery({ queryKey: ["documents"], queryFn: () => documentsApi.list() });
  const { data: adaptersData, refetch: refetchAdapters } = useQuery({ queryKey: ["adapters"], queryFn: () => adaptersApi.list() });

  const docs = docsData?.data ?? [];
  const adapters: Adapter[] = adaptersData?.data?.adapters ?? [];
  const selectedAdapter = adapters.find((a) => a.id === selectedAdapterId);

  // Fetch document detail when a document is selected (for parsed_result)
  const { data: docDetailData } = useQuery({
    queryKey: ["document-detail", documentId],
    queryFn: () => documentsApi.get(documentId),
    enabled: !!documentId,
  });

  // Compute match score when document detail loads
  useEffect(() => {
    if (!documentId) {
      setNoMatchBanner(false);
      setShowCreateAdapter(false);
      return;
    }
    const parsed = docDetailData?.data?.parsed_result;
    if (!parsed || adapters.length === 0) return;
    const score = computeAdapterMatchScore(parsed as Record<string, unknown>, adapters);
    setNoMatchBanner(score < 0.30);
    if (score >= 0.30) {
      setShowCreateAdapter(false);
    }
  }, [documentId, docDetailData, adapters]);

  const generateMutation = useMutation({
    mutationFn: configurationsApi.generate,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["configurations"] });
      toast("Configuration generated.", "success");
      onDone();
    },
    onError: (err: Error) => { setError(err.message ?? "Generation failed."); },
  });

  const createAdapterMutation = useMutation({
    mutationFn: () => adaptersApi.createFromDocument(documentId, newAdapterName.trim(), newAdapterCategory),
    onSuccess: (resp) => {
      const created = resp.data;
      queryClient.invalidateQueries({ queryKey: ["adapters"] });
      refetchAdapters().then(() => {
        if (created) {
          setSelectedAdapterId(created.id);
          const firstVersion = created.versions[0];
          if (firstVersion) setAdapterVersionId(firstVersion.id);
          if (!name) setName(`${created.name} Integration`);
        }
      });
      setShowCreateAdapter(false);
      setNoMatchBanner(false);
      const endpointCount = created?.versions[0]?.endpoints.length ?? 0;
      toast(`Adapter '${newAdapterName.trim()}' created with ${endpointCount} endpoint${endpointCount !== 1 ? "s" : ""}.`, "success");
    },
    onError: () => { toast("Failed to create adapter.", "error"); },
  });

  const handleAdapterChange = (id: string) => {
    setSelectedAdapterId(id);
    setAdapterVersionId("");
    const adapter = adapters.find((a) => a.id === id);
    if (adapter && !name) setName(`${adapter.name} Integration`);
  };

  const handleDocumentChange = (id: string) => {
    setDocumentId(id);
    setNoMatchBanner(false);
    setShowCreateAdapter(false);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !documentId || !adapterVersionId) return;
    setError(null);
    generateMutation.mutate({ name: name.trim(), document_id: documentId, adapter_version_id: adapterVersionId, auto_map: true });
  };

  const focusStyle = (e: React.FocusEvent<HTMLSelectElement | HTMLInputElement>) => {
    e.currentTarget.style.borderColor = "var(--color-brand-light)";
  };
  const blurStyle = (e: React.FocusEvent<HTMLSelectElement | HTMLInputElement>) => {
    e.currentTarget.style.borderColor = "var(--color-border-strong)";
  };

  return (
    <div className="card animate-fade-in" style={{ padding: 24 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Sparkles style={{ width: 16, height: 16, color: "var(--color-brand-light)" }} />
          <h2 style={{ fontSize: 15, fontWeight: 600, color: "var(--color-text-primary)" }}>Generate Configuration</h2>
        </div>
      </div>

      <TemplatesSection onSelect={(tplName) => setName(tplName)} />

      {/* No-match banner */}
      {noMatchBanner && !showCreateAdapter && (
        <div
          className="animate-fade-in"
          style={{
            display: "flex", alignItems: "center", gap: 12,
            marginTop: 16, padding: "12px 16px",
            borderRadius: 8, border: "1px solid rgba(217,119,6,0.35)",
            background: "rgba(217,119,6,0.06)",
          }}
        >
          <AlertCircle style={{ width: 15, height: 15, color: "var(--color-warning-text)", flexShrink: 0 }} />
          <span style={{ flex: 1, fontSize: 13, color: "var(--color-warning-text)" }}>
            No matching adapter found for this document.
          </span>
          <button
            type="button"
            className="btn-primary"
            style={{ fontSize: 12, padding: "5px 12px", flexShrink: 0 }}
            onClick={() => setShowCreateAdapter(true)}
          >
            <Plus style={{ width: 13, height: 13 }} />
            Create New Adapter
          </button>
        </div>
      )}

      {/* Inline create-adapter form */}
      {showCreateAdapter && (
        <div
          className="animate-fade-in"
          style={{
            marginTop: 16, padding: 16,
            borderRadius: 8, border: "1px solid var(--color-border-strong)",
            background: "var(--color-bg-base)",
            display: "flex", flexDirection: "column", gap: 12,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: "var(--color-text-primary)" }}>
              Create Adapter from Document
            </span>
            <button
              type="button"
              onClick={() => { setShowCreateAdapter(false); setNoMatchBanner(true); }}
              style={{ background: "none", border: "none", cursor: "pointer", color: "var(--color-text-muted)", padding: 4 }}
            >
              <X style={{ width: 14, height: 14 }} />
            </button>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div>
              <label htmlFor="new-adapter-name" style={{ display: "block", fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--color-text-muted)", marginBottom: 6 }}>
                Adapter Name
              </label>
              <input
                id="new-adapter-name"
                type="text"
                value={newAdapterName}
                onChange={(e) => setNewAdapterName(e.target.value)}
                placeholder="e.g. UPI Payment Gateway"
                style={inputStyle}
                onFocus={focusStyle}
                onBlur={blurStyle}
              />
            </div>

            <div>
              <label htmlFor="new-adapter-category" style={{ display: "block", fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--color-text-muted)", marginBottom: 6 }}>
                Category
              </label>
              <select
                id="new-adapter-category"
                value={newAdapterCategory}
                onChange={(e) => setNewAdapterCategory(e.target.value)}
                style={inputStyle}
                onFocus={focusStyle}
                onBlur={blurStyle}
              >
                {ADAPTER_CATEGORIES.map((cat) => (
                  <option key={cat} value={cat}>{cat}</option>
                ))}
              </select>
            </div>
          </div>

          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <button
              type="button"
              className="btn-secondary"
              style={{ fontSize: 12, padding: "5px 12px" }}
              onClick={() => { setShowCreateAdapter(false); setNoMatchBanner(true); }}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn-primary"
              style={{ fontSize: 12, padding: "5px 12px" }}
              disabled={!newAdapterName.trim() || createAdapterMutation.isPending}
              onClick={() => createAdapterMutation.mutate()}
            >
              {createAdapterMutation.isPending
                ? <><Loader2 style={{ width: 13, height: 13, animation: "spin 1s linear infinite" }} /> Creating...</>
                : <><Plus style={{ width: 13, height: 13 }} /> Create Adapter</>
              }
            </button>
          </div>
        </div>
      )}

      <form onSubmit={handleSubmit} style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 16, marginTop: 16 }}>
        <div>
          <label style={{ display: "block", fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--color-text-muted)", marginBottom: 6 }}>
            Document
          </label>
          <select value={documentId} onChange={(e) => handleDocumentChange(e.target.value)} style={inputStyle} onFocus={focusStyle} onBlur={blurStyle}>
            <option value="">Select document...</option>
            {docs.map((doc) => (
              <option key={doc.id} value={doc.id}>{doc.filename} ({doc.status})</option>
            ))}
          </select>
        </div>

        <div>
          <label style={{ display: "block", fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--color-text-muted)", marginBottom: 6 }}>
            Adapter
          </label>
          <select value={selectedAdapterId} onChange={(e) => handleAdapterChange(e.target.value)} style={inputStyle} onFocus={focusStyle} onBlur={blurStyle}>
            <option value="">Select adapter...</option>
            {adapters.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </div>

        <div>
          <label style={{ display: "block", fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--color-text-muted)", marginBottom: 6 }}>
            Version
          </label>
          <select
            value={adapterVersionId}
            onChange={(e) => setAdapterVersionId(e.target.value)}
            disabled={!selectedAdapter}
            style={{ ...inputStyle, opacity: selectedAdapter ? 1 : 0.5 }}
            onFocus={focusStyle} onBlur={blurStyle}
          >
            <option value="">{selectedAdapter ? "Select version..." : "Select adapter first"}</option>
            {selectedAdapter?.versions.map((v) => (
              <option key={v.id} value={v.id}>v{v.version} — {v.status}</option>
            ))}
          </select>
        </div>

        <div>
          <label style={{ display: "block", fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--color-text-muted)", marginBottom: 6 }}>
            Name
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Credit Bureau Integration"
            style={inputStyle}
            onFocus={focusStyle} onBlur={blurStyle}
          />
        </div>

        <div style={{ gridColumn: "1 / -1", display: "flex", justifyContent: "flex-end", gap: 8 }}>
          {error && (
            <div style={{ flex: 1, borderRadius: 6, border: "1px solid rgba(220,38,38,0.2)", background: "rgba(220,38,38,0.05)", padding: "8px 12px", fontSize: 12, color: "var(--color-error-text)" }}>
              {error}
            </div>
          )}
          <button type="submit" className="btn-primary" disabled={!name.trim() || !documentId || !adapterVersionId || generateMutation.isPending}>
            <Sparkles style={{ width: 14, height: 14 }} />
            {generateMutation.isPending ? "Generating..." : "Generate"}
          </button>
        </div>
      </form>
    </div>
  );
}

// ── Validation pipeline (one-click validate → test) ──────────────────────────

type StepStatus = "pending" | "running" | "passed" | "failed";
type PipelineStep = { name: string; status: StepStatus; confidence?: number; analysis?: string };
type PipelinePhase = "validating" | "testing" | "done" | "error";
type PipelineUI = {
  configId: string;
  configName: string;
  phase: PipelinePhase;
  validation: PipelineStep[];
  testing: PipelineStep[];
  errorMsg?: string;
};

// Seed names — we render these as "running" the moment the user clicks, before
// the LLM call returns, so the panel never feels stale. The LLM verdict snaps
// each row to its real status when the request resolves.
const VALIDATION_STEPS_SEED: string[] = [
  "config_structure_validation",
  "field_mapping_quality",
  "auth_configuration_adequacy",
  "error_handling_robustness",
  "retry_logic_appropriateness",
  "endpoint_configuration_validity",
  "security_best_practices",
];

const TEST_STEPS_SEED: string[] = [
  "auth_handshake",
  "happy_path_endpoint",
  "error_envelope_check",
  "retry_behaviour",
];

function ValidationPipelinePanel({ ui, onClose }: { ui: PipelineUI; onClose: () => void }) {
  const phaseLabel: Record<PipelinePhase, string> = {
    validating: "Phase 1 of 2 — Validating Configuration",
    testing: "Phase 2 of 2 — Running Smoke Tests",
    done: "Pipeline Complete — Ready to Deploy",
    error: "Pipeline Stopped",
  };
  const phaseColor: Record<PipelinePhase, string> = {
    validating: "var(--color-primary)",
    testing: "var(--color-primary)",
    done: "var(--color-success-text)",
    error: "var(--color-error-text)",
  };
  return (
    <div
      style={{
        padding: 16,
        borderRadius: 10,
        border: `1px solid ${phaseColor[ui.phase]}`,
        background: "var(--color-surface-2)",
        boxShadow: "0 2px 8px rgba(0,0,0,0.06)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {ui.phase === "validating" || ui.phase === "testing" ? (
            <Loader2 style={{ width: 16, height: 16, color: phaseColor[ui.phase], animation: "spin 1s linear infinite" }} />
          ) : ui.phase === "done" ? (
            <CheckCircle2 style={{ width: 16, height: 16, color: phaseColor[ui.phase] }} />
          ) : (
            <AlertCircle style={{ width: 16, height: 16, color: phaseColor[ui.phase] }} />
          )}
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, color: phaseColor[ui.phase] }}>
              {phaseLabel[ui.phase]}
            </div>
            <div style={{ fontSize: 11, color: "var(--color-text-muted)" }}>{ui.configName}</div>
          </div>
        </div>
        {(ui.phase === "done" || ui.phase === "error") && (
          <button type="button" className="btn-secondary" style={{ fontSize: 11, padding: "4px 10px" }} onClick={onClose}>
            Dismiss
          </button>
        )}
      </div>
      <PipelineStepGroup title="Validation dimensions" steps={ui.validation} />
      <PipelineStepGroup title="Smoke tests" steps={ui.testing} />
      {ui.errorMsg && (
        <div style={{ marginTop: 10, padding: "8px 10px", borderRadius: 6, background: "rgba(248,113,113,0.06)", color: "var(--color-error-text)", fontSize: 12 }}>
          {ui.errorMsg}
        </div>
      )}
    </div>
  );
}

function PipelineStepGroup({ title, steps }: { title: string; steps: PipelineStep[] }) {
  if (steps.length === 0) return null;
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.6, color: "var(--color-text-muted)", marginBottom: 6 }}>
        {title}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {steps.map((s) => (
          <PipelineStepRow key={s.name} step={s} />
        ))}
      </div>
    </div>
  );
}

function PipelineStepRow({ step }: { step: PipelineStep }) {
  const icon =
    step.status === "passed" ? (
      <CheckCircle2 style={{ width: 13, height: 13, color: "var(--color-success-text)" }} />
    ) : step.status === "failed" ? (
      <AlertCircle style={{ width: 13, height: 13, color: "var(--color-error-text)" }} />
    ) : step.status === "running" ? (
      <Loader2 style={{ width: 13, height: 13, color: "var(--color-primary)", animation: "spin 1s linear infinite" }} />
    ) : (
      <Clock style={{ width: 13, height: 13, color: "var(--color-text-muted)" }} />
    );
  const label = step.name.replace(/_/g, " ");
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 8,
        padding: "6px 10px",
        borderRadius: 6,
        background: "var(--color-surface-1)",
      }}
    >
      {icon}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12, color: "var(--color-text-primary)" }}>{label}</div>
        {step.analysis && (
          <div style={{ fontSize: 11, color: "var(--color-text-muted)", marginTop: 2 }}>{step.analysis}</div>
        )}
      </div>
      {typeof step.confidence === "number" && (
        <span className="mono" style={{ fontSize: 11, color: "var(--color-text-muted)" }}>
          {Math.round((step.confidence || 0) * 100)}%
        </span>
      )}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function Configurations() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [showForm, setShowForm] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [showCompare, setShowCompare] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<Configuration | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["configurations"],
    queryFn: () => configurationsApi.list(),
  });

  const [lastSimResult, setLastSimResult] = useState<{
    configId: string; status: string; passed: number; total: number; steps: Array<{ step_name: string; status: string; error_message?: string }>;
  } | null>(null);

  const [pipeline, setPipeline] = useState<PipelineUI | null>(null);

  // One-click pipeline: configured → validate (LLM 7-dim) → testing → smoke.
  // Optimistically renders skeleton step rows the moment the user clicks,
  // then snaps each row to the real verdict when the LLM call resolves.
  // Single composite endpoint call replaces the legacy 4-request orchestration.
  // The optimistic seed rows render while the request is in flight and snap to
  // real verdicts when the response lands. If the smoke run produced a
  // simulation_id we fetch the per-step breakdown for the dimension table.
  const runPipelineMutation = useMutation({
    mutationFn: async ({ id, name }: { id: string; name: string; currentStatus: string }) => {
      // Optimistic: paint both phases as running before the await
      setPipeline({
        configId: id,
        configName: name,
        phase: "validating",
        validation: VALIDATION_STEPS_SEED.map((n) => ({ name: n, status: "running" as StepStatus })),
        testing: TEST_STEPS_SEED.map((n) => ({ name: n, status: "running" as StepStatus })),
      });

      const resp = await configurationsApi.validateAndTest(id, {
        test_type: "smoke",
        reason: "ui:pipeline",
      });
      const data = resp.data;
      if (!data) {
        setPipeline((s) => (s ? { ...s, phase: "error", errorMsg: "Pipeline request failed" } : s));
        return;
      }

      // The composite returns 4 high-level pipeline steps:
      //   transition_to_validating | validate | transition_to_testing | smoke_simulation
      // We use `validate` for Phase 1's overall status + the dimension breakdown
      // we fetch separately; `smoke_simulation` for Phase 2.
      const validateStep = data.steps.find((s) => s.name === "validate");
      const smokeStep = data.steps.find((s) => s.name === "smoke_simulation");

      // Phase 1: real per-dimension breakdown. The composite doesn't include the
      // 7 LLM dimensions, only the aggregate validate verdict. So we fetch the
      // smoke run's step records (which include the 7 dimensions when AI is on).
      let validation: PipelineStep[];
      if (data.simulation_id) {
        try {
          const simResp = await simulationsApi.get(data.simulation_id);
          const simSteps = simResp.data?.steps ?? [];
          if (simSteps.length > 0) {
            validation = simSteps.map((s) => ({
              name: s.step_name,
              status: (s.status === "passed" ? "passed" : "failed") as StepStatus,
              confidence: s.confidence_score ?? undefined,
              analysis: s.error_message || undefined,
            }));
          } else {
            validation = [
              {
                name: "validate",
                status: (validateStep?.status === "passed" ? "passed" : "failed") as StepStatus,
                analysis: validateStep?.error ?? undefined,
              },
            ];
          }
        } catch {
          // simulation fetch failed — fall back to the aggregate
          validation = [
            {
              name: "validate",
              status: (validateStep?.status === "passed" ? "passed" : "failed") as StepStatus,
              analysis: validateStep?.error ?? undefined,
            },
          ];
        }
      } else {
        validation = [
          {
            name: "validate",
            status: (validateStep?.status === "passed" ? "passed" : "failed") as StepStatus,
            analysis: validateStep?.error ?? undefined,
          },
        ];
      }

      // Phase 2: a single aggregate row driven by the composite's smoke step.
      const testing: PipelineStep[] = smokeStep
        ? [
            {
              name: `smoke_simulation (${data.passed_tests}/${data.total_tests})`,
              status: (smokeStep.status === "passed" ? "passed" : "failed") as StepStatus,
              confidence: data.total_tests > 0 ? data.passed_tests / data.total_tests : undefined,
              analysis: smokeStep.error ?? undefined,
            },
          ]
        : [];

      const overallPassed = data.overall_status === "passed";
      setPipeline((s) =>
        s
          ? {
              ...s,
              phase: overallPassed ? "done" : "error",
              validation,
              testing,
              errorMsg: overallPassed
                ? undefined
                : `Pipeline ${data.overall_status}: ${data.passed_tests}/${data.total_tests} smoke tests passed`,
            }
          : s,
      );
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["configurations"] });
      queryClient.invalidateQueries({ queryKey: ["config-history"] });
    },
    onError: (err: unknown) => {
      setPipeline((s) =>
        s ? { ...s, phase: "error", errorMsg: err instanceof Error ? err.message : "Pipeline failed" } : s,
      );
      toast("Validation pipeline failed.", "error");
    },
  });

  const transitionMutation = useMutation({
    mutationFn: async ({ id, targetState }: { id: string; targetState: string }) => {
      // Validate -> smoke pipeline now lives behind a single composite endpoint
      // (POST /api/v1/configurations/{id}/validate-and-test, issue #116).
      // We still call /transition for every other lifecycle step.
      if (targetState === "testing") {
        const resp = await configurationsApi.validateAndTest(id, { test_type: "smoke", reason: "ui:start-testing" });
        const pipeline = resp.data;
        if (pipeline) {
          setLastSimResult({
            configId: id,
            status: pipeline.overall_status === "passed" ? "passed" : "failed",
            passed: pipeline.passed_tests,
            total: pipeline.total_tests,
            steps: (pipeline.steps || []).map((s) => ({
              step_name: s.name,
              status: s.status,
              error_message: s.error ?? undefined,
            })),
          });
        }
        return resp;
      }
      return configurationsApi.transition(id, targetState);
    },
    onSuccess: (_data, vars) => {
      queryClient.invalidateQueries({ queryKey: ["configurations"] });
      queryClient.invalidateQueries({ queryKey: ["config-history"] });
      if (vars.targetState === "testing" && lastSimResult) {
        toast(`Testing: ${lastSimResult.passed}/${lastSimResult.total} passed`, lastSimResult.status === "passed" ? "success" : "error");
      } else {
        const labels: Record<string, string> = { configured: "Marked as configured", validating: "Validation started", testing: "Testing started", active: "Deployed to production" };
        toast(labels[vars.targetState] || "State updated.", "success");
      }
    },
    onError: () => { toast("Transition failed.", "error"); },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => configurationsApi.delete(id),
    onSuccess: () => {
      setConfirmDelete(null);
      queryClient.invalidateQueries({ queryKey: ["configurations"] });
      toast("Configuration deleted.", "success");
    },
    onError: () => {
      toast("Failed to delete configuration.", "error");
      setConfirmDelete(null);
    },
  });

  const configs: Configuration[] = data?.data ?? [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {/* Compare modal */}
      {showCompare && configs.length >= 2 && (
        <CompareModal configs={configs} onClose={() => setShowCompare(false)} />
      )}

      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: "var(--color-text-primary)", marginBottom: 4 }}>Configurations</h1>
          <p style={{ fontSize: 13, color: "var(--color-text-secondary)" }}>
            Generate and manage integration configurations
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {configs.length >= 2 && (
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setShowCompare(true)}
            >
              <GitCompare style={{ width: 14, height: 14 }} /> Compare
            </button>
          )}
          <button
            type="button"
            className={showForm ? "btn-secondary" : "btn-primary"}
            onClick={() => setShowForm((v) => !v)}
          >
            {showForm ? "Cancel" : <><Plus style={{ width: 15, height: 15 }} /> Generate Config</>}
          </button>
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div style={{ borderRadius: 8, border: "1px solid rgba(217,119,6,0.2)", background: "rgba(217,119,6,0.05)", padding: "12px 16px", fontSize: 13, color: "var(--color-warning-text)" }}>
          Failed to load configurations. Check your connection and try again.
        </div>
      )}

      {/* Generate form slide-in */}
      {showForm && <GenerateForm onDone={() => setShowForm(false)} />}

      {/* Config list */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {isLoading ? (
          <><SkeletonRow /><SkeletonRow /><SkeletonRow /></>
        ) : configs.length === 0 && !error ? (
          <div className="card" style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "64px 24px", textAlign: "center" }}>
            <Settings style={{ width: 40, height: 40, color: "var(--color-text-muted)", marginBottom: 12 }} />
            <p style={{ fontSize: 14, fontWeight: 500, color: "var(--color-text-secondary)", marginBottom: 4 }}>No configurations yet</p>
            <p style={{ fontSize: 12, color: "var(--color-text-muted)" }}>Click "Generate Config" to create your first integration configuration.</p>
          </div>
        ) : (
          configs.map((cfg) => {
            const st = STATUS_CONFIG[cfg.status] ?? { label: cfg.status, cls: "badge-gray" };
            const isExpanded = expandedId === cfg.id;
            const transitions = TRANSITION_BUTTONS[cfg.status] ?? [];

            return (
              <div key={cfg.id} className="card" style={{ overflow: "hidden" }}>
                {/* Row header */}
                <button
                  type="button"
                  style={{
                    display: "flex", width: "100%", alignItems: "center", gap: 16,
                    padding: "14px 20px", textAlign: "left", background: "transparent", cursor: "pointer",
                    transition: "background 100ms ease",
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = "var(--color-bg-raised)"; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                  onClick={() => setExpandedId(isExpanded ? null : cfg.id)}
                >
                  <div style={{ width: 36, height: 36, borderRadius: 8, background: "var(--color-bg-raised)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                    <Settings style={{ width: 16, height: 16, color: "var(--color-text-muted)" }} />
                  </div>

                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 4 }}>
                      <span style={{ fontSize: 14, fontWeight: 600, color: "var(--color-text-primary)" }}>{cfg.name}</span>
                      <span className={st.cls}>{st.label}</span>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12, color: "var(--color-text-muted)" }}>
                      <span className="mono" style={{ fontSize: 12 }}>v{cfg.version}</span>
                      <span>·</span>
                      <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                        <Clock style={{ width: 11, height: 11 }} />
                        {fmtDate(cfg.updated_at)}
                      </span>
                      <span>·</span>
                      <span>{cfg.field_mappings.length} mappings</span>
                    </div>
                  </div>

                  {/* Lifecycle transition buttons + delete */}
                  <div style={{ display: "flex", gap: 8 }} onClick={(e) => e.stopPropagation()}>
                    {transitions.map((t) => {
                      const TIcon = t.icon;
                      const isPipeline = t.targetState === "__pipeline__";
                      const busy = isPipeline
                        ? runPipelineMutation.isPending && pipeline?.configId === cfg.id
                        : transitionMutation.isPending;
                      return (
                        <button
                          key={t.targetState + cfg.status}
                          type="button"
                          className="btn-secondary"
                          style={{ fontSize: 11, padding: "5px 10px" }}
                          disabled={busy || runPipelineMutation.isPending}
                          onClick={() => {
                            if (isPipeline) {
                              setLastSimResult(null);
                              runPipelineMutation.mutate({ id: cfg.id, name: cfg.name, currentStatus: cfg.status });
                              return;
                            }
                            if (t.targetState === "active" && !window.confirm(`Deploy "${cfg.name}" to production?`)) return;
                            setLastSimResult(null);
                            transitionMutation.mutate({ id: cfg.id, targetState: t.targetState });
                          }}
                        >
                          {busy ? (
                            <Loader2 style={{ width: 12, height: 12, animation: "spin 1s linear infinite" }} />
                          ) : (
                            <TIcon style={{ width: 12, height: 12 }} />
                          )}
                          {busy ? "Running…" : t.label}
                        </button>
                      );
                    })}
                    <button
                      type="button"
                      className="btn-secondary"
                      style={{ fontSize: 11, padding: "5px 8px", color: "var(--color-error-text)" }}
                      onClick={() => setConfirmDelete(cfg)}
                      aria-label={`Delete ${cfg.name}`}
                    >
                      <Trash2 style={{ width: 12, height: 12 }} />
                    </button>
                  </div>

                  <ChevronDown style={{
                    width: 15, height: 15, color: "var(--color-text-muted)", flexShrink: 0,
                    transform: isExpanded ? "rotate(180deg)" : "rotate(0deg)", transition: "transform 150ms ease",
                  }} />
                </button>

                {/* Inline pipeline progress — only renders for the config that ran it */}
                {pipeline && pipeline.configId === cfg.id && (
                  <div style={{ padding: "12px 18px", borderTop: "1px solid var(--color-border)" }}>
                    <ValidationPipelinePanel ui={pipeline} onClose={() => setPipeline(null)} />
                  </div>
                )}

                {/* Simulation results after transitioning to testing */}
                {lastSimResult && lastSimResult.configId === cfg.id && (
                  <div style={{
                    padding: "12px 18px", borderTop: `1px solid var(--color-border)`,
                    background: lastSimResult.status === "passed" ? "rgba(52,211,153,0.06)" : "rgba(248,113,113,0.06)",
                    borderRadius: "0 0 var(--glass-radius) var(--glass-radius)",
                  }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                      <span style={{ fontWeight: 700, fontSize: 13, color: lastSimResult.status === "passed" ? "var(--color-success-text)" : "var(--color-error-text)" }}>
                        {lastSimResult.status === "passed" ? "✓" : "✗"} Simulation {lastSimResult.status.toUpperCase()}
                      </span>
                      <span style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>
                        — {lastSimResult.passed}/{lastSimResult.total} tests passed
                      </span>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                      {lastSimResult.steps.map((s, i) => (
                        <div key={i} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
                          <span style={{ color: s.status === "passed" ? "var(--color-success-text)" : "var(--color-error-text)", fontWeight: 600 }}>
                            {s.status === "passed" ? "✓" : "✗"}
                          </span>
                          <span style={{ color: "var(--color-text-secondary)" }}>{s.step_name}</span>
                          {s.error_message && <span style={{ color: "var(--color-error-text)", fontSize: 11 }}>— {s.error_message}</span>}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Expanded detail */}
                {isExpanded && <ConfigDetail cfg={cfg} />}
              </div>
            );
          })
        )}
      </div>

      {/* Delete confirmation modal */}
      {confirmDelete && (
        <div
          style={{
            position: "fixed", inset: 0, zIndex: 50,
            background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center",
          }}
          onClick={(e) => { if (e.target === e.currentTarget) setConfirmDelete(null); }}
        >
          <div className="card" style={{ padding: 24, maxWidth: 420, width: "90%" }}>
            <h3 style={{ fontSize: 15, fontWeight: 600, color: "var(--color-text-primary)", marginBottom: 8 }}>
              Delete configuration?
            </h3>
            <p style={{ fontSize: 13, color: "var(--color-text-secondary)", marginBottom: 16 }}>
              <span className="mono" style={{ color: "var(--color-text-primary)" }}>{confirmDelete.name}</span>{" "}
              and its version history will be permanently deleted.
            </p>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button type="button" className="btn-secondary" onClick={() => setConfirmDelete(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn-primary"
                style={{ background: deleteMutation.isPending ? undefined : "rgba(220,38,38,0.12)", borderColor: "rgba(220,38,38,0.3)", color: "var(--color-error-text)" }}
                disabled={deleteMutation.isPending}
                onClick={() => deleteMutation.mutate(confirmDelete.id)}
              >
                {deleteMutation.isPending ? (
                  <Loader2 style={{ width: 13, height: 13, animation: "spin 1s linear infinite" }} />
                ) : (
                  <><Trash2 style={{ width: 13, height: 13 }} /> Delete</>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
