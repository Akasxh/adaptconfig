import { webhooksApi } from "@/lib/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, Webhook, Zap } from "lucide-react";
import { useState } from "react";

const ALL_EVENTS = [
  "config.created", "config.updated", "config.deployed", "simulation.passed",
  "simulation.failed", "document.uploaded", "document.parsed", "webhook.test",
] as const;

type EventType = (typeof ALL_EVENTS)[number];

interface WebhookEntry { id: string; tenant_id: string; url: string; events: string[]; is_active: boolean; created_at: string; }
interface TestResult { id: string; webhook_id: string; event_type: string; status: string; response_code: number | null; attempts: number; created_at: string; }

const C = {
  base: "var(--color-bg-base)", elevated: "var(--color-bg-elevated)", raised: "var(--color-bg-raised)",
  border: "var(--color-border)", borderStrong: "var(--color-border-strong)",
  brand: "var(--color-brand)", brandLight: "var(--color-brand-light)",
  teal: "var(--color-teal)", primary: "var(--color-text-primary)",
  secondary: "var(--color-text-secondary)", muted: "var(--color-text-muted)",
  error: "var(--color-error-text)",
} as const;

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function trunc(url: string, n = 42) { return url.length > n ? `${url.slice(0, n)}…` : url; }

// ── Add form ──────────────────────────────────────────────────────────────────

function AddForm({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [events, setEvents] = useState<EventType[]>([]);
  const toggle = (e: EventType) => setEvents((p) => p.includes(e) ? p.filter((x) => x !== e) : [...p, e]);

  const mut = useMutation({
    mutationFn: (p: { url: string; events: string[]; secret: string }) => webhooksApi.create(p),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["webhooks"] }); onClose(); },
  });

  const inputStyle = {
    width: "100%", background: C.base, border: `1px solid ${C.borderStrong}`,
    borderRadius: 6, padding: "8px 12px", color: C.primary, outline: "none", boxSizing: "border-box" as const,
  };

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); if (url && events.length && secret) mut.mutate({ url, events, secret }); }}
      className="card animate-fade-in"
      style={{ padding: "20px 24px", display: "flex", flexDirection: "column", gap: 16 }}
    >
      <h2 style={{ fontSize: 15, fontWeight: 600, color: C.primary, margin: 0 }}>New Webhook</h2>

      <div>
        <label htmlFor="wh-url" className="section-label" style={{ padding: 0, display: "block", marginBottom: 4 }}>
          Endpoint URL <span style={{ color: C.error }}>*</span>
        </label>
        <input id="wh-url" type="url" required placeholder="https://example.com/webhook"
          value={url} onChange={(e) => setUrl(e.target.value)} className="mono" style={inputStyle} />
      </div>

      <div>
        <label htmlFor="wh-secret" className="section-label" style={{ padding: 0, display: "block", marginBottom: 4 }}>
          Signing Secret <span style={{ color: C.muted }}>(optional)</span>
        </label>
        <input id="wh-secret" type="password" placeholder="Signing secret"
          value={secret} onChange={(e) => setSecret(e.target.value)} style={inputStyle} />
      </div>

      <div>
        <p className="section-label" style={{ padding: 0, marginBottom: 8 }}>
          Events <span style={{ color: C.error }}>*</span>
        </p>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6 }}>
          {ALL_EVENTS.map((ev) => {
            const on = events.includes(ev);
            return (
              <button key={ev} type="button" onClick={() => toggle(ev)} style={{
                padding: "4px 10px", borderRadius: 9999, fontSize: 11, fontWeight: 600,
                letterSpacing: "0.04em", cursor: "pointer", transition: "all 120ms ease",
                border: on ? `1px solid rgba(29,111,164,0.6)` : `1px solid ${C.border}`,
                background: on ? "rgba(29,111,164,0.18)" : "transparent",
                color: on ? C.brandLight : C.secondary,
              }}>{ev}</button>
            );
          })}
        </div>
      </div>

      {mut.isError && <p style={{ fontSize: 13, color: C.error, margin: 0 }}>Failed to create — check URL and retry.</p>}

      <div style={{ display: "flex", gap: 8 }}>
        <button type="submit" className="btn-primary" disabled={mut.isPending || !url || !events.length}>
          {mut.isPending ? "Creating…" : "Create Webhook"}
        </button>
        <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
      </div>
    </form>
  );
}

