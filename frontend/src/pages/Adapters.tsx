import { adaptersApi } from "@/lib/api";
import type { Adapter, AdapterVersion } from "@/types";
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { ChevronDown, Clock, Link2, Plug, RefreshCw, Shield, X } from "lucide-react";
import { useState } from "react";

function methodBadgeCls(method: string): string {
  if (method === "GET") return "bg-emerald-500/15 text-emerald-400";
  if (method === "POST") return "bg-blue-500/15 text-blue-400";
  if (method === "PUT" || method === "PATCH") return "bg-amber-500/15 text-amber-400";
  if (method === "DELETE") return "bg-red-500/15 text-red-400";
  return "bg-gray-500/15 text-gray-400";
}

const categoryColors: Record<string, string> = {
  bureau: "text-blue-400 bg-blue-500/10",
  kyc: "text-purple-400 bg-purple-500/10",
  gst: "text-emerald-400 bg-emerald-500/10",
  payment: "text-amber-400 bg-amber-500/10",
  fraud: "text-rose-400 bg-rose-500/10",
  notification: "text-cyan-400 bg-cyan-500/10",
  open_banking: "text-indigo-400 bg-indigo-500/10",
};

function formatTimeAgo(dateStr?: string): string {
  if (!dateStr) return "Never";
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function SkeletonCard() {
  return (
    <div className="card-hover p-5 animate-pulse" aria-hidden="true">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className="h-9 w-9 rounded-lg bg-gray-800" />
          <div className="space-y-2">
            <div className="h-4 w-28 rounded bg-gray-800" />
            <div className="h-3 w-16 rounded bg-gray-800" />
          </div>
        </div>
        <div className="h-5 w-14 rounded-full bg-gray-800" />
      </div>
      <div className="mt-3 space-y-1.5">
        <div className="h-3 w-full rounded bg-gray-800" />
        <div className="h-3 w-4/5 rounded bg-gray-800" />
      </div>
      <div className="mt-4 flex items-center justify-between border-t border-gray-800 pt-3">
        <div className="h-3 w-16 rounded bg-gray-800" />
        <div className="h-3 w-10 rounded bg-gray-800" />
      </div>
    </div>
  );
}

function VersionPanel({ version }: { version: AdapterVersion }) {
  const [open, setOpen] = useState(false);
  const statusCls = version.status === "active" ? "badge-green" : "badge-gray";

  return (
    <div className="border border-gray-800 rounded-lg overflow-hidden">
      <button
        type="button"
        className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-gray-800/30 transition-colors"
        onClick={() => setOpen(!open)}
      >
        <span className="text-sm font-mono font-medium text-gray-200">v{version.version}</span>
        <span className={statusCls}>{version.status}</span>
        {version.auth_type && (
          <span className="flex items-center gap-1 text-xs text-gray-500 ml-1">
            <Shield className="h-3 w-3" />
            {version.auth_type}
          </span>
        )}
        {version.base_url && (
          <code className="ml-auto text-xs text-indigo-400 truncate max-w-[180px]">
            {version.base_url}
          </code>
        )}
        <ChevronDown
          className={clsx(
            "h-3.5 w-3.5 text-gray-600 shrink-0 transition-transform",
            open && "rotate-180"
          )}
        />
      </button>
      {open && version.endpoints.length > 0 && (
        <div className="border-t border-gray-800 bg-gray-900/40 px-4 py-3">
          <p className="text-xs font-medium text-gray-500 mb-2">
            Endpoints ({version.endpoints.length})
          </p>
          <div className="space-y-1.5">
            {version.endpoints.map((ep, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: endpoints lack stable id
              <div key={i} className="flex items-center gap-2 text-xs">
                <span
                  className={clsx(
                    "inline-block rounded px-1.5 py-0.5 text-[10px] font-bold uppercase shrink-0",
                    methodBadgeCls(ep.method)
                  )}
                >
                  {ep.method}
                </span>
                <code className="font-mono text-gray-300">{ep.path}</code>
                {ep.description && <span className="text-gray-500 truncate">{ep.description}</span>}
              </div>
            ))}
          </div>
        </div>
      )}
      {open && version.endpoints.length === 0 && (
        <div className="border-t border-gray-800 bg-gray-900/40 px-4 py-3">
          <p className="text-xs text-gray-600">No endpoints defined for this version.</p>
        </div>
      )}
    </div>
  );
}

function AdapterModal({
  adapter,
  onClose,
}: {
  adapter: Adapter;
  onClose: () => void;
}) {
  const categoryColor = categoryColors[adapter.category] ?? "text-gray-400 bg-gray-500/10";

  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: modal overlay, keyboard handled via close button
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div className="relative z-10 w-full max-w-2xl max-h-[85vh] flex flex-col rounded-xl border border-gray-800 bg-gray-950 shadow-2xl">
        {/* Header */}
        <div className="flex items-center gap-3 border-b border-gray-800 px-6 py-4">
          <div className={clsx("rounded-lg p-2", categoryColor)}>
            <Plug className="h-4 w-4" />
          </div>
          <div className="flex-1">
            <h2 className="font-semibold text-white">{adapter.name}</h2>
            <div className="flex items-center gap-2 mt-0.5">
              <span
                className={clsx(
                  "inline-block rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
                  categoryColor
                )}
              >
                {adapter.category.replaceAll("_", " ")}
              </span>
              <span className={adapter.is_active ? "badge-green" : "badge-gray"}>
                {adapter.is_active ? "Active" : "Inactive"}
              </span>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-500 hover:text-gray-300 transition-colors"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-auto p-6 space-y-5">
          {adapter.description && (
            <p className="text-sm text-gray-400 leading-relaxed">{adapter.description}</p>
          )}

          <div>
            <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3 flex items-center gap-1.5">
              <Link2 className="h-3.5 w-3.5" />
              Versions ({adapter.versions.length})
            </h3>
            {adapter.versions.length > 0 ? (
              <div className="space-y-2">
                {adapter.versions.map((v) => (
                  <VersionPanel key={v.id} version={v} />
                ))}
              </div>
            ) : (
              <p className="text-sm text-gray-600">No versions available.</p>
            )}
          </div>

          <div className="text-xs text-gray-600 border-t border-gray-800 pt-3">
            Added {formatTimeAgo(adapter.created_at)}
          </div>
        </div>
      </div>
    </div>
  );
}

