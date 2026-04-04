import { configurationsApi, simulationsApi } from "@/lib/api";
import type { Configuration, Simulation, SimulationStepResult } from "@/types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, BarChart3, ChevronDown, FlaskConical, Loader2, Play, Zap } from "lucide-react";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const C = {
  base: "#0a0f1a",
  elevated: "#0f1724",
  raised: "#18243a",
  border: "#1e2d47",
  borderHi: "#2a3f5e",
  brand: "#1d6fa4",
  brandHi: "#2d8fce",
  teal: "#0fb89a",
  red: "#ef4444",
  text: "#e2e8f0",
  muted: "#64748b",
  mutedHi: "#94a3b8",
};

function formatDuration(ms?: number): string {
  if (!ms) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

function StatusDot({ passed }: { passed: boolean }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: 8,
        height: 8,
        borderRadius: "50%",
        background: passed ? C.teal : C.red,
        flexShrink: 0,
      }}
    />
  );
}

function TestTypeBadge({ type }: { type: string }) {
  const colors: Record<string, string> = {
    full: C.brand,
    smoke: "#d97706",
    integration: "#7c3aed",
  };
  return (
    <span
      style={{
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: "0.06em",
        textTransform: "uppercase",
        color: colors[type] ?? C.muted,
        background: `${colors[type] ?? C.muted}18`,
        border: `1px solid ${colors[type] ?? C.muted}40`,
        borderRadius: 4,
        padding: "2px 7px",
      }}
    >
      {type}
    </span>
  );
}

