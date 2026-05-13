import { adaptersApi } from "@/lib/api";
import type { Adapter, AdapterVersion } from "@/types";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ChevronDown, GitBranch, Globe, Plug, RefreshCw, Shield, X, Zap } from "lucide-react";
import { useState } from "react";

// ── Constants ──────────────────────────────────────────────────────────────

const CATEGORY_PILLS = [
  { value: "", label: "All" },
  { value: "bureau", label: "Bureau" },
  { value: "kyc", label: "KYC" },
  { value: "gst", label: "GST" },
  { value: "payment", label: "Payment" },
  { value: "fraud", label: "Fraud" },
  { value: "notification", label: "Notification" },
  { value: "open_banking", label: "Open Banking" },
] as const;

type CategoryBadge =
  | "badge-blue"
  | "badge-teal"
  | "badge-yellow"
  | "badge-green"
  | "badge-red"
  | "badge-gray";

const CATEGORY_BADGE: Record<string, CategoryBadge> = {
  bureau: "badge-blue",
  kyc: "badge-teal",
  gst: "badge-yellow",
  payment: "badge-green",
  fraud: "badge-red",
  notification: "badge-gray",
  open_banking: "badge-blue",
};

const METHOD_STYLE: Record<string, { bg: string; color: string }> = {
  GET: { bg: "rgba(15,184,154,0.12)", color: "#2dd4bf" },
  POST: { bg: "rgba(29,111,164,0.15)", color: "#60a5fa" },
  PUT: { bg: "rgba(217,119,6,0.12)", color: "#fbbf24" },
  PATCH: { bg: "rgba(217,119,6,0.12)", color: "#fbbf24" },
  DELETE: { bg: "rgba(220,38,38,0.12)", color: "#f87171" },
};

// ── Helpers ────────────────────────────────────────────────────────────────

function categoryBadge(category: string): CategoryBadge {
  return CATEGORY_BADGE[category] ?? "badge-gray";
}

function totalEndpoints(adapter: Adapter): number {
  return adapter.versions.reduce((acc, v) => acc + v.endpoints.length, 0);
}

function latestVersion(adapter: Adapter): string | undefined {
  return adapter.versions[adapter.versions.length - 1]?.version;
}

// ── Skeleton ───────────────────────────────────────────────────────────────

function SkeletonCard() {
  return (
    <div className="card animate-pulse" style={{ padding: "1.25rem" }} aria-hidden="true">
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          marginBottom: "0.75rem",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          <div
            style={{
              width: "2.25rem",
              height: "2.25rem",
              borderRadius: "0.5rem",
              background: "var(--color-bg-raised)",
            }}
          />
          <div>
            <div
              style={{
                width: "7rem",
                height: "0.875rem",
                borderRadius: "0.25rem",
                background: "var(--color-bg-raised)",
                marginBottom: "0.4rem",
              }}
            />
            <div
              style={{
                width: "4rem",
                height: "0.625rem",
                borderRadius: "0.25rem",
                background: "var(--color-bg-raised)",
              }}
            />
          </div>
        </div>
        <div
          style={{
            width: "3.5rem",
            height: "1.25rem",
            borderRadius: "9999px",
            background: "var(--color-bg-raised)",
          }}
        />
      </div>
      <div
        style={{
          marginBottom: "0.25rem",
          height: "0.75rem",
          borderRadius: "0.25rem",
          background: "var(--color-bg-raised)",
        }}
      />
      <div
        style={{
          width: "75%",
          height: "0.75rem",
          borderRadius: "0.25rem",
          background: "var(--color-bg-raised)",
        }}
      />
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          marginTop: "1rem",
          paddingTop: "0.75rem",
          borderTop: "1px solid var(--color-border)",
        }}
      >
        <div
          style={{
            width: "5rem",
            height: "0.625rem",
            borderRadius: "0.25rem",
            background: "var(--color-bg-raised)",
          }}
        />
        <div
          style={{
            width: "2.5rem",
            height: "0.625rem",
            borderRadius: "0.25rem",
            background: "var(--color-bg-raised)",
          }}
        />
      </div>
    </div>
  );
}

// ── Version panel ──────────────────────────────────────────────────────────

