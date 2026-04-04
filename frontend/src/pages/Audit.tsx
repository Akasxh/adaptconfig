import Pagination from "@/components/Pagination";
import { auditApi } from "@/lib/api";
import type { AuditEntry } from "@/types";
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { ChevronDown, Play, Plug, RefreshCw, Settings, Shield, Upload, User } from "lucide-react";
import { useState } from "react";

const actionIcons: Record<string, React.ComponentType<{ className?: string }>> = {
  document_upload: Upload,
  upload_document: Upload,
  document_process: RefreshCw,
  run_simulation: Play,
  generate_config: Settings,
  transition: Settings,
  rollback: RefreshCw,
  adapter_sync: RefreshCw,
  adapter_activate: Plug,
  delete_document: Upload,
};

const ACTION_OPTIONS = [
  { value: "", label: "All Actions" },
  { value: "upload_document", label: "Upload Document" },
  { value: "generate_config", label: "Generate Config" },
  { value: "run_simulation", label: "Run Simulation" },
  { value: "delete_document", label: "Delete Document" },
  { value: "transition", label: "Transition" },
  { value: "rollback", label: "Rollback" },
];

const RESOURCE_TYPE_OPTIONS = [
  { value: "", label: "All Resources" },
  { value: "document", label: "Document" },
  { value: "configuration", label: "Configuration" },
  { value: "simulation", label: "Simulation" },
];

const PAGE_SIZE = 20;

