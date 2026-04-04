import clsx from "clsx";
import { ChevronLeft, ChevronRight } from "lucide-react";

interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
}

export default function Pagination({ page, pageSize, total, onPageChange }: PaginationProps) {
  const start = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, total);
  const totalPages = Math.ceil(total / pageSize);
  const hasPrev = page > 1;
  const hasNext = page < totalPages;

  if (total === 0) return null;

  return (
    <div className="flex items-center justify-between px-1 py-3">
      <p className="text-sm text-gray-500">
        Showing{" "}
        <span className="font-medium text-gray-300">
          {start}–{end}
        </span>{" "}
        of <span className="font-medium text-gray-300">{total}</span>
      </p>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => onPageChange(page - 1)}
          disabled={!hasPrev}
          aria-label="Previous page"
          className={clsx(
            "flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors",
            hasPrev
              ? "border-gray-700 text-gray-300 hover:border-gray-600 hover:bg-gray-800/60 hover:text-white"
              : "cursor-not-allowed border-gray-800 text-gray-600"
          )}
        >
          <ChevronLeft className="h-4 w-4" />
          Prev
        </button>

        <span className="min-w-[60px] text-center text-sm text-gray-400">
          {page} / {totalPages}
        </span>

        <button
          type="button"
          onClick={() => onPageChange(page + 1)}
          disabled={!hasNext}
          aria-label="Next page"
          className={clsx(
            "flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors",
            hasNext
              ? "border-gray-700 text-gray-300 hover:border-gray-600 hover:bg-gray-800/60 hover:text-white"
              : "cursor-not-allowed border-gray-800 text-gray-600"
          )}
        >
          Next
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