// ── Webhook row ───────────────────────────────────────────────────────────────

interface RowProps {
  wh: WebhookEntry; isLast: boolean; testResult: TestResult | null;
  testPending: boolean; deleteConfirm: boolean; deletePending: boolean;
  onTest(): void; onDeleteRequest(): void; onDeleteConfirm(): void; onDeleteCancel(): void;
}

function WebhookRow({ wh, isLast, testResult, testPending, deleteConfirm, deletePending, onTest, onDeleteRequest, onDeleteConfirm, onDeleteCancel }: RowProps) {
  const visible = wh.events.slice(0, 3);
  const overflow = wh.events.length - 3;
  const testOk = testResult !== null && testResult.response_code !== null && testResult.response_code >= 200 && testResult.response_code < 300;

  return (
    <tr className="table-row" style={{ borderBottom: isLast ? "none" : undefined }}>
      <td style={{ padding: "0 16px", maxWidth: 320 }}>
        <span className="mono" style={{ color: C.primary, fontSize: 13 }} title={wh.url}>{trunc(wh.url)}</span>
      </td>
      <td style={{ padding: "0 16px" }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          {visible.map((ev) => <span key={ev} className="badge-blue">{ev}</span>)}
          {overflow > 0 && <span className="badge-gray">+{overflow} more</span>}
        </div>
      </td>
      <td style={{ padding: "0 16px" }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span style={{ width: 7, height: 7, borderRadius: "50%", flexShrink: 0, background: wh.is_active ? C.teal : C.muted }} />
          <span style={{ fontSize: 12, fontWeight: 500, color: wh.is_active ? C.teal : C.muted }}>{wh.is_active ? "Active" : "Inactive"}</span>
        </span>
      </td>
      <td style={{ padding: "0 16px", color: C.secondary, fontSize: 13 }}>{fmtDate(wh.created_at)}</td>
      <td style={{ padding: "0 16px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {testResult !== null && (
            <span className="animate-fade-in" style={{ fontSize: 11, fontWeight: 600, color: testOk ? C.teal : C.error }}>
              {testOk ? `${testResult.response_code ?? ""} OK` : testResult.response_code ? `${testResult.response_code} Err` : "Failed"}
            </span>
          )}
          {deleteConfirm ? (
            <>
              <button type="button" onClick={onDeleteConfirm} disabled={deletePending} style={{
                fontSize: 12, fontWeight: 600, padding: "4px 10px", borderRadius: 6,
                border: "1px solid rgba(220,38,38,0.4)", background: "rgba(220,38,38,0.12)",
                color: C.error, cursor: deletePending ? "not-allowed" : "pointer", opacity: deletePending ? 0.5 : 1,
              }}>Confirm</button>
              <button type="button" className="btn-secondary" onClick={onDeleteCancel} style={{ padding: "4px 10px", fontSize: 12 }}>Cancel</button>
            </>
          ) : (
            <>
              <button type="button" onClick={onTest} disabled={testPending} className="btn-secondary" style={{ padding: "4px 10px", fontSize: 12, gap: 4 }}>
                <Zap style={{ width: 12, height: 12 }} />{testPending ? "Testing…" : "Test"}
              </button>
              <button type="button" onClick={onDeleteRequest} className="btn-danger" style={{ padding: "4px 8px" }} title="Delete webhook">
                <Trash2 style={{ width: 13, height: 13 }} />
              </button>
            </>
          )}
        </div>
      </td>
    </tr>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function Webhooks() {
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});

  const { data, isLoading, error } = useQuery({ queryKey: ["webhooks"], queryFn: () => webhooksApi.list() });
  const rawData = data as { data?: WebhookEntry[] } | WebhookEntry[] | null | undefined;
  const webhooks: WebhookEntry[] = (Array.isArray(rawData) ? rawData : rawData?.data) ?? [];

  const deleteMut = useMutation({
    mutationFn: (id: string) => webhooksApi.delete(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["webhooks"] }); setDeleteConfirm(null); },
  });

  const testMut = useMutation({
    mutationFn: (id: string) => webhooksApi.test(id),
    onSuccess: (res, id) => setTestResults((p) => ({ ...p, [id]: ((res as { data?: TestResult })?.data ?? res) as TestResult })),
    onError: (_e, id) => setTestResults((p) => ({
      ...p, [id]: { id: "", webhook_id: id, event_type: "webhook.test", status: "failed", response_code: null, attempts: 1, created_at: new Date().toISOString() },
    })),
  });

  return (
    <div className="animate-fade-in" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: C.primary, margin: 0 }}>Webhooks</h1>
          <p style={{ fontSize: 13, color: C.secondary, margin: "4px 0 0" }}>
            Deliver real-time event notifications to external endpoints
          </p>
        </div>
        <button type="button" className="btn-primary" onClick={() => setShowForm((v) => !v)}>
          <Plus style={{ width: 14, height: 14 }} />{showForm ? "Cancel" : "Add Webhook"}
        </button>
      </div>

      {showForm && <AddForm onClose={() => setShowForm(false)} />}

      {error && (
        <div role="alert" style={{ borderRadius: 8, border: "1px solid rgba(220,38,38,0.2)", background: "rgba(220,38,38,0.06)", padding: "12px 16px", fontSize: 13, color: C.error }}>
          Failed to load webhooks — please refresh.
        </div>
      )}

      <div className="card" style={{ overflow: "hidden" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "12px 16px", borderBottom: `1px solid ${C.border}` }}>
          <Webhook style={{ width: 15, height: 15, color: C.muted }} />
          <span style={{ fontSize: 15, fontWeight: 600, color: C.primary }}>Registered Webhooks</span>
          {!isLoading && <span className="badge-gray" style={{ marginLeft: 4 }}>{webhooks.length}</span>}
        </div>

        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr className="table-header">
              <th style={{ textAlign: "left", padding: "0 16px", width: "30%" }}>URL</th>
              <th style={{ textAlign: "left", padding: "0 16px", width: "28%" }}>Events</th>
              <th style={{ textAlign: "left", padding: "0 16px", width: "10%" }}>Status</th>
              <th style={{ textAlign: "left", padding: "0 16px", width: "14%" }}>Created</th>
              <th style={{ textAlign: "left", padding: "0 16px", width: "18%" }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {isLoading
              ? [0, 1, 2].map((i) => (
                <tr key={i} style={{ height: 48, borderBottom: `1px solid ${C.border}` }}>
                  {[300, 180, 60, 80, 110].map((w, ci) => (
                    <td key={ci} style={{ padding: "0 16px" }}>
                      <div style={{ height: 12, width: w, borderRadius: 4, background: C.raised }} className="animate-pulse" />
                    </td>
                  ))}
                </tr>
              ))
              : webhooks.length === 0
                ? (
                  <tr><td colSpan={5}>
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "56px 24px", gap: 12 }}>
                      <div style={{ width: 44, height: 44, borderRadius: "50%", background: C.raised, display: "flex", alignItems: "center", justifyContent: "center" }}>
                        <Webhook style={{ width: 20, height: 20, color: C.muted }} />
                      </div>
                      <p style={{ fontSize: 15, fontWeight: 600, color: C.primary, margin: 0 }}>No webhooks configured</p>
                      <p style={{ fontSize: 13, color: C.secondary, margin: 0 }}>Add a webhook to start receiving real-time events.</p>
                      <button type="button" className="btn-primary" onClick={() => setShowForm(true)} style={{ marginTop: 4 }}>
                        <Plus style={{ width: 14, height: 14 }} />Add Webhook
                      </button>
                    </div>
                  </td></tr>
                )
                : webhooks.map((wh, i) => (
                  <WebhookRow
                    key={wh.id} wh={wh} isLast={i === webhooks.length - 1}
                    testResult={testResults[wh.id] ?? null}
                    testPending={testMut.isPending && testMut.variables === wh.id}
                    deleteConfirm={deleteConfirm === wh.id}
                    deletePending={deleteMut.isPending && deleteMut.variables === wh.id}
                    onTest={() => testMut.mutate(wh.id)}
                    onDeleteRequest={() => setDeleteConfirm(wh.id)}
                    onDeleteConfirm={() => deleteMut.mutate(wh.id)}
                    onDeleteCancel={() => setDeleteConfirm(null)}
                  />
                ))
            }
          </tbody>
        </table>
      </div>
    </div>
  );
}
