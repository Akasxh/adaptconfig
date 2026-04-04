import { searchApi } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Search as SearchIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

interface SearchResult {
  id: string;
  name: string;
  type: "adapter" | "configuration" | "simulation";
  score: number;
  details: Record<string, unknown>;
}

interface SearchData {
  query: string;
  total: number;
  adapters: SearchResult[];
  configurations: SearchResult[];
  simulations: SearchResult[];
}

const SECTIONS: { key: keyof Omit<SearchData, "query" | "total">; label: string }[] = [
  { key: "adapters", label: "Adapters" },
  { key: "configurations", label: "Configurations" },
  { key: "simulations", label: "Simulations" },
];

function scoreColor(score: number): string {
  if (score >= 0.7) return "var(--teal, #0fb89a)";
  if (score >= 0.4) return "#e6a817";
  return "#e05252";
}

function useDebounce(value: string, ms: number): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

const badgeStyle = (type: string): React.CSSProperties => {
  const map: Record<string, { bg: string; color: string }> = {
    adapter: { bg: "rgba(29,111,164,0.18)", color: "#2d8fce" },
    configuration: { bg: "rgba(15,184,154,0.15)", color: "#0fb89a" },
    simulation: { bg: "rgba(230,168,23,0.15)", color: "#e6a817" },
  };
  const t = map[type] ?? { bg: "rgba(255,255,255,0.07)", color: "#8fa3c0" };
  return {
    background: t.bg,
    color: t.color,
    borderRadius: 99,
    padding: "2px 8px",
    fontSize: 11,
    fontWeight: 600,
    letterSpacing: "0.06em",
    textTransform: "uppercase",
  };
};

const getRoute = (item: SearchResult): string => {
  switch (item.type) {
    case "adapter": return "/adapters";
    case "configuration": return "/configurations";
    case "simulation": return "/simulations";
    default: return "/";
  }
};