const CATEGORY_PILLS = [
  { value: "", label: "All" },
  { value: "bureau", label: "Bureau" },
  { value: "kyc", label: "KYC" },
  { value: "gst", label: "GST" },
  { value: "payment", label: "Payment" },
  { value: "fraud", label: "Fraud" },
  { value: "notification", label: "Notification" },
  { value: "open_banking", label: "Open Banking" },
];

export default function Adapters() {
  const [categoryFilter, setCategoryFilter] = useState("");
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["adapters", categoryFilter],
    queryFn: () => adaptersApi.list(categoryFilter || undefined),
  });
  const [selectedAdapter, setSelectedAdapter] = useState<Adapter | null>(null);

  const adapters: Adapter[] = isLoading ? [] : (data?.data?.adapters ?? []);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Adapters</h1>
          <p className="mt-1 text-sm text-gray-400">Integration connectors and data sources</p>
        </div>
        <button
          type="button"
          className="btn-secondary"
          onClick={() => refetch()}
          aria-label="Refresh adapters list"
        >
          <RefreshCw className={clsx("h-4 w-4", isLoading && "animate-spin")} />
          Refresh
        </button>
      </div>

      {/* Category filter pills */}
      <fieldset className="flex flex-wrap gap-2">
        <legend className="sr-only">Filter by category</legend>
        {CATEGORY_PILLS.map((pill) => (
          <button
            key={pill.value}
            type="button"
            onClick={() => setCategoryFilter(pill.value)}
            className={clsx(
              "rounded-full px-3 py-1.5 text-xs font-medium transition-colors",
              categoryFilter === pill.value
                ? "bg-indigo-600 text-white"
                : "border border-gray-700 text-gray-400 hover:border-gray-600 hover:text-gray-200"
            )}
          >
            {pill.label}
          </button>
        ))}
      </fieldset>

      {error && (
        <div
          role="alert"
          className="rounded-lg border border-red-500/20 bg-red-500/5 p-4 text-sm text-red-400"
        >
          Failed to load adapters. Please try refreshing.
        </div>
      )}

      {isLoading ? (
        <div
          className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3"
          aria-label="Loading adapters"
        >
          {Array.from({ length: 6 }).map((_, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: skeleton placeholders
            <SkeletonCard key={i} />
          ))}
        </div>
      ) : adapters.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-xl border border-gray-800 bg-gray-900/40 py-16 text-center">
          <div className="rounded-full bg-gray-800 p-4">
            <Plug className="h-6 w-6 text-gray-500" />
          </div>
          <h2 className="mt-4 text-base font-semibold text-gray-300">No adapters configured</h2>
          <p className="mt-1 text-sm text-gray-500">
            Connect your first data source to get started.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {adapters.map((adapter) => {
            const statusLabel = adapter.is_active ? "Active" : "Inactive";
            const statusCls = adapter.is_active ? "badge-green" : "badge-gray";
            const categoryColor =
              categoryColors[adapter.category] ?? "text-gray-400 bg-gray-500/10";
            const latestVersion = adapter.versions[adapter.versions.length - 1]?.version;

            return (
              <button
                key={adapter.id}
                type="button"
                className="card-hover group p-5 text-left w-full cursor-pointer"
                onClick={() => setSelectedAdapter(adapter)}
              >
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-3">
                    <div className={clsx("rounded-lg p-2", categoryColor)}>
                      <Plug className="h-4 w-4" aria-hidden="true" />
                    </div>
                    <div>
                      <h2 className="font-semibold text-white group-hover:text-indigo-300 transition-colors">
                        {adapter.name}
                      </h2>
                      <span
                        className={clsx(
                          "mt-0.5 inline-block rounded-md px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
                          categoryColor
                        )}
                      >
                        {adapter.category.replaceAll("_", " ")}
                      </span>
                    </div>
                  </div>
                  <span className={statusCls}>{statusLabel}</span>
                </div>
                <p className="mt-3 text-sm leading-relaxed text-gray-400">{adapter.description}</p>
                <div className="mt-4 flex items-center justify-between border-t border-gray-800 pt-3">
                  <div className="flex items-center gap-1.5 text-xs text-gray-500">
                    <Clock className="h-3 w-3" aria-hidden="true" />
                    <span>
                      <span className="sr-only">Added: </span>
                      {formatTimeAgo(adapter.created_at)}
                    </span>
                  </div>
                  {latestVersion && <span className="text-xs text-gray-500">v{latestVersion}</span>}
                </div>
              </button>
            );
          })}
        </div>
      )}

      {selectedAdapter && (
        <AdapterModal adapter={selectedAdapter} onClose={() => setSelectedAdapter(null)} />
      )}
    </div>
  );
}
