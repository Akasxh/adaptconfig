import { adaptersApi, analyticsApi, configurationsApi, documentsApi } from "@/lib/api";
import type { ConfigSummaryResponse } from "@/types";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  FileText,
  HeartPulse,
  Plug,
  Settings,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { CSSProperties } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

// ── Constants ──────────────────────────────────────────────────────────────

const BRAND = "#2d8fce";
const TEAL = "#0fb89a";
const TEAL_DIM = "#0d9e85";
const GRAY_STATUS = "#475569";
const ERROR_COLOR = "#f87171";

const CHART_TOOLTIP_STYLE: CSSProperties = {
  backgroundColor: "var(--color-bg-raised)",
  border: "1px solid var(--color-border-strong)",
  borderRadius: "6px",
  fontSize: "12px",
  color: "var(--color-text-primary)",
};

const SAMPLE_ACTIVITY: Array<{ name: string; documents: number; configs: number }> = [
  { name: "Mon", documents: 12, configs: 4 },
  { name: "Tue", documents: 19, configs: 7 },
  { name: "Wed", documents: 8, configs: 3 },
  { name: "Thu", documents: 24, configs: 9 },
  { name: "Fri", documents: 16, configs: 6 },
  { name: "Sat", documents: 5, configs: 2 },
  { name: "Sun", documents: 3, configs: 1 },
];

const SAMPLE_THROUGHPUT: Array<{ hour: string; records: number }> = [
  { hour: "00:00", records: 1200 },
  { hour: "04:00", records: 800 },
  { hour: "08:00", records: 2400 },
  { hour: "12:00", records: 3200 },
  { hour: "16:00", records: 2800 },
  { hour: "20:00", records: 1800 },
];

const PIE_FALLBACK = [
  { name: "Active", value: 8, color: TEAL },
  { name: "Inactive", value: 3, color: GRAY_STATUS },
  { name: "Error", value: 1, color: ERROR_COLOR },
];

// ── Helpers ────────────────────────────────────────────────────────────────