function VersionPanel({ version, adapterId }: { version: AdapterVersion; adapterId: string }) {
  const [open, setOpen] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const statusCls = version.status === "active" ? "badge-green" : "badge-gray";

  const { data: deprData } = useQuery({
    queryKey: ["adapter-deprecation", adapterId, version.version],
    queryFn: () => adaptersApi.deprecation(adapterId, version.version),
    enabled: version.status === "deprecated",
    staleTime: 5 * 60 * 1000,
  });

  return (
    <div
      style={{
        border: "1px solid var(--color-border)",
        borderRadius: "0.5rem",
        overflow: "hidden",
      }}
    >
      <button
        type="button"
        style={{
          display: "flex",
          width: "100%",
          alignItems: "center",
          gap: "0.625rem",
          padding: "0.625rem 1rem",
          textAlign: "left",
          background: "transparent",
          cursor: "pointer",
          transition: "background-color 120ms ease",
        }}
        onMouseEnter={(e) => {
          (e.currentTarget as HTMLButtonElement).style.backgroundColor = "var(--color-bg-raised)";
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLButtonElement).style.backgroundColor = "transparent";
        }}
        onClick={() => setOpen((o) => !o)}
      >
        <span
          className="mono"
          style={{ fontWeight: 600, fontSize: "0.8125rem", color: "var(--color-text-primary)" }}
        >
          v{version.version}
        </span>
        <span className={statusCls}>{version.status}</span>
        {version.auth_type && (
          <span
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.25rem",
              fontSize: "0.6875rem",
              color: "var(--color-text-muted)",
              marginLeft: "0.25rem",
            }}
          >
            <Shield size={11} />
            {version.auth_type}
          </span>
        )}
        {version.base_url && (
          <code
            className="mono"
            style={{
              marginLeft: "auto",
              fontSize: "0.6875rem",
              color: "var(--color-brand-light)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              maxWidth: "11rem",
            }}
          >
            {version.base_url}
          </code>
        )}
        <ChevronDown
          size={13}
          style={{
            color: "var(--color-text-muted)",
            flexShrink: 0,
            transform: open ? "rotate(180deg)" : "rotate(0deg)",
            transition: "transform 150ms ease",
          }}
        />
      </button>

      {open && (
        <div
          style={{
            borderTop: "1px solid var(--color-border)",
            background: "rgba(15,23,36,0.6)",
            padding: "0.75rem 1rem",
            display: "flex",
            flexDirection: "column",
            gap: "0.75rem",
          }}
        >
          {/* Deprecation warning banner */}
          {deprData?.data && deprData.data.status === "deprecated" && (
            <div
              role="alert"
              style={{
                padding: "0.625rem 0.875rem",
                borderRadius: "0.375rem",
                border: "1px solid rgba(245,158,11,0.3)",
                background: "rgba(245,158,11,0.08)",
                display: "flex",
                flexDirection: "column",
                gap: "0.375rem",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                <AlertTriangle size={13} style={{ color: "#f59e0b", flexShrink: 0 }} />
                <span style={{ fontSize: "0.75rem", fontWeight: 600, color: "#fbbf24" }}>
                  Deprecated
                  {deprData.data.sunset_date &&
                    ` — sunset ${deprData.data.sunset_date}${
                      deprData.data.days_until_sunset != null
                        ? ` (${deprData.data.days_until_sunset} days remaining)`
                        : ""
                    }`}
                </span>
              </div>
              {deprData.data.replacement_version && (
                <span
                  style={{
                    fontSize: "0.6875rem",
                    color: "var(--color-text-muted)",
                    paddingLeft: "1.375rem",
                  }}
                >
                  Replacement:{" "}
                  <span
                    className="mono"
                    style={{ color: "var(--color-brand-light)", fontWeight: 600 }}
                  >
                    v{deprData.data.replacement_version}
                  </span>
                </span>
              )}
              {deprData.data.migration_guide.length > 0 && (
                <div style={{ paddingLeft: "1.375rem" }}>
                  <button
                    type="button"
                    onClick={() => setGuideOpen((g) => !g)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "0.25rem",
                      fontSize: "0.6875rem",
                      color: "#f59e0b",
                      background: "transparent",
                      border: "none",
                      cursor: "pointer",
                      padding: 0,
                      fontWeight: 500,
                    }}
                  >
                    <ChevronDown
                      size={11}
                      style={{
                        transform: guideOpen ? "rotate(180deg)" : "rotate(0deg)",
                        transition: "transform 120ms ease",
                      }}
                    />
                    Migration Guide ({deprData.data.migration_guide.length} step
                    {deprData.data.migration_guide.length !== 1 ? "s" : ""})
                  </button>
                  {guideOpen && (
                    <div
                      style={{
                        marginTop: "0.375rem",
                        display: "flex",
                        flexDirection: "column",
                        gap: "0.25rem",
                      }}
                    >
                      {deprData.data.migration_guide.map((step, i) => (
                        <div
                          key={`${step.action}-${step.description ?? ""}`}
                          style={{
                            fontSize: "0.6875rem",
                            color: "var(--color-text-secondary)",
                            display: "flex",
                            gap: "0.375rem",
                          }}
                        >
                          <span
                            style={{
                              color: "#f59e0b",
                              fontWeight: 600,
                              flexShrink: 0,
                              minWidth: "0.75rem",
                            }}
                          >
                            {i + 1}.
                          </span>
                          <span>
                            <span style={{ fontWeight: 600, color: "var(--color-text-primary)" }}>
                              {step.action}
                            </span>
                            {step.description && (
                              <span style={{ color: "var(--color-text-muted)" }}>
                                {" — "}
                                {step.description}
                              </span>
                            )}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {version.endpoints.length === 0 ? (
            <p style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>
              No endpoints defined.
            </p>
          ) : (
            <>
              <p className="section-label" style={{ marginBottom: "0.5rem" }}>
                Endpoints ({version.endpoints.length})
              </p>
              <div style={{ display: "flex", flexDirection: "column", gap: "0.375rem" }}>
                {version.endpoints.map((ep) => {
                  const ms = METHOD_STYLE[ep.method] ?? {
                    bg: "rgba(71,85,105,0.15)",
                    color: "#94a3b8",
                  };
                  return (
                    <div
                      key={`${ep.method}:${ep.path}`}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "0.5rem",
                        fontSize: "0.75rem",
                      }}
                    >
                      <span
                        style={{
                          display: "inline-block",
                          padding: "0.125rem 0.375rem",
                          borderRadius: "0.25rem",
                          background: ms.bg,
                          color: ms.color,
                          fontWeight: 700,
                          fontSize: "0.625rem",
                          letterSpacing: "0.05em",
                          textTransform: "uppercase",
                          flexShrink: 0,
                          minWidth: "3rem",
                          textAlign: "center",
                        }}
                      >
                        {ep.method}
                      </span>
                      <code
                        className="mono"
                        style={{ color: "var(--color-text-secondary)", fontSize: "0.75rem" }}
                      >
                        {ep.path}
                      </code>
                      {ep.description && (
                        <span
                          style={{
                            color: "var(--color-text-muted)",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {ep.description}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ── Adapter detail modal ───────────────────────────────────────────────────

function AdapterModal({ adapter, onClose }: { adapter: Adapter; onClose: () => void }) {
  const badge = categoryBadge(adapter.category);
  const epCount = totalEndpoints(adapter);

  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: modal overlay, keyboard handled via close button
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 50,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "1rem",
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: "rgba(0,0,0,0.65)",
          backdropFilter: "blur(4px)",
        }}
      />
      <div
        className="animate-fade-in"
        style={{
          position: "relative",
          zIndex: 10,
          width: "100%",
          maxWidth: "42rem",
          maxHeight: "88vh",
          display: "flex",
          flexDirection: "column",
          borderRadius: "0.75rem",
          border: "1px solid var(--color-border-strong)",
          background: "var(--color-bg-elevated)",
          boxShadow: "0 24px 48px rgba(0,0,0,0.5)",
        }}
      >
        {/* Modal header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.875rem",
            padding: "1.25rem 1.5rem",
            borderBottom: "1px solid var(--color-border)",
          }}
        >
          <div
            style={{
              padding: "0.5rem",
              borderRadius: "0.5rem",
              background: "var(--color-brand-subtle)",
              color: "var(--color-brand-light)",
              flexShrink: 0,
            }}
          >
            <Plug size={18} />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h2
              style={{
                fontSize: "0.9375rem",
                fontWeight: 600,
                color: "var(--color-text-primary)",
                margin: 0,
              }}
            >
              {adapter.name}
            </h2>
            <div
              style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginTop: "0.25rem" }}
            >
              <span className={badge}>{adapter.category.replaceAll("_", " ")}</span>
              <span
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.3rem",
                  fontSize: "0.6875rem",
                  color: adapter.is_active ? "#34d399" : "var(--color-text-muted)",
                }}
              >
                <span
                  style={{
                    width: "5px",
                    height: "5px",
                    borderRadius: "50%",
                    background: adapter.is_active ? "#34d399" : "#475569",
                    display: "inline-block",
                  }}
                />
                {adapter.is_active ? "Active" : "Inactive"}
              </span>
            </div>
          </div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "1.25rem",
              fontSize: "0.6875rem",
              color: "var(--color-text-muted)",
            }}
          >
            <span style={{ display: "flex", alignItems: "center", gap: "0.25rem" }}>
              <GitBranch size={11} />
              {adapter.versions.length} version{adapter.versions.length !== 1 ? "s" : ""}
            </span>
            <span style={{ display: "flex", alignItems: "center", gap: "0.25rem" }}>
              <Zap size={11} />
              {epCount} endpoint{epCount !== 1 ? "s" : ""}
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            style={{
              color: "var(--color-text-muted)",
              cursor: "pointer",
              background: "transparent",
              border: "none",
              lineHeight: 0,
              padding: "0.25rem",
            }}
          >
            <X size={18} />
          </button>
        </div>

        {/* Modal body */}
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: "1.5rem",
            display: "flex",
            flexDirection: "column",
            gap: "1.25rem",
          }}
        >
          {adapter.description && (
            <p
              style={{
                fontSize: "0.8125rem",
                color: "var(--color-text-secondary)",
                lineHeight: "1.6",
                margin: 0,
              }}
            >
              {adapter.description}
            </p>
          )}

          <div>
            <p className="section-label" style={{ marginBottom: "0.625rem" }}>
              Versions
            </p>
            {adapter.versions.length > 0 ? (
              <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                {adapter.versions.map((v) => (
                  <VersionPanel key={v.id} version={v} adapterId={adapter.id} />
                ))}
              </div>
            ) : (
              <p style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
                No versions available.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Adapter card ───────────────────────────────────────────────────────────

function AdapterCard({ adapter, onClick }: { adapter: Adapter; onClick: () => void }) {
  const badge = categoryBadge(adapter.category);
  const epCount = totalEndpoints(adapter);
  const ver = latestVersion(adapter);

  return (
    <button
      type="button"
      className="card-hover"
      onClick={onClick}
      style={{
        display: "block",
        width: "100%",
        textAlign: "left",
        padding: "1.25rem",
        cursor: "pointer",
      }}
    >
      {/* Top row */}
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          marginBottom: "0.75rem",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", minWidth: 0 }}>
          <div
            style={{
              padding: "0.5rem",
              borderRadius: "0.5rem",
              background: "var(--color-brand-subtle)",
              color: "var(--color-brand-light)",
              flexShrink: 0,
            }}
          >
            <Plug size={16} />
          </div>
          <div style={{ minWidth: 0 }}>
            <p
              style={{
                fontSize: "0.9375rem",
                fontWeight: 600,
                color: "var(--color-text-primary)",
                margin: 0,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {adapter.name}
            </p>
            <span className={badge} style={{ marginTop: "0.25rem" }}>
              {adapter.category.replaceAll("_", " ")}
            </span>
          </div>
        </div>
        {/* Status dot + label */}
        <span
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.3rem",
            fontSize: "0.6875rem",
            fontWeight: 500,
            color: adapter.is_active ? "#34d399" : "var(--color-text-muted)",
            flexShrink: 0,
            marginLeft: "0.5rem",
          }}
        >
          <span
            style={{
              width: "6px",
              height: "6px",
              borderRadius: "50%",
              background: adapter.is_active ? "#34d399" : "#475569",
              display: "inline-block",
            }}
          />
          {adapter.is_active ? "Active" : "Inactive"}
        </span>
      </div>

      {/* Description */}
      <p
        style={{
          fontSize: "0.8125rem",
          color: "var(--color-text-secondary)",
          lineHeight: "1.5",
          margin: 0,
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}
      >
        {adapter.description ?? "No description provided."}
      </p>

      {/* Footer */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginTop: "1rem",
          paddingTop: "0.75rem",
          borderTop: "1px solid var(--color-border)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.875rem" }}>
          <span
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.3rem",
              fontSize: "0.6875rem",
              color: "var(--color-text-muted)",
            }}
          >
            <GitBranch size={11} />
            {adapter.versions.length} ver.
          </span>
          <span
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.3rem",
              fontSize: "0.6875rem",
              color: "var(--color-text-muted)",
            }}
          >
            <Globe size={11} />
            {epCount} ep.
          </span>
        </div>
        {ver && (
          <span
            className="mono"
            style={{ fontSize: "0.6875rem", color: "var(--color-text-muted)" }}
          >
            v{ver}
          </span>
        )}
      </div>
    </button>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function Adapters() {
  const [categoryFilter, setCategoryFilter] = useState("");
  const [selected, setSelected] = useState<Adapter | null>(null);

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["adapters", categoryFilter],
    queryFn: () => adaptersApi.list(categoryFilter || undefined),
  });

  const adapters: Adapter[] = data?.data?.adapters ?? [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      {/* Page header */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
        <div>
          <h1
            style={{
              fontSize: "1.5rem",
              fontWeight: 700,
              color: "var(--color-text-primary)",
              margin: 0,
            }}
          >
            Adapters
          </h1>
          <p
            style={{
              marginTop: "0.25rem",
              fontSize: "0.8125rem",
              color: "var(--color-text-secondary)",
            }}
          >
            Integration connectors and data sources
          </p>
        </div>
        <button
          type="button"
          className="btn-secondary"
          onClick={() => refetch()}
          aria-label="Refresh adapters"
        >
          <RefreshCw
            size={14}
            style={{ animation: isFetching ? "spin 1s linear infinite" : undefined }}
          />
          Refresh
        </button>
      </div>

      {/* Category filter bar */}
      <fieldset
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "0.5rem",
          border: "none",
          padding: 0,
          margin: 0,
        }}
      >
        <legend className="sr-only">Filter by category</legend>
        {CATEGORY_PILLS.map((pill) => {
          const active = categoryFilter === pill.value;
          return (
            <button
              key={pill.value}
              type="button"
              onClick={() => setCategoryFilter(pill.value)}
              style={{
                padding: "0.375rem 0.875rem",
                borderRadius: "9999px",
                fontSize: "0.75rem",
                fontWeight: 500,
                cursor: "pointer",
                transition:
                  "background-color 120ms ease, color 120ms ease, border-color 120ms ease",
                background: active ? "var(--color-brand)" : "transparent",
                color: active ? "#fff" : "var(--color-text-secondary)",
                border: active
                  ? "1px solid var(--color-brand)"
                  : "1px solid var(--color-border-strong)",
              }}
            >
              {pill.label}
            </button>
          );
        })}
      </fieldset>

      {/* Error banner */}
      {error && (
        <div
          role="alert"
          style={{
            padding: "0.875rem 1rem",
            borderRadius: "0.5rem",
            border: "1px solid rgba(220,38,38,0.25)",
            background: "rgba(220,38,38,0.06)",
            fontSize: "0.8125rem",
            color: "var(--color-error-text)",
          }}
        >
          Failed to load adapters. Please try refreshing.
        </div>
      )}

      {/* Grid */}
      {isLoading ? (
        <div
          aria-label="Loading adapters"
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(17rem, 1fr))",
            gap: "1rem",
          }}
        >
          {Array.from({ length: 6 }).map((_, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: skeleton placeholders
            <SkeletonCard key={i} />
          ))}
        </div>
      ) : adapters.length === 0 ? (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            padding: "4rem 1rem",
            borderRadius: "0.75rem",
            border: "1px solid var(--color-border)",
            background: "var(--color-bg-elevated)",
            textAlign: "center",
          }}
        >
          <div
            style={{
              padding: "1rem",
              borderRadius: "9999px",
              background: "var(--color-bg-raised)",
              marginBottom: "1rem",
            }}
          >
            <Plug size={24} style={{ color: "var(--color-text-muted)" }} />
          </div>
          <p
            style={{
              fontSize: "0.9375rem",
              fontWeight: 600,
              color: "var(--color-text-secondary)",
              margin: 0,
            }}
          >
            No adapters found
          </p>
          <p
            style={{
              marginTop: "0.25rem",
              fontSize: "0.8125rem",
              color: "var(--color-text-muted)",
            }}
          >
            {categoryFilter
              ? "Try a different category filter."
              : "Connect your first data source to get started."}
          </p>
        </div>
      ) : (
        <div
          className="animate-fade-in"
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(17rem, 1fr))",
            gap: "1rem",
          }}
        >
          {adapters.map((adapter) => (
            <AdapterCard key={adapter.id} adapter={adapter} onClick={() => setSelected(adapter)} />
          ))}
        </div>
      )}

      {/* Detail modal */}
      {selected && <AdapterModal adapter={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
