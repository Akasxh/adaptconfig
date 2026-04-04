import { searchApi } from "@/lib/api";
import { Search as SearchIcon } from "lucide-react";
import { useEffect, useState } from "react";

interface SearchResult {
  id: string;
  name: string;
  type: "adapter" | "configuration" | "simulation" | "document";
  score: number;
  description?: string;
}

interface SearchResponse {
  results: SearchResult[];
  total: number;
  query: string;
}

const TYPE_COLORS: Record<string, string> = {
  adapter: "bg-indigo-500/10 text-indigo-400",
  configuration: "bg-emerald-500/10 text-emerald-400",
  simulation: "bg-amber-500/10 text-amber-400",
  document: "bg-sky-500/10 text-sky-400",
};

function groupByType(results: SearchResult[]): Record<string, SearchResult[]> {
  const groups: Record<string, SearchResult[]> = {};
  for (const r of results) {
    if (!groups[r.type]) groups[r.type] = [];
    groups[r.type].push(r);
  }
  return groups;
}

export default function Search() {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query), 300);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (!debouncedQuery.trim()) {
      setResults([]);
      setSearched(false);
      return;
    }

    setIsLoading(true);
    setError(null);

    searchApi
      .search(debouncedQuery)
      .then((data: SearchResponse) => {
        setResults(data.results ?? []);
        setSearched(true);
      })
      .catch(() => {
        setError("Search failed. Please try again.");
        setResults([]);
      })
      .finally(() => setIsLoading(false));
  }, [debouncedQuery]);

  const groups = groupByType(results);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Search</h1>
        <p className="mt-1 text-sm text-gray-400">
          Search across adapters, configurations, simulations, and documents
        </p>
      </div>

      {/* Search input */}
      <div className="relative">
        <SearchIcon
          className="absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400"
          aria-hidden="true"
        />
        <input
          type="search"
          placeholder="Search..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="w-full rounded-lg border border-gray-700 bg-gray-900 py-3 pl-11 pr-4 text-sm text-white placeholder-gray-500 outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
        />
        {isLoading && (
          <div className="absolute right-4 top-1/2 -translate-y-1/2">
            <div
              className="h-4 w-4 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent"
              aria-label="Searching"
            />
          </div>
        )}
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-lg border border-red-500/20 bg-red-500/5 p-4 text-sm text-red-400"
        >
          {error}
        </div>
      )}

      {/* Results */}
      {searched && results.length === 0 && !isLoading && (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <div className="rounded-full bg-gray-800 p-4">
            <SearchIcon className="h-6 w-6 text-gray-500" aria-hidden="true" />
          </div>
          <h2 className="mt-4 text-base font-semibold text-gray-300">No results found</h2>
          <p className="mt-1 text-sm text-gray-500">Try a different search term.</p>
        </div>
      )}

      {Object.entries(groups).map(([type, items]) => (
        <div key={type} className="card overflow-hidden">
          <div className="border-b border-gray-800 px-6 py-4">
            <h2 className="font-semibold capitalize text-white">{type}s</h2>
          </div>
          <ul>
            {items.map((item, i) => (
              <li
                key={item.id}
                className={`flex items-center justify-between gap-4 px-6 py-4 hover:bg-gray-800/20 ${i !== items.length - 1 ? "border-b border-gray-800/40" : ""}`}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-3">
                    <span className="text-sm font-medium text-white">{item.name}</span>
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-medium ${TYPE_COLORS[item.type] ?? "bg-gray-700 text-gray-300"}`}
                    >
                      {item.type}
                    </span>
                  </div>
                  {item.description && (
                    <p className="mt-0.5 text-xs text-gray-500">{item.description}</p>
                  )}
                </div>

                {/* Relevance score bar */}
                <div
                  className="flex shrink-0 items-center gap-2"
                  aria-label={`Relevance: ${Math.round(item.score * 100)}%`}
                >
                  <div className="h-1.5 w-24 overflow-hidden rounded-full bg-gray-800">
                    <div
                      className="h-full rounded-full bg-indigo-500"
                      style={{ width: `${Math.min(100, Math.round(item.score * 100))}%` }}
                    />
                  </div>
                  <span className="w-8 text-right text-xs text-gray-500">
                    {Math.round(item.score * 100)}%
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
