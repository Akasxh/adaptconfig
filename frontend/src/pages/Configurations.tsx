import { useToast } from "@/components/Toast";
import { adaptersApi, configurationsApi, documentsApi } from "@/lib/api";
import type { Adapter, ConfigValidationResult, Configuration } from "@/types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  AlertCircle,
  BarChart3,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  PlayCircle,
  Plus,
  Rocket,
  Settings,
  Sparkles,
} from "lucide-react";
import { useState } from "react";

const statusConfig: Record<string, { label: string; cls: string }> = {
  draft: { label: "Draft", cls: "badge-yellow" },
  configured: { label: "Configured", cls: "badge-blue" },
  validating: { label: "Validating", cls: "badge-blue" },
  testing: { label: "Testing", cls: "badge-blue" },
  active: { label: "Active", cls: "badge-green" },
  deprecated: { label: "Deprecated", cls: "badge-gray" },
  rollback: { label: "Rollback", cls: "badge-yellow" },
};

const TRANSITION_BUTTONS: Record<
  string,
  { label: string; icon: React.ElementType; targetState: string }[]
> = {
  draft: [{ label: "Mark Configured", icon: CheckCircle2, targetState: "configured" }],
  configured: [{ label: "Start Validation", icon: PlayCircle, targetState: "testing" }],
  testing: [{ label: "Deploy", icon: Rocket, targetState: "active" }],
};

