import { adaptersApi } from "@/lib/api";
import type { Adapter } from "@/types";
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { Clock, Plug, RefreshCw } from "lucide-react";

const statusConfig: Record<string, { label: string; cls: string }> = {
  active: { label: "Active", cls: "badge-green" },
  inactive: { label: "Inactive", cls: "badge-gray" },
  error: { label: "Error", cls: "badge-red" },
};

const typeColors: Record<string, string> = {
  erp: "text-blue-400 bg-blue-500/10",
  crm: "text-purple-400 bg-purple-500/10",
  market_data: "text-emerald-400 bg-emerald-500/10",
  payments: "text-amber-400 bg-amber-500/10",
  trading: "text-rose-400 bg-rose-500/10",
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

export default function Adapters() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["adapters"],
    queryFn: adaptersApi.list,
  });

  const adapters: Adapter[] = isLoading ? [] : (data ?? []);

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
            const st = statusConfig[adapter.status] ?? {
              label: adapter.status,
              cls: "badge-gray",
            };
            const typeColor = typeColors[adapter.type] ?? "text-gray-400 bg-gray-500/10";

            return (
              <div key={adapter.id} className="card-hover group p-5">
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-3">
                    <div className={clsx("rounded-lg p-2", typeColor)}>
                      <Plug className="h-4 w-4" aria-hidden="true" />
                    </div>
                    <div>
                      <h2 className="font-semibold text-white group-hover:text-indigo-300 transition-colors">
                        {adapter.name}
                      </h2>
                      <span
                        className={clsx(
                          "mt-0.5 inline-block rounded-md px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
                          typeColor
                        )}
                      >
                        {adapter.type.replaceAll("_", " ")}
                      </span>
                    </div>
                  </div>
                  <span className={st.cls}>{st.label}</span>
                </div>
                <p className="mt-3 text-sm leading-relaxed text-gray-400">{adapter.description}</p>
                <div className="mt-4 flex items-center justify-between border-t border-gray-800 pt-3">
                  <div className="flex items-center gap-1.5 text-xs text-gray-500">
                    <Clock className="h-3 w-3" aria-hidden="true" />
                    <span>
                      <span className="sr-only">Last synced: </span>
                      {formatTimeAgo(adapter.last_sync)}
                    </span>
                  </div>
                  <span className="text-xs text-gray-500">v{adapter.version}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
