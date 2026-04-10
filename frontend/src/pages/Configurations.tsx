import { useToast } from "@/components/Toast";
import { adaptersApi, configurationsApi, documentsApi, simulationsApi } from "@/lib/api";
import type {
  Adapter,
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
  BarChart3,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Download,
  GitCompare,
  History,
  Loader2,
  PlayCircle,
  Plus,
  Rocket,
  RotateCcw,
  Save,
  Settings,
  ShieldCheck,
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
  configured: [{ label: "Start Validation", icon: PlayCircle, targetState: "validating" }],
  validating: [{ label: "Start Testing", icon: PlayCircle, targetState: "testing" }],
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

type DetailTab = "mappings" | "history" | "validation";

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

  const tabs: { id: DetailTab; label: string; icon: React.ElementType }[] = [
    { id: "mappings", label: "Mappings", icon: Settings },
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

  const transitionMutation = useMutation({
    mutationFn: async ({ id, targetState }: { id: string; targetState: string }) => {
      const transResult = await configurationsApi.transition(id, targetState);
      // Auto-run simulation when transitioning to "testing"
      if (targetState === "testing") {
        try {
          const simResp = await simulationsApi.run({ configuration_id: id, test_type: "smoke" });
          const sim = simResp.data;
          if (sim) {
            setLastSimResult({
              configId: id,
              status: sim.status,
              passed: sim.passed_tests,
              total: sim.total_tests,
              steps: sim.steps || [],
            });
          }
        } catch {
          // Simulation failed but transition succeeded — still OK
        }
      }
      return transResult;
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

  const batchValidateMutation = useMutation({
    mutationFn: (configIds: string[]) => configurationsApi.batchValidate(configIds),
    onSuccess: (resp) => {
      const d = resp.data as Record<string, unknown> | null;
      if (d && typeof d === "object") {
        const valid = (d.valid_count as number | undefined) ?? (d.valid as unknown[])?.length ?? "?";
        const invalid = (d.invalid_count as number | undefined) ?? (d.invalid as unknown[])?.length ?? "?";
        toast(`Batch validate complete: ${valid} valid, ${invalid} invalid.`, "success");
      } else {
        toast("Batch validate complete.", "success");
      }
    },
    onError: () => { toast("Batch validate failed.", "error"); },
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
          {configs.length > 0 && (
            <button
              type="button"
              className="btn-secondary"
              disabled={batchValidateMutation.isPending}
              onClick={() => batchValidateMutation.mutate(configs.map((c) => c.id))}
            >
              {batchValidateMutation.isPending
                ? <><Loader2 style={{ width: 14, height: 14, animation: "spin 1s linear infinite" }} /> Validating…</>
                : <><ShieldCheck style={{ width: 14, height: 14 }} /> Validate All</>
              }
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
                      return (
                        <button
                          key={t.targetState}
                          type="button"
                          className="btn-secondary"
                          style={{ fontSize: 11, padding: "5px 10px" }}
                          disabled={transitionMutation.isPending}
                          onClick={() => {
                            if (t.targetState === "active" && !window.confirm(`Deploy "${cfg.name}" to production?`)) return;
                            setLastSimResult(null);
                            transitionMutation.mutate({ id: cfg.id, targetState: t.targetState });
                          }}
                        >
                          <TIcon style={{ width: 12, height: 12 }} />
                          {t.label}
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
