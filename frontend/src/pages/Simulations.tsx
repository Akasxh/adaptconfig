import { configurationsApi, simulationsApi } from "@/lib/api";
import type { Configuration, Simulation, SimulationStepResult } from "@/types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  AlertCircle,
  BarChart3,
  CheckCircle2,
  ChevronDown,
  Clock,
  FlaskConical,
  Loader2,
  Play,
  XCircle,
  Zap,
} from "lucide-react";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const statusConfig: Record<
  string,
  { label: string; icon: React.ComponentType<{ className?: string }>; cls: string }
> = {
  pending: { label: "Pending", icon: Clock, cls: "badge-yellow" },
  running: { label: "Running", icon: Loader2, cls: "badge-blue" },
  passed: { label: "Passed", icon: CheckCircle2, cls: "badge-green" },
  failed: { label: "Failed", icon: XCircle, cls: "badge-red" },
  error: { label: "Error", icon: XCircle, cls: "badge-red" },
};

function formatDuration(ms?: number): string {
  if (!ms) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

function StepRow({ step }: { step: SimulationStepResult }) {
  const [expanded, setExpanded] = useState(false);
  const passed = step.status === "passed" || step.status === "pass";
  const pct = Math.round(step.confidence_score * 100);

  return (
    <div className="border border-gray-800 rounded-lg overflow-hidden">
      <button
        type="button"
        className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-gray-800/30 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className={clsx("shrink-0", passed ? "text-emerald-400" : "text-red-400")}>
          {passed ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
        </div>
        <span className="flex-1 text-sm font-medium text-gray-200">{step.step_name}</span>
        <span className="text-xs text-gray-500">{formatDuration(step.duration_ms)}</span>
        <div className="flex items-center gap-1.5 w-24">
          <div className="flex-1 h-1.5 rounded-full bg-gray-700 overflow-hidden">
            <div
              className={clsx(
                "h-full rounded-full",
                pct >= 70 ? "bg-emerald-500" : pct >= 40 ? "bg-amber-500" : "bg-red-500"
              )}
              style={{ width: `${pct}%` }}
            />
          </div>
          <span className="text-xs text-gray-500 tabular-nums">{pct}%</span>
        </div>
        <ChevronDown
          className={clsx(
            "h-3.5 w-3.5 text-gray-600 transition-transform shrink-0",
            expanded && "rotate-180"
          )}
        />
      </button>
      {expanded && (
        <div className="border-t border-gray-800 bg-gray-900/40 px-4 py-3 space-y-3 text-xs">
          {step.error_message && (
            <div className="flex items-start gap-2 rounded-lg border border-red-500/20 bg-red-500/5 p-3">
              <AlertCircle className="h-3.5 w-3.5 text-red-400 shrink-0 mt-0.5" />
              <p className="text-red-300">{step.error_message}</p>
            </div>
          )}
          {Object.keys(step.request_payload).length > 0 && (
            <div>
              <p className="text-gray-500 font-medium mb-1">Request</p>
              <pre className="rounded bg-gray-950 p-2 text-gray-300 overflow-x-auto">
                {JSON.stringify(step.request_payload, null, 2)}
              </pre>
            </div>
          )}
          {Object.keys(step.actual_response).length > 0 && (
            <div>
              <p className="text-gray-500 font-medium mb-1">Response</p>
              <pre className="rounded bg-gray-950 p-2 text-gray-300 overflow-x-auto">
                {JSON.stringify(step.actual_response, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SimCard({ sim, configName }: { sim: Simulation; configName?: string }) {
  const [showSteps, setShowSteps] = useState(false);
  const st = statusConfig[sim.status] ?? { label: sim.status, icon: Clock, cls: "badge-gray" };
  const StatusIcon = st.icon;
  const successPct =
    sim.total_tests > 0 ? Math.round((sim.passed_tests / sim.total_tests) * 100) : 0;

  return (
    <div className="card overflow-hidden">
      <div className="p-5">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className="rounded-lg bg-gray-800 p-2">
              <FlaskConical className="h-4 w-4 text-gray-400" />
            </div>
            <div>
              <h3 className="font-medium text-white">
                {configName ? configName : `Config: ${sim.configuration_id.slice(0, 8)}…`}
              </h3>
              <p className="mt-0.5 text-xs text-gray-500">
                {new Date(sim.created_at).toLocaleString()} &middot; {sim.test_type}
              </p>
            </div>
          </div>
          <span className={st.cls}>
            <StatusIcon
              className={clsx("mr-1 h-3 w-3", sim.status === "running" && "animate-spin")}
            />
            {st.label}
          </span>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-lg bg-gray-800/50 p-3">
            <p className="text-xs text-gray-500">Success Rate</p>
            <p
              className={clsx(
                "mt-1 text-lg font-bold",
                successPct >= 95
                  ? "text-emerald-400"
                  : successPct >= 80
                    ? "text-amber-400"
                    : "text-red-400"
              )}
            >
              {successPct}%
            </p>
          </div>
          <div className="rounded-lg bg-gray-800/50 p-3">
            <p className="text-xs text-gray-500">Tests</p>
            <p className="mt-1 text-lg font-bold text-white">
              {sim.passed_tests}
              <span className="text-xs font-normal text-gray-500">/{sim.total_tests}</span>
            </p>
          </div>
          <div className="rounded-lg bg-gray-800/50 p-3">
            <p className="text-xs text-gray-500">Failed</p>
            <p className="mt-1 text-lg font-bold text-red-400">{sim.failed_tests}</p>
          </div>
          <div className="rounded-lg bg-gray-800/50 p-3">
            <p className="text-xs text-gray-500">Duration</p>
            <p className="mt-1 text-lg font-bold text-gray-300">
              {formatDuration(sim.duration_ms)}
            </p>
          </div>
        </div>
      </div>

      {sim.steps && sim.steps.length > 0 && (
        <div className="border-t border-gray-800">
          <button
            type="button"
            className="flex w-full items-center justify-between px-5 py-3 text-xs text-gray-500 hover:text-gray-300 hover:bg-gray-800/30 transition-colors"
            onClick={() => setShowSteps(!showSteps)}
          >
            <span className="font-medium">Step Results ({sim.steps.length})</span>
            <ChevronDown
              className={clsx("h-3.5 w-3.5 transition-transform", showSteps && "rotate-180")}
            />
          </button>
          {showSteps && (
            <div className="px-5 pb-4 space-y-2">
              {sim.steps.map((step, i) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: steps lack stable id
                <StepRow key={i} step={step} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function Simulations() {
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [configId, setConfigId] = useState("");
  const [testType, setTestType] = useState("full");
  const [runError, setRunError] = useState<string | null>(null);
  const [recentSims, setRecentSims] = useState<Simulation[]>([]);

  const { data: configData, isLoading: configsLoading } = useQuery({
    queryKey: ["configurations"],
    queryFn: () => configurationsApi.list(),
  });

  const runMutation = useMutation({
    mutationFn: simulationsApi.run,
    onSuccess: (response) => {
      queryClient.invalidateQueries({ queryKey: ["simulations"] });
      if (response.data) {
        setRecentSims((prev) => [response.data as Simulation, ...prev]);
      }
      setShowForm(false);
      setConfigId("");
      setRunError(null);
    },
    onError: (err: Error) => {
      setRunError(err.message ?? "Failed to start simulation.");
    },
  });

  const configs: Configuration[] = configData?.data ?? [];

  const chartData = recentSims
    .filter((s) => s.total_tests > 0)
    .map((s) => {
      const cfg = configs.find((c) => c.id === s.configuration_id);
      return {
        name: cfg ? cfg.name.slice(0, 12) : s.configuration_id.slice(0, 8),
        success: Math.round((s.passed_tests / s.total_tests) * 100),
        failed: s.failed_tests,
      };
    });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Simulations</h1>
          <p className="mt-1 text-sm text-gray-400">Run and monitor integration simulations</p>
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
              <Play className="h-4 w-4" /> Run Simulation
            </>
          )}
        </button>
      </div>

      {/* Run form */}
      {showForm && (
        <div className="card p-6">
          <div className="mb-4 flex items-center gap-2">
            <Zap className="h-4 w-4 text-indigo-400" />
            <h3 className="font-semibold text-white">New Simulation</h3>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (configId) {
                setRunError(null);
                runMutation.mutate({
                  configuration_id: configId,
                  test_type: testType,
                });
              }
            }}
            className="grid gap-4 sm:grid-cols-3"
          >
            <div>
              <label
                htmlFor="sim-config"
                className="mb-1.5 block text-xs font-medium text-gray-400"
              >
                Configuration
              </label>
              <select
                id="sim-config"
                value={configId}
                onChange={(e) => setConfigId(e.target.value)}
                disabled={configsLoading}
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              >
                <option value="">Select configuration...</option>
                {configs.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} — {c.status}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="test-type" className="mb-1.5 block text-xs font-medium text-gray-400">
                Test Type
              </label>
              <select
                id="test-type"
                value={testType}
                onChange={(e) => setTestType(e.target.value)}
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              >
                <option value="full">Full</option>
                <option value="smoke">Smoke</option>
                <option value="integration">Integration</option>
              </select>
            </div>
            <div className="flex items-end">
              <button
                type="submit"
                className="btn-primary w-full justify-center"
                disabled={!configId || runMutation.isPending}
              >
                <Play className="h-4 w-4" />
                {runMutation.isPending ? "Running..." : "Run"}
              </button>
            </div>
          </form>
          {runError && (
            <div className="mt-3 rounded-lg border border-red-500/20 bg-red-500/5 p-3 text-sm text-red-400">
              {runError}
            </div>
          )}
        </div>
      )}

      {/* Results chart */}
      {chartData.length > 0 && (
        <div className="card p-6">
          <div className="mb-4 flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-gray-400" />
            <h3 className="font-semibold text-white">Success Rates</h3>
          </div>
          <div className="h-52">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                <XAxis type="number" domain={[0, 100]} stroke="#6b7280" fontSize={12} />
                <YAxis type="category" dataKey="name" stroke="#6b7280" fontSize={11} width={80} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "#111827",
                    border: "1px solid #374151",
                    borderRadius: "8px",
                    fontSize: "12px",
                  }}
                />
                <Bar dataKey="success" fill="#6366f1" radius={[0, 4, 4, 0]} name="Success %" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Simulation list */}
      <div className="space-y-3">
        {recentSims.length === 0 ? (
          <div className="card flex flex-col items-center justify-center py-16 text-center">
            <FlaskConical className="mb-3 h-10 w-10 text-gray-600" />
            <p className="text-sm font-medium text-gray-400">No simulations yet</p>
            <p className="mt-1 text-xs text-gray-600">
              Click "Run Simulation" to test an integration configuration.
            </p>
          </div>
        ) : (
          recentSims.map((sim) => {
            const cfg = configs.find((c) => c.id === sim.configuration_id);
            return <SimCard key={sim.id} sim={sim} configName={cfg?.name} />;
          })
        )}
      </div>
    </div>
  );
}
