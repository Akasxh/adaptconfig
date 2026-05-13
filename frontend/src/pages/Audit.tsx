import Pagination from "@/components/Pagination";
import { auditApi } from "@/lib/api";
import type { AuditEntry } from "@/types";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Shield } from "lucide-react";
import { useState } from "react";

const ACTION_OPTIONS = [
  { value: "", label: "All Actions" },
  { value: "upload_document", label: "Upload Document" },
  { value: "delete_document", label: "Delete Document" },
  { value: "generate_config", label: "Generate Config" },
  { value: "transition", label: "Transition" },
  { value: "rollback", label: "Rollback" },
  { value: "run_simulation", label: "Run Simulation" },
  { value: "create", label: "Create" },
];

const RESOURCE_TYPE_OPTIONS = [
  { value: "", label: "All Resources" },
  { value: "document", label: "Document" },
  { value: "configuration", label: "Configuration" },
  { value: "simulation", label: "Simulation" },
  { value: "adapter", label: "Adapter" },
];

const ACTION_BADGE: Record<string, [string, string, string]> = {
  upload_document: ["Upload",     "#2d8fce", "rgba(29,111,164,0.15)"],
  delete_document: ["Delete",     "#f87171", "rgba(248,113,113,0.12)"],
  generate_config: ["Gen Config", "#0fb89a", "rgba(15,184,154,0.12)"],
  transition:      ["Transition", "#facc15", "rgba(250,204,21,0.12)"],
  rollback:        ["Rollback",   "#facc15", "rgba(250,204,21,0.12)"],
  run_simulation:  ["Simulation", "#2d8fce", "rgba(29,111,164,0.15)"],
  create:          ["Create",     "#0fb89a", "rgba(15,184,154,0.12)"],
};

const RESOURCE_BADGE: Record<string, [string, string]> = {
  document:      ["#2d8fce", "rgba(29,111,164,0.15)"],
  configuration: ["#0fb89a", "rgba(15,184,154,0.12)"],
  simulation:    ["#facc15", "rgba(250,204,21,0.12)"],
  adapter:       ["#94a3b8", "rgba(148,163,184,0.12)"],
};

const PAGE_SIZE = 20;
const BADGE_BASE: React.CSSProperties = { fontSize: 11, fontWeight: 600, letterSpacing: "0.04em", padding: "2px 8px", borderRadius: 4, textTransform: "uppercase", whiteSpace: "nowrap" };
const SELECT_STYLE: React.CSSProperties = { appearance: "none", background: "#0f1724", border: "1px solid #1e2d47", borderRadius: 6, color: "#94a3b8", fontSize: 13, padding: "6px 28px 6px 10px", cursor: "pointer", outline: "none" };
const COL_HEADERS = ["Timestamp", "Actor", "Action", "Resource Type", "Resource ID", "Details"];

