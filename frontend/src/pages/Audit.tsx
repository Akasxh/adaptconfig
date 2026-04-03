import { auditApi } from "@/lib/api";
import type { AuditEntry } from "@/types";
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import {
  AlertTriangle,
  CheckCircle2,
  Play,
  Plug,
  RefreshCw,
  Settings,
  Shield,
  Upload,
  User,
  XCircle,
} from "lucide-react";

const actionIcons: Record<string, React.ComponentType<{ className?: string }>> = {
  "document.upload": Upload,
  "document.process": RefreshCw,
  "simulation.run": Play,
  "configuration.generate": Settings,
  "configuration.update": Settings,
  "adapter.sync": RefreshCw,
  "adapter.activate": Plug,
};

const statusConfig: Record<
  string,
  {
    icon: React.ComponentType<{ className?: string }>;
    cls: string;
    bg: string;
    label: string;
  }
> = {
  success: {
    icon: CheckCircle2,
    cls: "text-emerald-400",
    bg: "bg-emerald-500/10",
    label: "Success",
  },
  failure: { icon: XCircle, cls: "text-red-400", bg: "bg-red-500/10", label: "Failure" },
  warning: { icon: AlertTriangle, cls: "text-amber-400", bg: "bg-amber-500/10", label: "Warning" },
};

const defaultStatus = {
  icon: Shield,
  cls: "text-gray-400",
  bg: "bg-gray-500/10",
  label: "Unknown",
};

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

export default function Audit() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["audit"],
    queryFn: auditApi.list,
  });

  const entries: AuditEntry[] = isLoading ? [] : (data ?? []);

  const successCount = entries.filter((e) => e.status === "success").length;
  const warningCount = entries.filter((e) => e.status === "warning").length;
  const failureCount = entries.filter((e) => e.status === "failure").length;

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
      <div className="grid grid-cols-3 gap-4" aria-label="Audit summary">
        <div className="card p-4 text-center">
          <p
            className="text-2xl font-bold text-emerald-400"
            aria-label={`${successCount} successful`}
          >
            {isLoading ? (
              <span
                className="inline-block h-8 w-8 rounded bg-gray-800 animate-pulse"
                aria-hidden="true"
              />
            ) : (
              successCount
            )}
          </p>
          <p className="text-xs text-gray-400">Successful</p>
        </div>
        <div className="card p-4 text-center">
          <p className="text-2xl font-bold text-amber-400" aria-label={`${warningCount} warnings`}>
            {isLoading ? (
              <span
                className="inline-block h-8 w-8 rounded bg-gray-800 animate-pulse"
                aria-hidden="true"
              />
            ) : (
              warningCount
            )}
          </p>
          <p className="text-xs text-gray-400">Warnings</p>
        </div>
        <div className="card p-4 text-center">
          <p className="text-2xl font-bold text-red-400" aria-label={`${failureCount} failures`}>
            {isLoading ? (
              <span
                className="inline-block h-8 w-8 rounded bg-gray-800 animate-pulse"
                aria-hidden="true"
              />
            ) : (
              failureCount
            )}
          </p>
          <p className="text-xs text-gray-400">Failures</p>
        </div>
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
              Activity will appear here as actions are performed.
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
              const st = statusConfig[entry.status] ?? defaultStatus;
              const StatusIcon = st.icon;
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
                    className={clsx(
                      "relative z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
                      st.bg
                    )}
                    aria-hidden="true"
                  >
                    <ActionIcon className={clsx("h-3.5 w-3.5", st.cls)} />
                  </div>

                  {/* Content */}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-white text-sm">
                            {entry.action
                              .replace(".", " ")
                              .replace(/\b\w/g, (c) => c.toUpperCase())}
                          </span>
                          <StatusIcon
                            className={clsx("h-3.5 w-3.5", st.cls)}
                            aria-label={st.label}
                          />
                        </div>
                        <div className="mt-1 flex items-center gap-2 text-xs text-gray-500">
                          <User className="h-3 w-3" aria-hidden="true" />
                          <span>{entry.user}</span>
                          <span aria-hidden="true">&middot;</span>
                          <span>
                            {entry.entity_type}/{entry.entity_id}
                          </span>
                        </div>
                      </div>
                      <time dateTime={entry.timestamp} className="shrink-0 text-xs text-gray-500">
                        {formatTime(entry.timestamp)}
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
      </div>
    </div>
  );
}
