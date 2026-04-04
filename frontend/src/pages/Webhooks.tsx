import { webhooksApi } from "@/lib/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, Webhook, Zap } from "lucide-react";
import { useState } from "react";

const ALL_EVENTS = [
  "adapter.created",
  "adapter.updated",
  "configuration.generated",
  "configuration.validated",
  "simulation.completed",
  "simulation.failed",
  "document.uploaded",
  "document.processed",
];

interface WebhookEntry {
  id: string;
  url: string;
  events: string[];
  active: boolean;
  created_at: string;
}

interface TestResult {
  success: boolean;
  status_code?: number;
  error?: string;
}

interface WebhookRowProps {
  wh: WebhookEntry;
  isLast: boolean;
  testResult?: TestResult;
  testPending: boolean;
  deleteConfirm: string | null;
  deletePending: boolean;
  onTest: (id: string) => void;
  onDeleteRequest: (id: string) => void;
  onDeleteConfirm: (id: string) => void;
  onDeleteCancel: () => void;
}

function WebhookRow({
  wh,
  isLast,
  testResult,
  testPending,
  deleteConfirm,
  deletePending,
  onTest,
  onDeleteRequest,
  onDeleteConfirm,
  onDeleteCancel,
}: WebhookRowProps) {
  return (
    <li className={`px-6 py-4 hover:bg-gray-800/20 ${isLast ? "" : "border-b border-gray-800/40"}`}>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-white truncate">{wh.url}</span>
            <span
              className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${
                wh.active ? "bg-emerald-500/10 text-emerald-400" : "bg-gray-700 text-gray-400"
              }`}
            >
              {wh.active ? "Active" : "Inactive"}
            </span>
          </div>
          <div className="mt-1 flex flex-wrap gap-1">
            {wh.events.map((ev) => (
              <span key={ev} className="rounded bg-gray-800 px-1.5 py-0.5 text-xs text-gray-400">
                {ev}
              </span>
            ))}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {testResult && (
            <span className={`text-xs ${testResult.success ? "text-emerald-400" : "text-red-400"}`}>
              {testResult.success
                ? `OK ${testResult.status_code ?? ""}`
                : (testResult.error ?? "Failed")}
            </span>
          )}

          <button
            type="button"
            onClick={() => onTest(wh.id)}
            disabled={testPending}
            title="Send test event"
            className="flex items-center gap-1.5 rounded-lg border border-gray-700 px-3 py-1.5 text-xs font-medium text-gray-300 hover:bg-gray-800 disabled:opacity-50 transition-colors"
          >
            <Zap className="h-3 w-3" />
            Test
          </button>

          {deleteConfirm === wh.id ? (
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => onDeleteConfirm(wh.id)}
                disabled={deletePending}
                className="rounded-lg bg-red-600/80 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-600 disabled:opacity-50 transition-colors"
              >
                Confirm
              </button>
              <button
                type="button"
                onClick={onDeleteCancel}
                className="rounded-lg border border-gray-700 px-3 py-1.5 text-xs font-medium text-gray-300 hover:bg-gray-800 transition-colors"
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => onDeleteRequest(wh.id)}
              title="Delete webhook"
              className="flex items-center justify-center rounded-lg border border-gray-700 p-1.5 text-gray-400 hover:border-red-500/50 hover:text-red-400 transition-colors"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>
    </li>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="rounded-full bg-gray-800 p-4">
        <Webhook className="h-6 w-6 text-gray-500" aria-hidden="true" />
      </div>
      <h2 className="mt-4 text-base font-semibold text-gray-300">No webhooks registered</h2>
      <p className="mt-1 text-sm text-gray-500">Add a webhook below to start receiving events.</p>
    </div>
  );
}

export default function Webhooks() {
  const queryClient = useQueryClient();

  const [showForm, setShowForm] = useState(false);
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [selectedEvents, setSelectedEvents] = useState<string[]>([]);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});

  const { data, isLoading, error } = useQuery({
    queryKey: ["webhooks"],
    queryFn: () => webhooksApi.list(),
  });

  const webhooks: WebhookEntry[] = data?.data ?? data ?? [];

  const createMutation = useMutation({
    mutationFn: (payload: { url: string; events: string[]; secret?: string }) =>
      webhooksApi.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["webhooks"] });
      setShowForm(false);
      setUrl("");
      setSecret("");
      setSelectedEvents([]);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => webhooksApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["webhooks"] });
      setDeleteConfirm(null);
    },
  });

  const testMutation = useMutation({
    mutationFn: (id: string) => webhooksApi.test(id),
    onSuccess: (result, id) => {
      setTestResults((prev) => ({ ...prev, [id]: result?.data ?? result }));
    },
    onError: (_err, id) => {
      setTestResults((prev) => ({ ...prev, [id]: { success: false, error: "Request failed" } }));
    },
  });

  function toggleEvent(event: string) {
    setSelectedEvents((prev) =>
      prev.includes(event) ? prev.filter((e) => e !== event) : [...prev, event]
    );
  }

  function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!url || selectedEvents.length === 0) return;
    createMutation.mutate({ url, events: selectedEvents, secret: secret || undefined });
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Webhooks</h1>
          <p className="mt-1 text-sm text-gray-400">
            Manage event notifications to external endpoints
          </p>
        </div>
        <button
          type="button"
          onClick={() => setShowForm((v) => !v)}
          className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 transition-colors"
        >
          <Plus className="h-4 w-4" />
          Add Webhook
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleCreate} className="card p-6 space-y-4">
          <h2 className="font-semibold text-white">New Webhook</h2>

          <div>
            <label htmlFor="webhook-url" className="block text-xs font-medium text-gray-400 mb-1">
              URL <span className="text-red-400">*</span>
            </label>
            <input
              id="webhook-url"
              type="url"
              required
              placeholder="https://example.com/webhook"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className="w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-white placeholder-gray-500 outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
            />
          </div>

          <div>
            <label
              htmlFor="webhook-secret"
              className="block text-xs font-medium text-gray-400 mb-1"
            >
              Secret (optional)
            </label>
            <input
              id="webhook-secret"
              type="password"
              placeholder="Signing secret"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              className="w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-white placeholder-gray-500 outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
            />
          </div>

          <div>
            <p className="block text-xs font-medium text-gray-400 mb-2">
              Events <span className="text-red-400">*</span>
            </p>
            <div className="grid grid-cols-2 gap-2">
              {ALL_EVENTS.map((event) => (
                <label
                  key={event}
                  className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={selectedEvents.includes(event)}
                    onChange={() => toggleEvent(event)}
                    className="h-4 w-4 rounded border-gray-600 bg-gray-800 accent-indigo-500"
                  />
                  {event}
                </label>
              ))}
            </div>
          </div>

          {createMutation.isError && (
            <p className="text-sm text-red-400">Failed to create webhook. Please try again.</p>
          )}

          <div className="flex gap-3">
            <button
              type="submit"
              disabled={createMutation.isPending || !url || selectedEvents.length === 0}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {createMutation.isPending ? "Creating..." : "Create Webhook"}
            </button>
            <button
              type="button"
              onClick={() => setShowForm(false)}
              className="rounded-lg border border-gray-700 px-4 py-2 text-sm font-medium text-gray-300 hover:bg-gray-800 transition-colors"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {error && (
        <div
          role="alert"
          className="rounded-lg border border-red-500/20 bg-red-500/5 p-4 text-sm text-red-400"
        >
          Failed to load webhooks. Please try refreshing.
        </div>
      )}

      <div className="card overflow-hidden">
        <div className="border-b border-gray-800 px-6 py-4">
          <div className="flex items-center gap-2">
            <Webhook className="h-4 w-4 text-gray-400" aria-hidden="true" />
            <h2 className="font-semibold text-white">Registered Webhooks</h2>
          </div>
        </div>

        {isLoading ? (
          <div className="space-y-0" aria-label="Loading webhooks">
            {(["s1", "s2", "s3"] as const).map((key) => (
              <div
                key={key}
                className="flex items-center gap-4 px-6 py-4 border-b border-gray-800/40 animate-pulse"
              >
                <div className="flex-1 space-y-2">
                  <div className="h-4 w-64 rounded bg-gray-800" />
                  <div className="h-3 w-40 rounded bg-gray-800" />
                </div>
                <div className="h-8 w-16 rounded bg-gray-800" />
              </div>
            ))}
          </div>
        ) : webhooks.length === 0 ? (
          <EmptyState />
        ) : (
          <ul>
            {webhooks.map((wh, i) => (
              <WebhookRow
                key={wh.id}
                wh={wh}
                isLast={i === webhooks.length - 1}
                testResult={testResults[wh.id]}
                testPending={testMutation.isPending && testMutation.variables === wh.id}
                deleteConfirm={deleteConfirm}
                deletePending={deleteMutation.isPending}
                onTest={(id) => testMutation.mutate(id)}
                onDeleteRequest={(id) => setDeleteConfirm(id)}
                onDeleteConfirm={(id) => deleteMutation.mutate(id)}
                onDeleteCancel={() => setDeleteConfirm(null)}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