function StepRow({ step }: { step: SimulationStepResult }) {
  const [open, setOpen] = useState(false);
  const passed = step.status === "passed" || step.status === "pass";
  const pct = Math.round((step.confidence_score ?? 0) * 100);
  const barColor = pct >= 70 ? C.teal : pct >= 40 ? "#d97706" : C.red;

  return (
    <div style={{ border: `1px solid ${C.border}`, borderRadius: 6, overflow: "hidden" }}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          width: "100%",
          padding: "10px 14px",
          background: "transparent",
          border: "none",
          cursor: "pointer",
          textAlign: "left",
        }}
      >
        <StatusDot passed={passed} />
        <span style={{ flex: 1, fontSize: 13, color: C.text }}>{step.step_name}</span>
        <span style={{ fontSize: 11, color: C.muted }}>{formatDuration(step.duration_ms)}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 6, width: 100 }}>
          <div
            style={{
              flex: 1,
              height: 4,
              borderRadius: 2,
              background: C.raised,
              overflow: "hidden",
            }}
          >
            <div style={{ width: `${pct}%`, height: "100%", background: barColor, borderRadius: 2 }} />
          </div>
          <span style={{ fontSize: 11, color: C.muted, fontVariantNumeric: "tabular-nums" }}>
            {pct}%
          </span>
        </div>
        <ChevronDown
          style={{
            width: 14,
            height: 14,
            color: C.muted,
            transform: open ? "rotate(180deg)" : "none",
            transition: "transform 0.15s",
          }}
        />
      </button>

      {open && (
        <div
          style={{
            borderTop: `1px solid ${C.border}`,
            background: C.base,
            padding: "12px 14px",
            display: "flex",
            flexDirection: "column",
            gap: 10,
          }}
        >
          {step.error_message && (
            <div
              style={{
                display: "flex",
                gap: 8,
                padding: 10,
                borderRadius: 6,
                border: `1px solid ${C.red}30`,
                background: `${C.red}08`,
              }}
            >
              <AlertCircle style={{ width: 14, height: 14, color: C.red, flexShrink: 0, marginTop: 1 }} />
              <p style={{ fontSize: 12, color: "#fca5a5" }}>{step.error_message}</p>
            </div>
          )}
          {Object.keys(step.request_payload ?? {}).length > 0 && (
            <div>
              <p style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.06em", textTransform: "uppercase", color: C.muted, marginBottom: 6 }}>
                Request
              </p>
              <pre
                className="mono"
                style={{
                  background: "#060b12",
                  border: `1px solid ${C.border}`,
                  borderRadius: 6,
                  padding: "10px 12px",
                  fontSize: 11,
                  color: C.mutedHi,
                  overflowX: "auto",
                  margin: 0,
                }}
              >
                {JSON.stringify(step.request_payload, null, 2)}
              </pre>
            </div>
          )}
          {Object.keys(step.actual_response ?? {}).length > 0 && (
            <div>
              <p style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.06em", textTransform: "uppercase", color: C.muted, marginBottom: 6 }}>
                Response
              </p>
              <pre
                className="mono"
                style={{
                  background: "#060b12",
                  border: `1px solid ${C.border}`,
                  borderRadius: 6,
                  padding: "10px 12px",
                  fontSize: 11,
                  color: C.mutedHi,
                  overflowX: "auto",
                  margin: 0,
                }}
              >
                {JSON.stringify(step.actual_response, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SimRow({ sim, configName }: { sim: Simulation; configName?: string }) {
  const [open, setOpen] = useState(false);
  const passed = sim.status === "passed";
  const successPct = sim.total_tests > 0 ? Math.round((sim.passed_tests / sim.total_tests) * 100) : 0;

  return (
    <div
      style={{
        border: `1px solid ${C.border}`,
        borderRadius: 8,
        background: C.elevated,
        overflow: "hidden",
      }}
    >
      <button
        type="button"
        onClick={() => setOpen(!open)}
        style={{
          display: "grid",
          gridTemplateColumns: "auto 1fr auto auto auto auto auto",
          alignItems: "center",
          gap: 14,
          width: "100%",
          padding: "14px 18px",
          background: "transparent",
          border: "none",
          cursor: "pointer",
          textAlign: "left",
        }}
      >
        <StatusDot passed={passed} />
        <div>
          <p style={{ fontSize: 13, fontWeight: 600, color: C.text }}>
            {configName ?? `Config ${sim.configuration_id.slice(0, 8)}`}
          </p>
          <p style={{ fontSize: 11, color: C.muted, marginTop: 2 }}>
            {new Date(sim.created_at).toLocaleString()}
          </p>
        </div>
        <TestTypeBadge type={sim.test_type} />
        <span
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: successPct >= 90 ? C.teal : successPct >= 70 ? "#d97706" : C.red,
            minWidth: 40,
            textAlign: "right",
          }}
        >
          {successPct}%
        </span>
        <span style={{ fontSize: 12, color: C.muted }}>
          {sim.passed_tests}/{sim.total_tests} passed
        </span>
        <span style={{ fontSize: 12, color: C.muted }}>{formatDuration(sim.duration_ms)}</span>
        <ChevronDown
          style={{
            width: 14,
            height: 14,
            color: C.muted,
            transform: open ? "rotate(180deg)" : "none",
            transition: "transform 0.15s",
          }}
        />
      </button>

      {open && (
        <div
          style={{
            borderTop: `1px solid ${C.border}`,
            padding: "14px 18px",
            display: "flex",
            flexDirection: "column",
            gap: 8,
          }}
        >
          {sim.steps && sim.steps.length > 0 ? (
            <>
              <p
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  letterSpacing: "0.06em",
                  textTransform: "uppercase",
                  color: C.muted,
                  marginBottom: 4,
                }}
              >
                Steps ({sim.steps.length})
              </p>
              {sim.steps.map((step, i) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: steps lack stable id
                <StepRow key={i} step={step} />
              ))}
            </>
          ) : (
            <p style={{ fontSize: 12, color: C.muted, fontStyle: "italic" }}>
              Step details not available for this simulation. Run a new simulation to see detailed results.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  background: C.raised,
  border: `1px solid ${C.border}`,
  borderRadius: 6,
  padding: "8px 12px",
  fontSize: 13,
  color: C.text,
  outline: "none",
  boxSizing: "border-box",
};

export default function Simulations() {
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [configId, setConfigId] = useState("");
  const [testType, setTestType] = useState("full");
  const [runError, setRunError] = useState<string | null>(null);

  const { data: configData } = useQuery({
    queryKey: ["configurations"],
    queryFn: () => configurationsApi.list(),
  });

  const { data: simsData, isLoading: simsLoading } = useQuery({
    queryKey: ["simulations"],
    queryFn: () => simulationsApi.list(),
  });

  const runMutation = useMutation({
    mutationFn: simulationsApi.run,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["simulations"] });
      queryClient.invalidateQueries({ queryKey: ["configurations"] });
      setShowForm(false);
      setConfigId("");
      setRunError(null);
    },
    onError: (err: Error) => {
      setRunError(err.message ?? "Failed to start simulation.");
    },
  });

  const configs: Configuration[] = configData?.data ?? [];
  const sims: Simulation[] = simsData?.data ?? [];

  const totalSims = sims.length;
  const passedSims = sims.filter((s) => s.status === "passed").length;
  const passRate = totalSims > 0 ? Math.round((passedSims / totalSims) * 100) : null;
  const avgDuration =
    sims.length > 0
      ? Math.round(sims.reduce((acc, s) => acc + (s.duration_ms ?? 0), 0) / sims.length)
      : null;

  const chartData = sims
    .filter((s) => s.total_tests > 0)
    .slice(0, 10)
    .map((s) => {
      const cfg = configs.find((c) => c.id === s.configuration_id);
      return {
        name: cfg ? cfg.name.slice(0, 14) : s.configuration_id.slice(0, 8),
        passed: s.passed_tests,
        failed: s.failed_tests,
      };
    })
    .reverse();

  return (
    <div className="animate-fade-in" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 600, color: C.text, margin: 0 }}>Simulations</h1>
          <p style={{ fontSize: 13, color: C.muted, margin: "4px 0 0" }}>
            Run and review integration test simulations
          </p>
        </div>
        <button
          type="button"
          className="btn-primary"
          onClick={() => {
            setShowForm(!showForm);
            setRunError(null);
          }}
        >
          {showForm ? (
            "Cancel"
          ) : (
            <>
              <Play style={{ width: 14, height: 14 }} /> Run Simulation
            </>
          )}
        </button>
      </div>

      {/* Summary stats */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16 }}>
        {(
          [
            { label: "Total Runs", value: totalSims || "—" },
            {
              label: "Pass Rate",
              value: passRate !== null ? `${passRate}%` : "—",
              color: passRate !== null ? (passRate >= 80 ? C.teal : C.red) : undefined,
            },
            { label: "Avg Duration", value: avgDuration ? formatDuration(avgDuration) : "—" },
          ] as { label: string; value: string | number; color?: string }[]
        ).map(({ label, value, color }) => (
          <div
            key={label}
            style={{
              background: C.elevated,
              border: `1px solid ${C.border}`,
              borderRadius: 8,
              padding: "16px 20px",
            }}
          >
            <p
              style={{
                fontSize: 11,
                fontWeight: 600,
                letterSpacing: "0.06em",
                textTransform: "uppercase",
                color: C.muted,
                margin: 0,
              }}
            >
              {label}
            </p>
            <p
              style={{
                fontSize: 24,
                fontWeight: 700,
                color: color ?? C.text,
                margin: "6px 0 0",
              }}
            >
              {value}
            </p>
          </div>
        ))}
      </div>

      {/* Run form */}
      {showForm && (
        <div
          style={{
            background: C.elevated,
            border: `1px solid ${C.border}`,
            borderRadius: 8,
            padding: "20px 24px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
            <Zap style={{ width: 15, height: 15, color: C.teal }} />
            <h2 style={{ fontSize: 15, fontWeight: 600, color: C.text, margin: 0 }}>New Simulation</h2>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (configId) {
                setRunError(null);
                runMutation.mutate({ configuration_id: configId, test_type: testType });
              }
            }}
            style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 14, alignItems: "end" }}
          >
            <div>
              <label
                htmlFor="sim-config"
                style={{
                  display: "block",
                  fontSize: 11,
                  fontWeight: 600,
                  letterSpacing: "0.06em",
                  textTransform: "uppercase",
                  color: C.muted,
                  marginBottom: 6,
                }}
              >
                Configuration
              </label>
              <select
                id="sim-config"
                value={configId}
                onChange={(e) => setConfigId(e.target.value)}
                style={inputStyle}
              >
                <option value="">Select configuration…</option>
                {configs.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} — {c.status}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label
                htmlFor="test-type"
                style={{
                  display: "block",
                  fontSize: 11,
                  fontWeight: 600,
                  letterSpacing: "0.06em",
                  textTransform: "uppercase",
                  color: C.muted,
                  marginBottom: 6,
                }}
              >
                Test Type
              </label>
              <select
                id="test-type"
                value={testType}
                onChange={(e) => setTestType(e.target.value)}
                style={inputStyle}
              >
                <option value="full">Full</option>
                <option value="smoke">Smoke</option>
                <option value="integration">Integration</option>
              </select>
            </div>
            <button
              type="submit"
              className="btn-primary"
              disabled={!configId || runMutation.isPending}
              style={{ whiteSpace: "nowrap" }}
            >
              {runMutation.isPending ? (
                <>
                  <Loader2 style={{ width: 14, height: 14, animation: "spin 1s linear infinite" }} />
                  Running…
                </>
              ) : (
                <>
                  <Play style={{ width: 14, height: 14 }} /> Run
                </>
              )}
            </button>
          </form>
          {runError && (
            <div
              style={{
                marginTop: 12,
                padding: "10px 14px",
                borderRadius: 6,
                border: `1px solid ${C.red}30`,
                background: `${C.red}08`,
                fontSize: 13,
                color: "#fca5a5",
              }}
            >
              {runError}
            </div>
          )}
        </div>
      )}

      {/* Chart */}
      {chartData.length > 0 && (
        <div
          style={{
            background: C.elevated,
            border: `1px solid ${C.border}`,
            borderRadius: 8,
            padding: "20px 24px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
            <BarChart3 style={{ width: 15, height: 15, color: C.muted }} />
            <h2 style={{ fontSize: 15, fontWeight: 600, color: C.text, margin: 0 }}>
              Test Results by Run
            </h2>
          </div>
          <div style={{ height: 200 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} layout="vertical" barCategoryGap="30%">
                <CartesianGrid strokeDasharray="3 3" stroke={C.border} horizontal={false} />
                <XAxis type="number" stroke={C.muted} fontSize={11} />
                <YAxis
                  type="category"
                  dataKey="name"
                  stroke={C.muted}
                  fontSize={11}
                  width={90}
                  tick={{ fill: C.mutedHi }}
                />
                <Tooltip
                  contentStyle={{
                    background: C.raised,
                    border: `1px solid ${C.borderHi}`,
                    borderRadius: 6,
                    fontSize: 12,
                    color: C.text,
                  }}
                  cursor={{ fill: `${C.border}60` }}
                />
                <Bar dataKey="passed" fill={C.teal} radius={[0, 3, 3, 0]} name="Passed" stackId="a" />
                <Bar dataKey="failed" fill={C.red} radius={[0, 3, 3, 0]} name="Failed" stackId="a" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Simulations list */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {simsLoading ? (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 8,
              padding: "48px 0",
              color: C.muted,
              fontSize: 13,
            }}
          >
            <Loader2 style={{ width: 16, height: 16, animation: "spin 1s linear infinite" }} />
            Loading simulations…
          </div>
        ) : sims.length === 0 ? (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              padding: "64px 0",
              gap: 10,
              background: C.elevated,
              border: `1px solid ${C.border}`,
              borderRadius: 8,
            }}
          >
            <FlaskConical style={{ width: 40, height: 40, color: C.border }} />
            <p style={{ fontSize: 14, fontWeight: 600, color: C.mutedHi, margin: 0 }}>
              No simulations yet
            </p>
            <p style={{ fontSize: 12, color: C.muted, margin: 0 }}>
              Click "Run Simulation" to test an integration configuration.
            </p>
          </div>
        ) : (
          sims.map((sim) => {
            const cfg = configs.find((c) => c.id === sim.configuration_id);
            return <SimRow key={sim.id} sim={sim} configName={cfg?.name} />;
          })
        )}
      </div>
    </div>
  );
}