export default function Search() {
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebounce(query, 300);
  const navigate = useNavigate();

  const { data, isLoading, isError } = useQuery<{ data: SearchData }>({
    queryKey: ["search", debouncedQuery],
    queryFn: () => searchApi.search(debouncedQuery.trim()) as Promise<{ data: SearchData }>,
    enabled: !!debouncedQuery.trim(),
    staleTime: 30_000,
  });

  const results = data?.data;
  const total = results?.total ?? 0;
  const hasResults = !!results && total > 0;
  const searched = !!debouncedQuery.trim() && !isLoading;

  return (
    <div className="animate-fade-in space-y-6" style={{ maxWidth: 860, margin: "0 auto" }}>
      {/* Header */}
      <div>
        <h1 style={{ fontSize: 22, fontWeight: 600, color: "#e8edf5", margin: 0 }}>Search</h1>
        <p style={{ fontSize: 13, color: "#5d7a99", marginTop: 4 }}>
          Search across adapters, configurations, and simulations
        </p>
      </div>

      {/* Search input */}
      <div style={{ position: "relative" }}>
        <SearchIcon
          size={16}
          style={{ position: "absolute", left: 16, top: "50%", transform: "translateY(-50%)", color: "#5d7a99", pointerEvents: "none" }}
        />
        <input
          type="search"
          placeholder="Type to search..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{
            width: "100%",
            boxSizing: "border-box",
            background: "#0f1724",
            border: "1.5px solid #1e2d47",
            borderRadius: 10,
            padding: "14px 44px",
            fontSize: 15,
            color: "#e8edf5",
            outline: "none",
            transition: "border-color 0.15s",
          }}
          onFocus={(e) => { e.currentTarget.style.borderColor = "#1d6fa4"; }}
          onBlur={(e) => { e.currentTarget.style.borderColor = "#1e2d47"; }}
        />
        {isLoading && (
          <Loader2
            size={16}
            style={{ position: "absolute", right: 16, top: "50%", transform: "translateY(-50%)", color: "#1d6fa4", animation: "spin 1s linear infinite" }}
          />
        )}
      </div>

      {/* Error */}
      {isError && (
        <div role="alert" style={{ background: "rgba(224,82,82,0.08)", border: "1px solid rgba(224,82,82,0.25)", borderRadius: 8, padding: "12px 16px", fontSize: 13, color: "#e05252" }}>
          Search failed. Please try again.
        </div>
      )}

      {/* Quick stats */}
      {hasResults && (
        <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
          <span style={{ fontSize: 13, color: "#5d7a99" }}>
            <span style={{ color: "#e8edf5", fontWeight: 600 }}>{total}</span> result{total !== 1 ? "s" : ""} for{" "}
            <span style={{ color: "#2d8fce" }}>&ldquo;{results.query}&rdquo;</span>
          </span>
          {SECTIONS.map(({ key, label }) => {
            const count = results[key].length;
            return count > 0 ? (
              <span key={key} style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.06em", textTransform: "uppercase", color: "#5d7a99" }}>
                {label}: <span style={{ color: "#e8edf5" }}>{count}</span>
              </span>
            ) : null;
          })}
        </div>
      )}

      {/* Empty states */}
      {!debouncedQuery.trim() && !isLoading && (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "64px 0", textAlign: "center" }}>
          <div style={{ background: "#0f1724", border: "1px solid #1e2d47", borderRadius: "50%", padding: 20, marginBottom: 20 }}>
            <SearchIcon size={28} style={{ color: "#2a3f5e" }} />
          </div>
          <p style={{ fontSize: 15, fontWeight: 600, color: "#8fa3c0", margin: 0 }}>Search across adapters, configurations, and simulations</p>
          <p style={{ fontSize: 13, color: "#3d5470", marginTop: 6 }}>Type a keyword to get started</p>
        </div>
      )}

      {searched && !isLoading && !hasResults && !isError && (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "64px 0", textAlign: "center" }}>
          <div style={{ background: "#0f1724", border: "1px solid #1e2d47", borderRadius: "50%", padding: 20, marginBottom: 20 }}>
            <SearchIcon size={28} style={{ color: "#2a3f5e" }} />
          </div>
          <p style={{ fontSize: 15, fontWeight: 600, color: "#8fa3c0", margin: 0 }}>No results found for &ldquo;{debouncedQuery}&rdquo;</p>
          <p style={{ fontSize: 13, color: "#3d5470", marginTop: 6 }}>Try a different search term or check your spelling</p>
        </div>
      )}

      {/* Grouped results */}
      {hasResults && SECTIONS.map(({ key, label }) => {
        const items = results[key];
        if (!items.length) return null;
        return (
          <div key={key} className="card" style={{ overflow: "hidden" }}>
            <div style={{ padding: "12px 20px", borderBottom: "1px solid #1e2d47", display: "flex", alignItems: "center", gap: 10 }}>
              <span className="section-label">{label}</span>
              <span style={{ fontSize: 11, fontWeight: 600, background: "#18243a", color: "#5d7a99", borderRadius: 99, padding: "1px 8px" }}>{items.length}</span>
            </div>
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {items.map((item, i) => {
                const desc = typeof item.details?.description === "string" ? item.details.description : undefined;
                const pct = Math.min(100, Math.round(item.score * 100));
                return (
                  <li
                    key={item.id}
                    className="card-hover"
                    onClick={() => navigate(getRoute(item))}
                    role="link"
                    tabIndex={0}
                    onKeyDown={(e) => { if (e.key === "Enter") navigate(getRoute(item)); }}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 16,
                      padding: "14px 20px",
                      cursor: "pointer",
                      borderBottom: i < items.length - 1 ? "1px solid rgba(30,45,71,0.5)" : "none",
                    }}
                  >
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                        <span style={{ fontSize: 14, fontWeight: 600, color: "#dde4f0" }}>{item.name}</span>
                        <span style={badgeStyle(item.type)}>{item.type}</span>
                      </div>
                      {desc && (
                        <p style={{ fontSize: 12, color: "#5d7a99", margin: "4px 0 0", overflow: "hidden", whiteSpace: "nowrap", textOverflow: "ellipsis" }}>
                          {desc}
                        </p>
                      )}
                    </div>

                    {/* Relevance score */}
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }} aria-label={`Relevance: ${pct}%`}>
                      <div style={{ width: 80, height: 4, background: "#18243a", borderRadius: 99, overflow: "hidden" }}>
                        <div style={{ width: `${pct}%`, height: "100%", background: scoreColor(item.score), borderRadius: 99, transition: "width 0.3s" }} />
                      </div>
                      <span className="mono" style={{ fontSize: 11, color: "#5d7a99", width: 30, textAlign: "right" }}>{pct}%</span>
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </div>
  );
}