function formatTime(dateStr: string): string {
  const d = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMins = Math.floor(diffMs / 60000);

  if (diffMins < 1) return "Just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;

  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function SkeletonRow() {
  return (
    <div
      className="relative flex gap-4 px-6 py-4 border-b border-gray-800/40 animate-pulse"
      aria-hidden="true"
    >
      <div className="relative z-10 h-8 w-8 shrink-0 rounded-full bg-gray-800" />
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex items-start justify-between gap-2">
          <div className="space-y-1.5 flex-1">
            <div className="h-4 w-40 rounded bg-gray-800" />
            <div className="h-3 w-28 rounded bg-gray-800" />
          </div>
          <div className="h-3 w-16 rounded bg-gray-800" />
        </div>
        <div className="h-8 w-full rounded-lg bg-gray-800/60" />
      </div>
    </div>
  );
}

// biome-ignore lint/complexity/noExcessiveCognitiveComplexity: page component with filters, pagination, and timeline rendering
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

  function handleFilterChange(setter: (v: string) => void, value: string) {
    setter(value);
    setPage(1);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Audit Log</h1>
        <p className="mt-1 text-sm text-gray-400">Activity timeline and compliance tracking</p>
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-lg border border-red-500/20 bg-red-500/5 p-4 text-sm text-red-400"
        >
          Failed to load audit log. Please try refreshing.
        </div>
      )}

      {/* Summary stats */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3" aria-label="Audit summary">
        <div className="card p-4 text-center">
          <p className="text-2xl font-bold text-white" aria-label={`${total} total events`}>
            {isLoading ? (
              <span
                className="inline-block h-8 w-8 rounded bg-gray-800 animate-pulse"
                aria-hidden="true"
              />
            ) : (
              total
            )}
          </p>
          <p className="text-xs text-gray-400">Total Events</p>
        </div>
        <div className="card p-4 text-center">
          <p className="text-2xl font-bold text-indigo-400">
            {isLoading ? (
              <span
                className="inline-block h-8 w-8 rounded bg-gray-800 animate-pulse"
                aria-hidden="true"
              />
            ) : (
              entries.length
            )}
          </p>
          <p className="text-xs text-gray-400">This Page</p>
        </div>
        <div className="card p-4 text-center">
          <p className="text-2xl font-bold text-emerald-400">
            {isLoading ? (
              <span
                className="inline-block h-8 w-8 rounded bg-gray-800 animate-pulse"
                aria-hidden="true"
              />
            ) : (
              new Set(entries.map((e) => e.resource_type)).size
            )}
          </p>
          <p className="text-xs text-gray-400">Resource Types</p>
        </div>
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap gap-3">
        <div className="relative">
          <select
            value={actionFilter}
            onChange={(e) => handleFilterChange(setActionFilter, e.target.value)}
            className="appearance-none rounded-lg border border-gray-700 bg-gray-900 pl-3 pr-8 py-2 text-sm text-gray-300 focus:border-indigo-500 focus:outline-none hover:border-gray-600 transition-colors cursor-pointer"
            aria-label="Filter by action"
          >
            {ACTION_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-500" />
        </div>

        <div className="relative">
          <select
            value={resourceTypeFilter}
            onChange={(e) => handleFilterChange(setResourceTypeFilter, e.target.value)}
            className="appearance-none rounded-lg border border-gray-700 bg-gray-900 pl-3 pr-8 py-2 text-sm text-gray-300 focus:border-indigo-500 focus:outline-none hover:border-gray-600 transition-colors cursor-pointer"
            aria-label="Filter by resource type"
          >
            {RESOURCE_TYPE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-500" />
        </div>

        {(actionFilter || resourceTypeFilter) && (
          <button
            type="button"
            onClick={() => {
              setActionFilter("");
              setResourceTypeFilter("");
              setPage(1);
            }}
            className="rounded-lg border border-gray-700 px-3 py-2 text-sm text-gray-400 hover:text-gray-200 hover:border-gray-600 transition-colors"
          >
            Clear filters
          </button>
        )}
      </div>

      {/* Timeline */}
      <div className="card overflow-hidden">
        <div className="border-b border-gray-800 px-6 py-4">
          <div className="flex items-center gap-2">
            <Shield className="h-4 w-4 text-gray-400" aria-hidden="true" />
            <h2 className="font-semibold text-white">Activity Timeline</h2>
          </div>
        </div>

        {isLoading ? (
          <div aria-label="Loading audit entries">
            {Array.from({ length: 5 }).map((_, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: skeleton placeholders
              <SkeletonRow key={i} />
            ))}
          </div>
        ) : entries.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <div className="rounded-full bg-gray-800 p-4">
              <Shield className="h-6 w-6 text-gray-500" aria-hidden="true" />
            </div>
            <h2 className="mt-4 text-base font-semibold text-gray-300">No audit entries</h2>
            <p className="mt-1 text-sm text-gray-500">
              {actionFilter || resourceTypeFilter
                ? "No entries match the current filters."
                : "Activity will appear here as actions are performed."}
            </p>
          </div>
        ) : (
          <div className="relative">
            {/* Timeline line */}
            <div
              className="absolute left-[39px] top-0 bottom-0 w-px bg-gray-800"
              aria-hidden="true"
            />

            {entries.map((entry, i) => {
              const ActionIcon = actionIcons[entry.action] ?? Shield;

              return (
                <div
                  key={entry.id}
                  className={clsx(
                    "relative flex gap-4 px-6 py-4 transition-colors hover:bg-gray-800/20",
                    i !== entries.length - 1 && "border-b border-gray-800/40"
                  )}
                >
                  {/* Timeline dot */}
                  <div
                    className="relative z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-indigo-500/10"
                    aria-hidden="true"
                  >
                    <ActionIcon className="h-3.5 w-3.5 text-indigo-400" />
                  </div>

                  {/* Content */}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-white text-sm">
                            {entry.action
                              .replace(/_/g, " ")
                              .replace(/\b\w/g, (c) => c.toUpperCase())}
                          </span>
                        </div>
                        <div className="mt-1 flex items-center gap-2 text-xs text-gray-500">
                          <User className="h-3 w-3" aria-hidden="true" />
                          <span>{entry.actor}</span>
                          <span aria-hidden="true">&middot;</span>
                          <span>
                            {entry.resource_type}/{entry.resource_id}
                          </span>
                        </div>
                      </div>
                      <time dateTime={entry.created_at} className="shrink-0 text-xs text-gray-500">
                        {formatTime(entry.created_at)}
                      </time>
                    </div>

                    {entry.details && Object.keys(entry.details).length > 0 && (
                      <div className="mt-2 rounded-lg bg-gray-950/60 px-3 py-2 text-xs text-gray-400">
                        {Object.entries(entry.details).map(([k, v]) => (
                          <span key={k} className="mr-3">
                            <span className="text-gray-500">{k}:</span>{" "}
                            <span className="text-gray-300">{String(v)}</span>
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {!isLoading && total > PAGE_SIZE && (
          <div className="border-t border-gray-800 px-6">
            <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
          </div>
        )}
      </div>
    </div>
  );
}