const STATUS_STEPS = ["draft", "configured", "testing", "active"];

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function SkeletonRow() {
  return (
    <div className="card overflow-hidden animate-pulse">
      <div className="flex items-center gap-4 px-6 py-4">
        <div className="rounded-lg bg-gray-800 p-2">
          <div className="h-4 w-4 rounded bg-gray-700" />
        </div>
        <div className="flex-1 space-y-2">
          <div className="h-4 w-48 rounded bg-gray-700" />
          <div className="h-3 w-32 rounded bg-gray-800" />
        </div>
        <div className="h-4 w-4 rounded bg-gray-700" />
      </div>
    </div>
  );
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full bg-gray-700 overflow-hidden">
        <div
          className={clsx(
            "h-full rounded-full transition-all",
            pct >= 70 ? "bg-emerald-500" : pct >= 40 ? "bg-amber-500" : "bg-red-500"
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span
        className={clsx(
          "text-xs tabular-nums font-medium",
          pct >= 70 ? "text-emerald-400" : pct >= 40 ? "text-amber-400" : "text-red-400"
        )}
      >
        {pct}%
      </span>
    </div>
  );
}

function StatusStepper({ status }: { status: string }) {
  const currentIdx = STATUS_STEPS.indexOf(status);
  return (
    <div className="flex items-center gap-1">
      {STATUS_STEPS.map((step, idx) => (
        <div key={step} className="flex items-center gap-1">
          <div
            className={clsx(
              "h-2 w-2 rounded-full",
              idx < currentIdx
                ? "bg-emerald-500"
                : idx === currentIdx
                  ? "bg-indigo-500"
                  : "bg-gray-700"
            )}
          />
          {idx < STATUS_STEPS.length - 1 && (
            <div
              className={clsx("h-px w-4", idx < currentIdx ? "bg-emerald-500/50" : "bg-gray-700")}
            />
          )}
        </div>
      ))}
      <span className="ml-2 text-xs text-gray-500 capitalize">{status}</span>
    </div>
  );
}

function ValidationPanel({ configId }: { configId: string }) {
  const [result, setResult] = useState<ConfigValidationResult | null>(null);
  const [ran, setRan] = useState(false);

  const validateMutation = useMutation({
    mutationFn: () => configurationsApi.validate(configId),
    onSuccess: (resp) => {
      if (resp.data) {
        setResult(resp.data);
        setRan(true);
      }
    },
  });

  return (
    <div className="mt-3 space-y-3">
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="btn-secondary text-xs py-1.5 px-3"
          onClick={() => validateMutation.mutate()}
          disabled={validateMutation.isPending}
        >
          <BarChart3 className="h-3.5 w-3.5" />
          {validateMutation.isPending ? "Validating..." : ran ? "Re-validate" : "Validate"}
        </button>
        {ran && result && (
          <span
            className={clsx(
              "text-xs font-medium",
              result.is_valid ? "text-emerald-400" : "text-red-400"
            )}
          >
            {result.is_valid ? "✓ Valid" : "✗ Invalid"}
          </span>
        )}
      </div>
      {ran && result && (
        <div className="space-y-2">
          <div className="flex items-center gap-3 text-xs text-gray-500">
            <span>Coverage</span>
            <div className="flex-1 max-w-xs">
              <ConfidenceBar value={result.coverage_score} />
            </div>
          </div>
          {result.errors.length > 0 && (
            <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-3">
              <p className="text-xs font-medium text-red-400 mb-1">
                Errors ({result.errors.length})
              </p>
              {result.errors.map((e) => (
                <p key={e} className="text-xs text-red-300 flex items-start gap-1">
                  <AlertCircle className="h-3 w-3 mt-0.5 shrink-0" />
                  {e}
                </p>
              ))}
            </div>
          )}
          {result.warnings.length > 0 && (
            <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3">
              <p className="text-xs font-medium text-amber-400 mb-1">
                Warnings ({result.warnings.length})
              </p>
              {result.warnings.map((w) => (
                <p key={w} className="text-xs text-amber-300">
                  {w}
                </p>
              ))}
            </div>
          )}
          {result.missing_required_fields.length > 0 && (
            <p className="text-xs text-gray-500">
              Missing required: {result.missing_required_fields.join(", ")}
            </p>
          )}
        </div>
      )}
      {validateMutation.isError && (
        <p className="text-xs text-red-400">Validation failed. Check connection.</p>
      )}
    </div>
  );
}

function ConfigDetail({ cfg }: { cfg: Configuration }) {
  const [showRaw, setShowRaw] = useState(false);

  return (
    <div className="border-t border-gray-800 bg-gray-900/40 px-6 py-4 space-y-4">
      {/* Status stepper */}
      <div>
        <p className="text-xs font-medium text-gray-500 mb-2">Progress</p>
        <StatusStepper status={cfg.status} />
      </div>

      {/* Field mappings table */}
      {cfg.field_mappings.length > 0 && (
        <div>
          <p className="text-xs font-medium text-gray-500 mb-2">
            Field Mappings ({cfg.field_mappings.length})
          </p>
          <div className="overflow-x-auto rounded-lg border border-gray-800">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-800 bg-gray-900/60">
                  <th className="px-3 py-2 text-left font-medium text-gray-500">Source</th>
                  <th className="px-3 py-2 text-center text-gray-700">→</th>
                  <th className="px-3 py-2 text-left font-medium text-gray-500">Target</th>
                  <th className="px-3 py-2 text-left font-medium text-gray-500">Confidence</th>
                  <th className="px-3 py-2 text-left font-medium text-gray-500">Transform</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/60">
                {cfg.field_mappings.map((fm, i) => (
                  // biome-ignore lint/suspicious/noArrayIndexKey: field mappings lack stable id
                  <tr key={i} className="hover:bg-gray-800/30">
                    <td className="px-3 py-2 font-mono text-gray-300">{fm.source_field}</td>
                    <td className="px-3 py-2 text-center text-gray-700">
                      <ChevronRight className="h-3 w-3 mx-auto" />
                    </td>
                    <td className="px-3 py-2 font-mono text-gray-300">{fm.target_field}</td>
                    <td className="px-3 py-2 w-32">
                      <ConfidenceBar value={fm.confidence} />
                    </td>
                    <td className="px-3 py-2 text-gray-400">{fm.transformation || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Validate section */}
      <div>
        <p className="text-xs font-medium text-gray-500 mb-1">Validation</p>
        <ValidationPanel configId={cfg.id} />
      </div>

      {/* Raw JSON toggle */}
      <div>
        <button
          type="button"
          className="text-xs text-gray-500 hover:text-gray-300 flex items-center gap-1"
          onClick={() => setShowRaw(!showRaw)}
        >
          <ChevronDown className={clsx("h-3 w-3 transition-transform", showRaw && "rotate-180")} />
          {showRaw ? "Hide" : "Show"} raw JSON
        </button>
        {showRaw && (
          <pre className="mt-2 rounded-lg bg-gray-950 p-4 text-xs text-gray-300 overflow-x-auto leading-relaxed">
            {JSON.stringify(cfg, null, 2)}
          </pre>
        )}
      </div>

      {/* Meta */}
      <div className="text-xs text-gray-500">
        Adapter version: <code className="text-gray-400">{cfg.adapter_version_id}</code> &middot;
        Created {formatDate(cfg.created_at)}
      </div>
    </div>
  );
}

export default function Configurations() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [documentId, setDocumentId] = useState("");
  const [selectedAdapterId, setSelectedAdapterId] = useState("");
  const [adapterVersionId, setAdapterVersionId] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [generateError, setGenerateError] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["configurations"],
    queryFn: () => configurationsApi.list(),
  });

  const { data: docsData } = useQuery({
    queryKey: ["documents"],
    queryFn: () => documentsApi.list(),
    enabled: showForm,
  });

  const { data: adaptersData } = useQuery({
    queryKey: ["adapters"],
    queryFn: () => adaptersApi.list(),
    enabled: showForm,
  });

  const generateMutation = useMutation({
    mutationFn: configurationsApi.generate,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["configurations"] });
      setShowForm(false);
      setName("");
      setDocumentId("");
      setSelectedAdapterId("");
      setAdapterVersionId("");
      setGenerateError(null);
      toast("Configuration generated successfully.", "success");
    },
    onError: (err: Error) => {
      setGenerateError(err.message ?? "Failed to generate configuration.");
    },
  });

  const transitionMutation = useMutation({
    mutationFn: ({ id, targetState }: { id: string; targetState: string }) =>
      configurationsApi.transition(id, targetState),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["configurations"] });
      toast("Configuration state updated.", "success");
    },
    onError: () => {
      toast("Failed to transition state.", "error");
    },
  });

  const configs: Configuration[] = data?.data ?? [];
  const docs = docsData?.data ?? [];
  const adapters: Adapter[] = adaptersData?.data?.adapters ?? [];

  const selectedAdapter = adapters.find((a) => a.id === selectedAdapterId);

  const handleAdapterChange = (adapterId: string) => {
    setSelectedAdapterId(adapterId);
    setAdapterVersionId("");
    const adapter = adapters.find((a) => a.id === adapterId);
    if (adapter && !name) {
      setName(`${adapter.name} Integration`);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Configurations</h1>
          <p className="mt-1 text-sm text-gray-400">
            Manage and generate integration configurations
          </p>
        </div>
        <button
          type="button"
          className="btn-primary"
          onClick={() => {
            setShowForm(!showForm);
            setGenerateError(null);
          }}
        >
          {showForm ? (
            "Cancel"
          ) : (
            <>
              <Plus className="h-4 w-4" /> Generate Config
            </>
          )}
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-4 text-sm text-amber-400">
          Failed to load configurations. Check your connection and try again.
        </div>
      )}

      {/* Generate form */}
      {showForm && (
        <div className="card p-6">
          <div className="mb-4 flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-indigo-400" />
            <h3 className="font-semibold text-white">Generate New Configuration</h3>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim() && documentId.trim() && adapterVersionId.trim()) {
                setGenerateError(null);
                generateMutation.mutate({
                  name: name.trim(),
                  document_id: documentId.trim(),
                  adapter_version_id: adapterVersionId.trim(),
                });
              }
            }}
            className="grid gap-4 sm:grid-cols-2"
          >
            {/* Document dropdown */}
            <div>
              <label htmlFor="cfg-doc" className="mb-1.5 block text-xs font-medium text-gray-400">
                Document
              </label>
              <select
                id="cfg-doc"
                value={documentId}
                onChange={(e) => setDocumentId(e.target.value)}
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              >
                <option value="">Select document...</option>
                {docs.map((doc) => (
                  <option key={doc.id} value={doc.id}>
                    {doc.filename} ({doc.status})
                  </option>
                ))}
              </select>
            </div>

            {/* Adapter dropdown */}
            <div>
              <label
                htmlFor="cfg-adapter-name"
                className="mb-1.5 block text-xs font-medium text-gray-400"
              >
                Adapter
              </label>
              <select
                id="cfg-adapter-name"
                value={selectedAdapterId}
                onChange={(e) => handleAdapterChange(e.target.value)}
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              >
                <option value="">Select adapter...</option>
                {adapters.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </div>

            {/* Version dropdown */}
            <div>
              <label
                htmlFor="cfg-adapter-version"
                className="mb-1.5 block text-xs font-medium text-gray-400"
              >
                Adapter Version
              </label>
              <select
                id="cfg-adapter-version"
                value={adapterVersionId}
                onChange={(e) => setAdapterVersionId(e.target.value)}
                disabled={!selectedAdapter}
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:opacity-50"
              >
                <option value="">
                  {selectedAdapter ? "Select version..." : "Select adapter first"}
                </option>
                {selectedAdapter?.versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    v{v.version} — {v.status}
                  </option>
                ))}
              </select>
            </div>

            {/* Name input */}
            <div>
              <label htmlFor="cfg-name" className="mb-1.5 block text-xs font-medium text-gray-400">
                Configuration Name
              </label>
              <input
                id="cfg-name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g., Credit Bureau Integration"
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>

            <div className="sm:col-span-2 flex justify-end">
              <button
                type="submit"
                className="btn-primary"
                disabled={
                  !name.trim() ||
                  !documentId.trim() ||
                  !adapterVersionId.trim() ||
                  generateMutation.isPending
                }
              >
                <Sparkles className="h-4 w-4" />
                {generateMutation.isPending ? "Generating..." : "Generate"}
              </button>
            </div>
          </form>
          {generateError && (
            <div className="mt-3 rounded-lg border border-red-500/20 bg-red-500/5 p-3 text-sm text-red-400">
              {generateError}
            </div>
          )}
        </div>
      )}

      {/* Config list */}
      <div className="space-y-3">
        {isLoading ? (
          <>
            <SkeletonRow />
            <SkeletonRow />
            <SkeletonRow />
          </>
        ) : configs.length === 0 && !error ? (
          <div className="card flex flex-col items-center justify-center py-16 text-center">
            <Settings className="mb-3 h-10 w-10 text-gray-600" />
            <p className="text-sm font-medium text-gray-400">No configurations yet</p>
            <p className="mt-1 text-xs text-gray-600">
              Click "Generate Config" to create your first integration configuration.
            </p>
          </div>
        ) : (
          configs.map((cfg) => {
            const st = statusConfig[cfg.status] ?? { label: cfg.status, cls: "badge-gray" };
            const isExpanded = expandedId === cfg.id;
            const transitions = TRANSITION_BUTTONS[cfg.status] ?? [];

            return (
              <div key={cfg.id} className="card overflow-hidden">
                <button
                  type="button"
                  className="flex w-full items-center gap-4 px-6 py-4 text-left transition-colors hover:bg-gray-800/30"
                  onClick={() => setExpandedId(isExpanded ? null : cfg.id)}
                >
                  <div className="rounded-lg bg-gray-800 p-2">
                    <Settings className="h-4 w-4 text-gray-400" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="font-medium text-white">{cfg.name}</h3>
                      <span className={st.cls}>{st.label}</span>
                    </div>
                    <div className="mt-1 flex items-center gap-3 text-xs text-gray-500">
                      <span className="font-mono">v{cfg.version}</span>
                      <span>&middot;</span>
                      <span className="flex items-center gap-1">
                        <Clock className="h-3 w-3" />
                        Updated {formatDate(cfg.updated_at)}
                      </span>
                      <span>&middot;</span>
                      <span>{cfg.field_mappings.length} mappings</span>
                    </div>
                  </div>
                  {/* Transition buttons */}
                  {transitions.length > 0 && (
                    <div className="flex gap-2">
                      {transitions.map((t) => {
                        const TIcon = t.icon;
                        return (
                          <button
                            key={t.targetState}
                            type="button"
                            className="btn-secondary text-xs py-1 px-2.5"
                            disabled={transitionMutation.isPending}
                            onClick={(e) => {
                              e.stopPropagation();
                              transitionMutation.mutate({ id: cfg.id, targetState: t.targetState });
                            }}
                          >
                            <TIcon className="h-3.5 w-3.5" />
                            {t.label}
                          </button>
                        );
                      })}
                    </div>
                  )}
                  <ChevronDown
                    className={clsx(
                      "h-4 w-4 text-gray-500 transition-transform shrink-0",
                      isExpanded && "rotate-180"
                    )}
                  />
                </button>
                {isExpanded && <ConfigDetail cfg={cfg} />}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
