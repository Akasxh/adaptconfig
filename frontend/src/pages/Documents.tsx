import { useToast } from "@/components/Toast";
import { documentsApi } from "@/lib/api";
import type { Document } from "@/types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  Clock,
  FileCode,
  FileSpreadsheet,
  FileText,
  Layers,
  Link2,
  Loader2,
  Search,
  Shield,
  Upload,
  X,
} from "lucide-react";
import { useCallback, useState } from "react";
import { useDropzone } from "react-dropzone";
import type { FileRejection } from "react-dropzone";

const statusConfig: Record<
  string,
  { label: string; icon: React.ComponentType<{ className?: string }>; cls: string }
> = {
  pending: { label: "Pending", icon: Clock, cls: "badge-yellow" },
  processing: { label: "Processing", icon: Loader2, cls: "badge-blue" },
  parsing: { label: "Parsing", icon: Loader2, cls: "badge-blue" },
  completed: { label: "Completed", icon: CheckCircle2, cls: "badge-green" },
  done: { label: "Done", icon: CheckCircle2, cls: "badge-green" },
  parsed: { label: "Parsed", icon: CheckCircle2, cls: "badge-green" },
  failed: { label: "Failed", icon: AlertCircle, cls: "badge-red" },
};

function methodBadgeCls(method: string): string {
  if (method === "GET") return "bg-emerald-500/15 text-emerald-400";
  if (method === "POST") return "bg-blue-500/15 text-blue-400";
  if (method === "PUT" || method === "PATCH") return "bg-amber-500/15 text-amber-400";
  if (method === "DELETE") return "bg-red-500/15 text-red-400";
  return "bg-gray-500/15 text-gray-400";
}