function relativeTime(iso: string): string {
  const m = Math.floor((Date.now() - new Date(iso).getTime()) / 60_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  if (m < 1440) return `${Math.floor(m / 60)}h ago`;
  const d = Math.floor(m / 1440);
  return d < 30 ? `${d}d ago` : new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function Chip({ label, color, bg }: { label: string; color: string; bg: string }) {
  return <span style={{ ...BADGE_BASE, color, background: bg }}>{label}</span>;
}

// biome-ignore lint/complexity/noExcessiveCognitiveComplexity: row with expand logic
function AuditRow({ entry }: { entry: AuditEntry }) {
  const [open, setOpen] = useState(false);
  const [hovered, setHovered] = useState(false);
  const hasDetails = !!entry.details && Object.keys(entry.details).length > 0;
  const [aLabel, aColor, aBg] = ACTION_BADGE[entry.action] ?? [entry.action, "#94a3b8", "rgba(148,163,184,0.12)"];
  const [rColor, rBg] = RESOURCE_BADGE[entry.resource_type] ?? ["#94a3b8", "rgba(148,163,184,0.12)"];

  return (
    <>
      <tr
        onClick={() => hasDetails && setOpen((v) => !v)}
        onKeyDown={hasDetails ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen((v) => !v); } } : undefined}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        role={hasDetails ? "button" : undefined}
        tabIndex={hasDetails ? 0 : undefined}
        style={{ height: 36, borderBottom: "1px solid #1e2d47", cursor: hasDetails ? "pointer" : "default", background: open ? "rgba(29,111,164,0.06)" : hovered ? "rgba(255,255,255,0.02)" : "transparent" }}
      >
        <td style={{ padding: "0 16px", fontSize: 12, color: "#64748b", whiteSpace: "nowrap" }}>
          <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
            {hasDetails && <ChevronRight size={12} style={{ color: "#2d8fce", transition: "transform 0.15s", transform: open ? "rotate(90deg)" : "none" }} />}
            <time dateTime={entry.created_at}>{relativeTime(entry.created_at)}</time>
          </span>
        </td>
        <td style={{ padding: "0 16px", fontSize: 13, color: "#cbd5e1" }}>{entry.actor}</td>
        <td style={{ padding: "0 16px" }}><Chip label={aLabel} color={aColor} bg={aBg} /></td>
        <td style={{ padding: "0 16px" }}><Chip label={entry.resource_type} color={rColor} bg={rBg} /></td>
        <td style={{ padding: "0 16px", maxWidth: 180 }}>
          <span className="mono" style={{ fontSize: 12, color: "#64748b", display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={entry.resource_id}>
            {entry.resource_id}
          </span>
        </td>
        <td style={{ padding: "0 16px", textAlign: "center" }}>
          {hasDetails
            ? <ChevronDown size={14} style={{ color: "#2d8fce", transform: open ? "rotate(180deg)" : "none", transition: "transform 0.15s" }} />
            : <span style={{ color: "#2a3f5e" }}>—</span>}
        </td>
      </tr>
      {open && hasDetails && (
        <tr style={{ background: "rgba(10,15,26,0.4)" }}>
          <td colSpan={6} style={{ padding: "4px 16px 10px" }}>
            <div style={{ background: "rgba(10,15,26,0.6)", border: "1px solid #1e2d47", borderRadius: 6, padding: "8px 12px", display: "flex", flexWrap: "wrap", gap: "4px 16px" }}>
              {Object.entries(entry.details as Record<string, unknown>).map(([k, v]) => (
                <span key={k} style={{ fontSize: 12 }}>
                  <span style={{ color: "#475569" }}>{k}:</span>{" "}
                  <span style={{ color: "#94a3b8" }}>{String(v)}</span>
                </span>
              ))}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export default function Audit() {
  const [page, setPage] = useState(1);
  const [actionFilter, setActionFilter] = useState("");
  const [resourceTypeFilter, setResourceTypeFilter] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["audit", page, actionFilter, resourceTypeFilter],
    queryFn: () =>
      auditApi.list({
        page,
        page_size: PAGE_SIZE,
        ...(actionFilter ? { action: actionFilter } : {}),
        ...(resourceTypeFilter ? { resource_type: resourceTypeFilter } : {}),
      }),
  });

  const entries: AuditEntry[] = isLoading ? [] : (data?.data?.items ?? []);
  const total = data?.data?.total ?? 0;
  const uniqueTypes = new Set(entries.map((e) => e.resource_type)).size;

  function handleFilter(setter: (v: string) => void, value: string) {
    setter(value);
    setPage(1);
  }

  return (
    <div className="animate-fade-in" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <div>
        <h1 style={{ fontSize: 22, fontWeight: 700, color: "#f1f5f9", margin: 0 }}>Audit Log</h1>
        <p style={{ fontSize: 13, color: "#64748b", marginTop: 4 }}>Activity history and compliance tracking</p>
      </div>

      {error && (
        <div role="alert" style={{ background: "rgba(248,113,113,0.08)", border: "1px solid rgba(248,113,113,0.25)", borderRadius: 8, padding: "10px 14px", fontSize: 13, color: "#f87171" }}>
          Failed to load audit log. Please refresh.
        </div>
      )}

      {/* Stat cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 16, maxWidth: 400 }}>
        {([
          ["Total Events", isLoading, total, "#f1f5f9"],
          ["Resource Types", isLoading, uniqueTypes, "#0fb89a"],
        ] as [string, boolean, number, string][]).map(([label, loading, val, color]) => (
          <div key={label} className="card" style={{ padding: 16, textAlign: "center" }}>
            <p style={{ fontSize: 28, fontWeight: 700, color, margin: 0 }}>
              {loading ? <span style={{ display: "inline-block", width: 36, height: 26, background: "#1e2d47", borderRadius: 4 }} /> : val}
            </p>
            <p className="section-label" style={{ marginTop: 4 }}>{label}</p>
          </div>
        ))}
      </div>

      {/* Filter bar */}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        {([
          [ACTION_OPTIONS, actionFilter, setActionFilter, "Filter by action"],
          [RESOURCE_TYPE_OPTIONS, resourceTypeFilter, setResourceTypeFilter, "Filter by resource type"],
        ] as [typeof ACTION_OPTIONS, string, (v: string) => void, string][]).map(([opts, val, setter, label]) => (
          <div key={label} style={{ position: "relative" }}>
            <select value={val} onChange={(e) => handleFilter(setter, e.target.value)} style={SELECT_STYLE} aria-label={label}>
              {opts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <ChevronDown size={13} style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", color: "#475569", pointerEvents: "none" }} />
          </div>
        ))}
        {(actionFilter || resourceTypeFilter) && (
          <button type="button" className="btn-secondary" style={{ fontSize: 13, padding: "6px 12px" }} onClick={() => { setActionFilter(""); setResourceTypeFilter(""); setPage(1); }}>
            Clear filters
          </button>
        )}
      </div>

      {/* Table */}
      <div className="card" style={{ overflow: "hidden" }}>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed" }}>
            <colgroup>
              <col style={{ width: 120 }} /><col style={{ width: 160 }} /><col style={{ width: 130 }} />
              <col style={{ width: 140 }} /><col style={{ width: 180 }} /><col style={{ width: 70 }} />
            </colgroup>
            <thead>
              <tr style={{ position: "sticky", top: 0, background: "#0f1724", borderBottom: "1px solid #1e2d47" }}>
                {COL_HEADERS.map((h) => (
                  <th key={h} className="table-header" style={{ padding: "10px 16px", textAlign: "left" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {isLoading
                ? Array.from({ length: 8 }).map((_, i) => (
                  // biome-ignore lint/suspicious/noArrayIndexKey: skeleton rows
                  <tr key={i} style={{ height: 36, borderBottom: "1px solid #1e2d47" }}>
                    {COL_HEADERS.map((h) => (
                      <td key={h} style={{ padding: "0 16px" }}>
                        <span className="animate-pulse" style={{ display: "block", height: 12, background: "#1e2d47", borderRadius: 3, width: h === "Details" ? 20 : "65%" }} />
                      </td>
                    ))}
                  </tr>
                ))
                : entries.length === 0
                  ? (
                    <tr><td colSpan={6} style={{ padding: 48, textAlign: "center" }}>
                      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
                        <div style={{ width: 44, height: 44, borderRadius: "50%", background: "#18243a", display: "flex", alignItems: "center", justifyContent: "center" }}>
                          <Shield size={20} color="#2a3f5e" />
                        </div>
                        <p style={{ fontSize: 14, color: "#475569", margin: 0 }}>
                          {actionFilter || resourceTypeFilter ? "No entries match the current filters." : "No audit entries yet."}
                        </p>
                      </div>
                    </td></tr>
                  )
                  : entries.map((entry) => <AuditRow key={entry.id} entry={entry} />)}
            </tbody>
          </table>
        </div>
        {!isLoading && total > 0 && (
          <div style={{ borderTop: "1px solid #1e2d47", padding: "0 16px" }}>
            <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
          </div>
        )}
      </div>
    </div>
  );
}