function formatLarge(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

function buildPieData(adapters: Array<{ is_active: boolean; status?: string }>) {
  const counts = { Active: 0, Inactive: 0, Error: 0 };
  for (const a of adapters) {
    if (a.status === "error") counts.Error++;
    else if (a.is_active) counts.Active++;
    else counts.Inactive++;
  }
  return Object.entries(counts)
    .filter(([, v]) => v > 0)
    .map(([name, value]) => ({
      name,
      value,
      color: name === "Active" ? TEAL : name === "Error" ? ERROR_COLOR : GRAY_STATUS,
    }));
}

const STATUS_COLORS: Record<string, string> = {
  active: TEAL,
  draft: "#fbbf24",
  deprecated: GRAY_STATUS,
  archived: GRAY_STATUS,
  error: ERROR_COLOR,
};

function statusColor(s: string): string {
  return STATUS_COLORS[s.toLowerCase()] ?? GRAY_STATUS;
}

// ── Sub-components ─────────────────────────────────────────────────────────

interface KpiCardProps {
  title: string;
  value: string | number;
  subtitle: string;
  icon: LucideIcon;
  iconBg: string;
  iconColor: string;
  delay: number;
}

function KpiCard({ title, value, subtitle, icon: Icon, iconBg, iconColor, delay }: KpiCardProps) {
  return (
    <div
      className="card-hover animate-fade-in p-5"
      style={{ animationDelay: `${delay}ms`, animationFillMode: "both" }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="metric-label">{title}</p>
          <p className="metric-value mt-2">{value}</p>
          <p style={{ fontSize: "11px", color: "var(--color-text-muted)", marginTop: "4px" }}>
            {subtitle}
          </p>
        </div>
        <div
          style={{
            borderRadius: "8px",
            padding: "10px",
            backgroundColor: iconBg,
            color: iconColor,
            flexShrink: 0,
          }}
        >
          <Icon size={18} />
        </div>
      </div>
    </div>
  );
}

function ConfigSummaryCard({ summary }: { summary: ConfigSummaryResponse }) {
  const confidencePct = Math.round(summary.avg_confidence * 100);
  const confidenceColor = confidencePct >= 80 ? TEAL : confidencePct >= 50 ? "#fbbf24" : ERROR_COLOR;

  return (
    <div
      className="card animate-fade-in p-5"
      style={{ animationDelay: "200ms", animationFillMode: "both" }}
    >
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 style={{ fontSize: "14px", fontWeight: 600, color: "var(--color-text-primary)" }}>
            Configuration Summary
          </h2>
          <p style={{ fontSize: "11px", color: "var(--color-text-muted)", marginTop: "2px" }}>
            {summary.total} total configuration{summary.total !== 1 ? "s" : ""}
          </p>
        </div>
        <Settings size={16} style={{ color: "var(--color-text-muted)" }} aria-hidden />
      </div>

      <div className="flex flex-wrap items-start gap-6">
        {/* By status */}
        <div style={{ flex: "1 1 160px" }}>
          <p style={{ fontSize: "11px", fontWeight: 600, color: "var(--color-text-muted)", marginBottom: "8px", textTransform: "uppercase", letterSpacing: "0.04em" }}>
            By Status
          </p>
          <div className="space-y-2">
            {Object.entries(summary.by_status).map(([status, count]) => (
              <div key={status} className="flex items-center gap-2">
                <div
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    backgroundColor: statusColor(status),
                    flexShrink: 0,
                  }}
                />
                <span style={{ fontSize: "12px", color: "var(--color-text-secondary)", textTransform: "capitalize", flex: 1 }}>
                  {status}
                </span>
                <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--color-text-primary)", fontVariantNumeric: "tabular-nums" }}>
                  {count}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Avg confidence */}
        <div style={{ flex: "2 1 200px" }}>
          <p style={{ fontSize: "11px", fontWeight: 600, color: "var(--color-text-muted)", marginBottom: "8px", textTransform: "uppercase", letterSpacing: "0.04em" }}>
            Avg. Confidence
          </p>
          <div className="flex items-center gap-3">
            <div
              style={{
                flex: 1,
                height: 6,
                borderRadius: 3,
                backgroundColor: "var(--color-border)",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  height: "100%",
                  width: `${confidencePct}%`,
                  borderRadius: 3,
                  backgroundColor: confidenceColor,
                  transition: "width 0.4s ease",
                }}
              />
            </div>
            <span style={{ fontSize: "13px", fontWeight: 700, color: confidenceColor, fontVariantNumeric: "tabular-nums", minWidth: "36px" }}>
              {confidencePct}%
            </span>
          </div>
          <p style={{ fontSize: "11px", color: "var(--color-text-muted)", marginTop: "6px" }}>
            across all mapped configurations
          </p>
        </div>
      </div>
    </div>
  );
}

// ── Dashboard ──────────────────────────────────────────────────────────────

export default function Dashboard() {
  const adaptersQ = useQuery({ queryKey: ["adapters"], queryFn: () => adaptersApi.list() });
  const documentsQ = useQuery({ queryKey: ["documents"], queryFn: () => documentsApi.list() });
  const configsQ = useQuery({
    queryKey: ["configurations"],
    queryFn: () => configurationsApi.list(),
  });
  const analyticsQ = useQuery({
    queryKey: ["analytics", "dashboard"],
    queryFn: () => analyticsApi.dashboard(),
    retry: false,
  });
  const configSummaryQ = useQuery({
    queryKey: ["configurations", "summary"],
    queryFn: () => configurationsApi.getSummary(),
    retry: false,
  });

  // Derived counts
  const adapterList = adaptersQ.data?.data?.adapters ?? [];
  const adapterTotal = adaptersQ.data?.data?.total ?? 0;
  const activeAdapters = adapterList.filter((a) => a.is_active).length;
  const docCount = documentsQ.data?.data?.length ?? 0;
  const configCount = configsQ.data?.data?.length ?? 0;

  // Analytics
  const analyticsData = analyticsQ.data?.data;
  const totalWarnings = analyticsData?.total_warnings ?? 0;
  const totalProcessed = analyticsData?.total_processed ?? 0;

  // Health score from backend analytics
  const healthScore = Math.round(analyticsData?.health_score ?? 100);
  const healthColor = healthScore >= 80 ? TEAL : healthScore >= 50 ? "#fbbf24" : ERROR_COLOR;
  const healthBg =
    healthScore >= 80
      ? "var(--color-teal-subtle)"
      : healthScore >= 50
        ? "rgba(251,191,36,0.12)"
        : "rgba(248,113,113,0.12)";

  // Chart data with sample fallback
  const rawActivity = analyticsData?.weekly_activity;
  const activityData =
    rawActivity && rawActivity.length > 0
      ? rawActivity.map((d) => ({ name: d.name, documents: d.documents, configs: d.simulations }))
      : SAMPLE_ACTIVITY;
  const activityIsSample = !rawActivity || rawActivity.length === 0;

  const rawThroughput = analyticsData?.throughput;
  const throughputData = rawThroughput && rawThroughput.length > 0 ? rawThroughput : SAMPLE_THROUGHPUT;
  const throughputIsSample = !rawThroughput || rawThroughput.length === 0;

  // Pie data
  const pieSlices = buildPieData(adapterList);
  const pieData = pieSlices.length > 0 ? pieSlices : PIE_FALLBACK;
  const pieIsSample = pieSlices.length === 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="animate-fade-in">
        <h1 style={{ fontSize: "22px", fontWeight: 700, color: "var(--color-text-primary)" }}>
          Dashboard
        </h1>
        <p style={{ marginTop: "2px", fontSize: "13px", color: "var(--color-text-secondary)" }}>
          Integration platform overview
        </p>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <KpiCard
          title="Active Adapters"
          value={activeAdapters}
          subtitle={adapterTotal > 0 ? `${adapterTotal} total connectors` : "Integration connectors"}
          icon={Plug}
          iconBg="var(--color-brand-subtle)"
          iconColor="var(--color-brand-light)"
          delay={0}
        />
        <KpiCard
          title="Documents"
          value={docCount}
          subtitle="Uploaded & processed"
          icon={FileText}
          iconBg="var(--color-teal-subtle)"
          iconColor={TEAL}
          delay={60}
        />
        <KpiCard
          title="Configurations"
          value={configCount}
          subtitle="Active mappings"
          icon={Settings}
          iconBg="rgba(251,191,36,0.10)"
          iconColor="#fbbf24"
          delay={120}
        />
        <KpiCard
          title="Health Score"
          value={`${healthScore}%`}
          subtitle={`${totalWarnings} warning${totalWarnings !== 1 ? "s" : ""} detected`}
          icon={HeartPulse}
          iconBg={healthBg}
          iconColor={healthColor}
          delay={180}
        />
      </div>

      {/* Configuration Summary */}
      {configSummaryQ.data?.data && (
        <ConfigSummaryCard summary={configSummaryQ.data.data} />
      )}

      {/* Charts row: activity + pie */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Weekly Activity */}
        <div
          className="card animate-fade-in p-5 lg:col-span-2"
          style={{ animationDelay: "220ms", animationFillMode: "both" }}
        >
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 style={{ fontSize: "14px", fontWeight: 600, color: "var(--color-text-primary)" }}>
                Weekly Activity
              </h2>
              <p style={{ fontSize: "11px", color: "var(--color-text-muted)", marginTop: "2px" }}>
                Documents &amp; configurations processed
                {activityIsSample && (
                  <span style={{ color: "var(--color-warning-text)", marginLeft: "6px" }}>
                    · sample data
                  </span>
                )}
              </p>
            </div>
            <Activity size={16} style={{ color: "var(--color-text-muted)" }} aria-hidden />
          </div>
          <div style={{ height: "220px" }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={activityData} barGap={4}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" vertical={false} />
                <XAxis dataKey="name" stroke="var(--color-text-muted)" fontSize={11} tickLine={false} axisLine={false} />
                <YAxis stroke="var(--color-text-muted)" fontSize={11} tickLine={false} axisLine={false} width={28} />
                <Tooltip contentStyle={CHART_TOOLTIP_STYLE} cursor={{ fill: "rgba(255,255,255,0.03)" }} />
                <Bar dataKey="documents" fill={BRAND} radius={[3, 3, 0, 0]} name="Documents" />
                <Bar dataKey="configs" fill={TEAL_DIM} radius={[3, 3, 0, 0]} name="Configs" />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-3 flex items-center gap-4">
            <div className="flex items-center gap-1.5">
              <span style={{ width: 10, height: 10, borderRadius: 2, backgroundColor: BRAND, display: "inline-block" }} />
              <span style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>Documents</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span style={{ width: 10, height: 10, borderRadius: 2, backgroundColor: TEAL_DIM, display: "inline-block" }} />
              <span style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>Configs</span>
            </div>
          </div>
        </div>

        {/* Adapter Status donut */}
        <div
          className="card animate-fade-in p-5"
          style={{ animationDelay: "260ms", animationFillMode: "both" }}
        >
          <div className="mb-4">
            <h2 style={{ fontSize: "14px", fontWeight: 600, color: "var(--color-text-primary)" }}>
              Adapter Status
            </h2>
            <p style={{ fontSize: "11px", color: "var(--color-text-muted)", marginTop: "2px" }}>
              {pieIsSample ? "Distribution · sample data" : "Current distribution"}
            </p>
          </div>
          <div style={{ height: "160px" }}>
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={pieData}
                  cx="50%"
                  cy="50%"
                  innerRadius={48}
                  outerRadius={72}
                  paddingAngle={3}
                  dataKey="value"
                  strokeWidth={0}
                >
                  {pieData.map((entry) => (
                    <Cell key={entry.name} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-3 space-y-2">
            {pieData.map((s) => (
              <div key={s.name} className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div style={{ width: 8, height: 8, borderRadius: "50%", backgroundColor: s.color }} />
                  <span style={{ fontSize: "12px", color: "var(--color-text-secondary)" }}>
                    {s.name}
                  </span>
                </div>
                <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--color-text-primary)", fontVariantNumeric: "tabular-nums" }}>
                  {s.value}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Data Throughput area chart */}
      <div
        className="card animate-fade-in p-5"
        style={{ animationDelay: "300ms", animationFillMode: "both" }}
      >
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 style={{ fontSize: "14px", fontWeight: 600, color: "var(--color-text-primary)" }}>
              Data Throughput
            </h2>
            <p style={{ fontSize: "11px", color: "var(--color-text-muted)", marginTop: "2px" }}>
              Records processed per hour
              {throughputIsSample && (
                <span style={{ color: "var(--color-warning-text)", marginLeft: "6px" }}>
                  · sample data
                </span>
              )}
            </p>
          </div>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-1.5">
              <CheckCircle2 size={13} style={{ color: TEAL }} aria-hidden />
              <span style={{ fontSize: "12px", color: "var(--color-text-secondary)" }}>
                {formatLarge(totalProcessed)} processed
              </span>
            </div>
            {totalWarnings > 0 && (
              <div className="flex items-center gap-1.5">
                <AlertTriangle size={13} style={{ color: "var(--color-warning-text)" }} aria-hidden />
                <span style={{ fontSize: "12px", color: "var(--color-text-secondary)" }}>
                  {totalWarnings} warnings
                </span>
              </div>
            )}
          </div>
        </div>
        <div style={{ height: "200px" }}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={throughputData}>
              <defs>
                <linearGradient id="tpGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={BRAND} stopOpacity={0.25} />
                  <stop offset="95%" stopColor={BRAND} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" vertical={false} />
              <XAxis dataKey="hour" stroke="var(--color-text-muted)" fontSize={11} tickLine={false} axisLine={false} />
              <YAxis stroke="var(--color-text-muted)" fontSize={11} tickLine={false} axisLine={false} width={40} />
              <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
              <Area
                type="monotone"
                dataKey="records"
                stroke={BRAND}
                strokeWidth={2}
                fill="url(#tpGrad)"
                name="Records"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