function fileIcon(fileType: string) {
  if (fileType === "json" || fileType === "yaml") return FileCode;
  if (fileType === "xlsx" || fileType === "csv") return FileSpreadsheet;
  return FileText;
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

type ParsedResult = {
  endpoints?: Array<{ path: string; method: string; description?: string; summary?: string }>;
  fields?: Array<{
    name: string;
    data_type?: string;
    is_required?: boolean;
    description?: string;
    sample_value?: string;
    source_section?: string;
  }>;
  auth_requirements?: Array<{
    auth_type: string;
    details?: { name?: string; scheme?: string; in?: string };
  }>;
  title?: string;
  summary?: string;
  services_identified?: string[];
  confidence_score?: number;
  sections?: Record<string, string>;
  raw_entities?: string[];
  parse_errors?: string[];
};

type DocumentDetail = Document & {
  parsed_result?: ParsedResult;
};

type DetailTab = "endpoints" | "fields" | "auth" | "summary" | "raw";

// biome-ignore lint/complexity/noExcessiveCognitiveComplexity: multi-tab modal with conditional rendering
function DetailModal({
  doc,
  onClose,
}: {
  doc: Document;
  onClose: () => void;
}) {
  const [activeTab, setActiveTab] = useState<DetailTab>("summary");
  const [showRaw, setShowRaw] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["document", doc.id],
    queryFn: () => documentsApi.get(doc.id),
  });

  const detail = data?.data as DocumentDetail | null;
  const parsed = detail?.parsed_result;

  const tabs: { id: DetailTab; label: string; icon: React.ElementType }[] = [
    { id: "summary", label: "Summary", icon: FileText },
    {
      id: "endpoints",
      label: `Endpoints${parsed?.endpoints?.length ? ` (${parsed.endpoints.length})` : ""}`,
      icon: Link2,
    },
    {
      id: "fields",
      label: `Fields${parsed?.fields?.length ? ` (${parsed.fields.length})` : ""}`,
      icon: Layers,
    },
    { id: "auth", label: "Auth", icon: Shield },
  ];

  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: modal overlay, keyboard handled via close button
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div className="relative z-10 w-full max-w-3xl max-h-[85vh] flex flex-col rounded-xl border border-gray-800 bg-gray-950 shadow-2xl">
        {/* Header */}
        <div className="flex items-center gap-3 border-b border-gray-800 px-6 py-4">
          <div className="rounded-lg bg-gray-800 p-2">
            {(() => {
              const IconFile = fileIcon(doc.file_type);
              return <IconFile className="h-4 w-4 text-gray-400" />;
            })()}
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="truncate font-semibold text-white">{doc.filename}</h2>
            <p className="text-xs text-gray-500">
              {doc.file_type.toUpperCase()} &middot; {formatDate(doc.created_at)}
            </p>
          </div>
          {(() => {
            const st = statusConfig[doc.status] ?? {
              label: doc.status,
              icon: Clock,
              cls: "badge-gray",
            };
            const StatusIcon = st.icon;
            return (
              <span className={st.cls}>
                <StatusIcon
                  className={clsx("mr-1 h-3 w-3", doc.status === "processing" && "animate-spin")}
                />
                {st.label}
              </span>
            );
          })()}
          <button
            type="button"
            onClick={onClose}
            className="ml-2 text-gray-500 hover:text-gray-300 transition-colors"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {isLoading ? (
          <div className="flex flex-1 items-center justify-center p-12">
            <Loader2 className="h-6 w-6 animate-spin text-indigo-400" />
          </div>
        ) : error || !parsed ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 p-12 text-center">
            <AlertCircle className="h-8 w-8 text-gray-600" />
            <p className="text-sm text-gray-400">
              {error
                ? "Failed to load document details."
                : "No parsed data available for this document."}
            </p>
            <p className="text-xs text-gray-600">
              {doc.status === "pending" || doc.status === "processing"
                ? "Document is still being processed. Check back shortly."
                : "Upload a supported file type to extract structured data."}
            </p>
          </div>
        ) : (
          <>
            {/* Tabs */}
            <div className="flex gap-1 border-b border-gray-800 px-4 pt-2">
              {tabs.map((tab) => {
                const Icon = tab.icon;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => {
                      setActiveTab(tab.id);
                      setShowRaw(false);
                    }}
                    className={clsx(
                      "flex items-center gap-1.5 rounded-t px-3 py-2 text-xs font-medium transition-colors",
                      activeTab === tab.id && !showRaw
                        ? "border-b-2 border-indigo-500 text-indigo-300"
                        : "text-gray-500 hover:text-gray-300"
                    )}
                  >
                    <Icon className="h-3.5 w-3.5" />
                    {tab.label}
                  </button>
                );
              })}
              <button
                type="button"
                onClick={() => setShowRaw(!showRaw)}
                className={clsx(
                  "ml-auto flex items-center gap-1.5 rounded-t px-3 py-2 text-xs font-medium transition-colors",
                  showRaw
                    ? "border-b-2 border-indigo-500 text-indigo-300"
                    : "text-gray-500 hover:text-gray-300"
                )}
              >
                <FileCode className="h-3.5 w-3.5" />
                Raw JSON
              </button>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-auto p-5">
              {showRaw ? (
                <pre className="rounded-lg bg-gray-900 p-4 text-xs text-gray-300 overflow-x-auto leading-relaxed">
                  {JSON.stringify(detail, null, 2)}
                </pre>
              ) : activeTab === "summary" ? (
                <div className="space-y-4">
                  {parsed.title && (
                    <div>
                      <p className="text-xs font-medium text-gray-500 mb-1">Title</p>
                      <p className="text-sm text-gray-200">{parsed.title}</p>
                    </div>
                  )}
                  {parsed.summary && (
                    <div>
                      <p className="text-xs font-medium text-gray-500 mb-1">Summary</p>
                      <p className="text-sm text-gray-300 leading-relaxed whitespace-pre-line">
                        {parsed.summary}
                      </p>
                    </div>
                  )}
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                    <div className="rounded-lg bg-gray-800/50 p-3">
                      <p className="text-xs text-gray-500">Confidence</p>
                      <p className="mt-1 text-lg font-bold text-emerald-400">
                        {parsed.confidence_score != null
                          ? `${Math.round(parsed.confidence_score * 100)}%`
                          : "—"}
                      </p>
                    </div>
                    <div className="rounded-lg bg-gray-800/50 p-3">
                      <p className="text-xs text-gray-500">Endpoints</p>
                      <p className="mt-1 text-lg font-bold text-white">
                        {parsed.endpoints?.length ?? 0}
                      </p>
                    </div>
                    <div className="rounded-lg bg-gray-800/50 p-3">
                      <p className="text-xs text-gray-500">Fields</p>
                      <p className="mt-1 text-lg font-bold text-white">
                        {parsed.fields?.length ?? 0}
                      </p>
                    </div>
                    <div className="rounded-lg bg-gray-800/50 p-3">
                      <p className="text-xs text-gray-500">Auth Schemes</p>
                      <p className="mt-1 text-lg font-bold text-white">
                        {parsed.auth_requirements?.length ?? 0}
                      </p>
                    </div>
                  </div>
                  {parsed.services_identified && parsed.services_identified.length > 0 && (
                    <div>
                      <p className="text-xs font-medium text-gray-500 mb-2">Services Identified</p>
                      <div className="flex flex-wrap gap-2">
                        {parsed.services_identified.map((s) => (
                          <span
                            key={s}
                            className="rounded-full bg-indigo-500/10 px-3 py-1 text-xs text-indigo-300"
                          >
                            {s}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                  {parsed.sections?.base_urls && (
                    <div>
                      <p className="text-xs font-medium text-gray-500 mb-2">Base URLs</p>
                      <code className="block rounded bg-gray-800 px-3 py-1.5 text-xs text-indigo-300">
                        {parsed.sections.base_urls}
                      </code>
                    </div>
                  )}
                  {parsed.parse_errors && parsed.parse_errors.length > 0 && (
                    <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-3">
                      <p className="text-xs font-medium text-red-400 mb-1">Parse Errors</p>
                      {parsed.parse_errors.map((e) => (
                        <p key={e} className="text-xs text-red-300">
                          {e}
                        </p>
                      ))}
                    </div>
                  )}
                </div>
              ) : activeTab === "endpoints" ? (
                parsed.endpoints && parsed.endpoints.length > 0 ? (
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-gray-800">
                          <th className="pb-2 text-left font-medium text-gray-500">Method</th>
                          <th className="pb-2 text-left font-medium text-gray-500">Path</th>
                          <th className="pb-2 text-left font-medium text-gray-500">Description</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-800/60">
                        {parsed.endpoints.map((ep, i) => (
                          // biome-ignore lint/suspicious/noArrayIndexKey: endpoints lack stable id
                          <tr key={i} className="hover:bg-gray-800/30">
                            <td className="py-2 pr-3">
                              <span
                                className={clsx(
                                  "inline-block rounded px-1.5 py-0.5 text-[10px] font-bold uppercase",
                                  methodBadgeCls(ep.method)
                                )}
                              >
                                {ep.method}
                              </span>
                            </td>
                            <td className="py-2 pr-3 font-mono text-gray-300">{ep.path}</td>
                            <td className="py-2 text-gray-400">
                              {ep.description || ep.summary || "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="text-sm text-gray-500 text-center py-8">No endpoints extracted.</p>
                )
              ) : activeTab === "fields" ? (
                parsed.fields && parsed.fields.length > 0 ? (
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-gray-800">
                          <th className="pb-2 text-left font-medium text-gray-500">Name</th>
                          <th className="pb-2 text-left font-medium text-gray-500">Type</th>
                          <th className="pb-2 text-left font-medium text-gray-500">Required</th>
                          <th className="pb-2 text-left font-medium text-gray-500">Source</th>
                          <th className="pb-2 text-left font-medium text-gray-500">Description</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-800/60">
                        {parsed.fields.map((f, i) => (
                          // biome-ignore lint/suspicious/noArrayIndexKey: field list lacks stable id
                          <tr key={i} className="hover:bg-gray-800/30">
                            <td className="py-2 pr-3 font-mono text-gray-200">{f.name}</td>
                            <td className="py-2 pr-3 text-indigo-400">{f.data_type ?? "—"}</td>
                            <td className="py-2 pr-3">
                              {f.is_required ? (
                                <span className="text-emerald-400 font-medium">Yes</span>
                              ) : (
                                <span className="text-gray-600">No</span>
                              )}
                            </td>
                            <td className="py-2 pr-3 text-gray-500 text-[10px]">
                              {f.source_section || "—"}
                            </td>
                            <td className="py-2 text-gray-400">{f.description || "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="text-sm text-gray-500 text-center py-8">No fields extracted.</p>
                )
              ) : activeTab === "auth" ? (
                parsed.auth_requirements && parsed.auth_requirements.length > 0 ? (
                  <div className="space-y-3">
                    {parsed.auth_requirements.map((auth, i) => (
                      // biome-ignore lint/suspicious/noArrayIndexKey: auth list lacks stable id
                      <div key={i} className="rounded-lg border border-gray-800 bg-gray-900/40 p-4">
                        <div className="flex items-center gap-2 mb-2">
                          <Shield className="h-3.5 w-3.5 text-indigo-400" />
                          <span className="text-xs font-semibold text-indigo-300 uppercase tracking-wide">
                            {auth.auth_type}
                          </span>
                        </div>
                        {auth.details?.name && (
                          <p className="text-xs text-gray-400">
                            Name: <code className="text-gray-300">{auth.details.name}</code>
                          </p>
                        )}
                        {auth.details?.in && (
                          <p className="text-xs text-gray-400">
                            Location: <code className="text-gray-300">{auth.details.in}</code>
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-gray-500 text-center py-8">
                    No auth requirements extracted.
                  </p>
                )
              ) : null}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// biome-ignore lint/complexity/noExcessiveCognitiveComplexity: page component with upload, list, modals
export default function Documents() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [uploadQueue, setUploadQueue] = useState<string[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [selectedDoc, setSelectedDoc] = useState<Document | null>(null);
  const [confirmDeleteDoc, setConfirmDeleteDoc] = useState<Document | null>(null);
  const [search, setSearch] = useState("");

  const { data, error, isLoading } = useQuery({
    queryKey: ["documents"],
    queryFn: () => documentsApi.list(),
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => documentsApi.upload(file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (err: unknown) => {
      const message = err instanceof Error ? err.message : "Upload failed. Please try again.";
      setUploadError(message);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => documentsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      setConfirmDeleteDoc(null);
      toast("Document deleted.", "success");
    },
    onError: () => {
      toast("Failed to delete document.", "error");
      setConfirmDeleteDoc(null);
    },
  });

  const onDrop = useCallback(
    (acceptedFiles: File[]) => {
      setUploadError(null);
      for (const file of acceptedFiles) {
        setUploadQueue((q) => [...q, file.name]);
        uploadMutation.mutate(file, {
          onSettled: () => {
            setUploadQueue((q) => q.filter((n) => n !== file.name));
          },
        });
      }
    },
    [uploadMutation]
  );

  const onDropRejected = useCallback((rejectedFiles: FileRejection[]) => {
    const messages = rejectedFiles.flatMap((r) =>
      r.errors.map((e) =>
        e.code === "file-too-large" ? `${r.file.name} exceeds the 50 MB size limit` : e.message
      )
    );
    setUploadError(messages.join("; "));
  }, []);

  const isBackendDown = !!error;

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    onDropRejected,
    noClick: isBackendDown,
    noDrag: isBackendDown,
    disabled: isBackendDown,
    maxSize: 50 * 1024 * 1024,
    accept: {
      "application/pdf": [".pdf"],
      "application/json": [".json"],
      "application/xml": [".xml"],
      "text/csv": [".csv"],
      "application/x-yaml": [".yaml", ".yml"],
      "text/yaml": [".yaml", ".yml"],
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
    },
  });

  const allDocuments: Document[] = data?.data ?? [];
  const documents = search.trim()
    ? allDocuments.filter((d) => d.filename.toLowerCase().includes(search.trim().toLowerCase()))
    : allDocuments;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Documents</h1>
        <p className="mt-1 text-sm text-gray-400">Upload and manage integration documents</p>
      </div>

      {error && (
        <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-4 text-sm text-amber-400">
          Backend unavailable. Uploads disabled.
        </div>
      )}

      {uploadError && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-4 text-sm text-red-400">
          {uploadError}
        </div>
      )}

      {/* Drop zone */}
      <div
        {...getRootProps()}
        className={clsx(
          "card border-2 border-dashed p-10 text-center transition-all",
          isBackendDown
            ? "cursor-not-allowed opacity-50 border-gray-700"
            : isDragActive
              ? "cursor-pointer border-indigo-500 bg-indigo-500/5"
              : "group cursor-pointer border-gray-700 hover:border-gray-500"
        )}
      >
        <input {...getInputProps()} />
        <div className="flex flex-col items-center gap-3">
          <div
            className={clsx(
              "rounded-full p-3 transition-colors",
              isDragActive
                ? "bg-indigo-500/20 text-indigo-400"
                : "bg-gray-800 text-gray-400 group-hover:text-gray-300"
            )}
          >
            <Upload className="h-6 w-6" />
          </div>
          <div>
            <p className="font-medium text-gray-300">
              {isBackendDown
                ? "Upload unavailable — backend offline"
                : isDragActive
                  ? "Drop files here..."
                  : "Drag & drop files, or click to browse"}
            </p>
            <p className="mt-1 text-xs text-gray-500">
              PDF, DOCX, YAML, JSON, XML, CSV, XLSX &middot; max 50 MB
            </p>
          </div>
        </div>
      </div>

      {/* Upload queue */}
      {uploadQueue.length > 0 && (
        <div className="space-y-2">
          {uploadQueue.map((name) => (
            <div
              key={name}
              className="flex items-center gap-3 rounded-lg border border-indigo-500/20 bg-indigo-500/5 px-4 py-3"
            >
              <Loader2 className="h-4 w-4 animate-spin text-indigo-400" />
              <span className="text-sm text-indigo-300">{name}</span>
              <span className="text-xs text-indigo-400/60">Uploading...</span>
            </div>
          ))}
        </div>
      )}

      {/* Document list */}
      <div className="card overflow-hidden">
        <div className="border-b border-gray-800 px-6 py-4 flex items-center gap-3">
          <h2 className="font-semibold text-white flex-1">
            Recent Documents{" "}
            {!isLoading && (
              <span className="text-sm font-normal text-gray-500">({documents.length})</span>
            )}
          </h2>
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-500" />
            <input
              type="text"
              placeholder="Search by filename..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="rounded-lg border border-gray-700 bg-gray-900 pl-8 pr-3 py-1.5 text-sm text-gray-300 placeholder-gray-600 focus:border-indigo-500 focus:outline-none hover:border-gray-600 transition-colors w-52"
            />
          </div>
        </div>

        {isLoading ? (
          <div className="divide-y divide-gray-800/60">
            {Array.from({ length: 4 }).map((_, i) => (
              <div
                // biome-ignore lint/suspicious/noArrayIndexKey: skeleton rows have no stable id
                key={i}
                className="flex items-center gap-4 px-6 py-4"
              >
                <div className="h-8 w-8 animate-pulse rounded-lg bg-gray-800" />
                <div className="flex-1 space-y-2">
                  <div className="h-4 w-48 animate-pulse rounded bg-gray-800" />
                  <div className="h-3 w-32 animate-pulse rounded bg-gray-800" />
                </div>
                <div className="h-5 w-20 animate-pulse rounded-full bg-gray-800" />
              </div>
            ))}
          </div>
        ) : documents.length === 0 ? (
          <div className="flex flex-col items-center gap-3 px-6 py-16 text-center">
            <div className="rounded-full bg-gray-800 p-4">
              {search.trim() ? (
                <Search className="h-6 w-6 text-gray-500" />
              ) : (
                <Upload className="h-6 w-6 text-gray-500" />
              )}
            </div>
            <p className="font-medium text-gray-400">
              {search.trim() ? "No matching documents." : "No documents yet."}
            </p>
            <p className="text-sm text-gray-500">
              {search.trim()
                ? `No filenames match "${search}".`
                : "Upload your first document using the drop zone above."}
            </p>
          </div>
        ) : (
          <div className="divide-y divide-gray-800/60">
            {documents.map((doc) => {
              const st = statusConfig[doc.status] ?? {
                label: doc.status,
                icon: Clock,
                cls: "badge-gray",
              };
              const IconFile = fileIcon(doc.file_type);
              const StatusIcon = st.icon;

              return (
                <div
                  key={doc.id}
                  className="flex items-center gap-4 px-6 py-4 transition-colors hover:bg-gray-800/30"
                >
                  <button
                    type="button"
                    className="flex flex-1 items-center gap-4 text-left min-w-0"
                    onClick={() => setSelectedDoc(doc)}
                  >
                    <div className="rounded-lg bg-gray-800 p-2 shrink-0">
                      <IconFile className="h-4 w-4 text-gray-400" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium text-gray-200">{doc.filename}</p>
                      <p className="text-xs text-gray-500">
                        {doc.file_type.toUpperCase()} &middot; {formatDate(doc.created_at)}
                      </p>
                    </div>
                    <span className={st.cls}>
                      <StatusIcon
                        className={clsx(
                          "mr-1 h-3 w-3",
                          doc.status === "processing" && "animate-spin"
                        )}
                      />
                      {st.label}
                    </span>
                    <ChevronRight className="h-4 w-4 text-gray-600 shrink-0" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmDeleteDoc(doc)}
                    className="text-gray-600 hover:text-red-400 transition-colors shrink-0"
                    aria-label={`Delete ${doc.filename}`}
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Detail modal */}
      {selectedDoc && <DetailModal doc={selectedDoc} onClose={() => setSelectedDoc(null)} />}

      {/* Delete confirmation dialog */}
      {confirmDeleteDoc && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          {/* biome-ignore lint/a11y/useKeyWithClickEvents: backdrop dismiss, keyboard handled by Cancel button */}
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => setConfirmDeleteDoc(null)}
          />
          <div className="relative z-10 w-full max-w-sm rounded-xl border border-gray-800 bg-gray-950 p-6 shadow-2xl">
            <h3 className="font-semibold text-white mb-2">Delete document?</h3>
            <p className="text-sm text-gray-400 mb-5">
              <span className="text-gray-200">{confirmDeleteDoc.filename}</span> will be permanently
              deleted. This cannot be undone.
            </p>
            <div className="flex gap-3 justify-end">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setConfirmDeleteDoc(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-500 transition-colors disabled:opacity-50"
                disabled={deleteMutation.isPending}
                onClick={() => deleteMutation.mutate(confirmDeleteDoc.id)}
              >
                {deleteMutation.isPending ? "Deleting..." : "Delete"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
