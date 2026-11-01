import { useEffect, useState } from "react";
import { useFeedRows, type SortDir } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { cn } from "@/lib/cn";

// MD-B6 Data table: the raw feed rows with per-column filtering, sorting and
// server-side pagination — works for any CSV (all filtering/paging is in DuckDB).

function useDebounced<T>(value: T, ms = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

function fmtCell(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString() : String(v);
  return String(v);
}

const PAGE_SIZES = [25, 50, 100, 200];

export function FeedDataTable({ feedId }: { feedId: string }) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [sortCol, setSortCol] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const debFilters = useDebounced(filters, 300);
  const query = useFeedRows(feedId, page, pageSize, debFilters, sortCol, sortDir);
  const data = query.data;

  const cols = data?.columns ?? [];
  const total = data?.total ?? 0;
  const nPages = data?.n_pages ?? 1;
  const anyFilter = Object.values(filters).some((v) => v.trim() !== "");
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);

  function onFilter(col: string, val: string) {
    setFilters((f) => ({ ...f, [col]: val }));
    setPage(1);
  }
  function toggleSort(col: string) {
    if (sortCol === col) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortCol(col);
      setSortDir("asc");
    }
    setPage(1);
  }
  function clearFilters() {
    setFilters({});
    setPage(1);
  }

  return (
    <Card>
      <CardHeader
        title="Data table"
        subtitle="the raw feed — filter any column, sort, and page through all rows"
        right={
          <span className="text-xs text-fg-secondary">
            <span className="tnum font-medium text-fg">{total.toLocaleString()}</span>{" "}
            {anyFilter ? "matching rows" : "rows"}
            {query.isFetching && <span className="ml-2 text-fg-muted">updating…</span>}
          </span>
        }
      />
      <CardBody className="space-y-3">
        {anyFilter && (
          <button type="button" onClick={clearFilters} className="text-xs text-primary hover:underline">
            Clear filters
          </button>
        )}

        <div className="overflow-auto rounded border border" style={{ maxHeight: "60vh" }}>
          <table className="w-full text-xs">
            <thead className="sticky top-0 z-10 bg-surface-2">
              <tr className="text-left text-fg-secondary">
                {cols.map((c) => {
                  const active = sortCol === c;
                  return (
                    <th
                      key={c}
                      onClick={() => toggleSort(c)}
                      title="Click to sort"
                      className="cursor-pointer whitespace-nowrap border-b border px-3 py-2 font-semibold hover:bg-surface-0"
                    >
                      <span className="inline-flex items-center gap-1">
                        {c}
                        <span className={cn("text-nano", active ? "text-primary" : "text-fg-muted")}>
                          {active ? (sortDir === "asc" ? "▲" : "▼") : "↕"}
                        </span>
                      </span>
                    </th>
                  );
                })}
              </tr>
              <tr className="bg-surface-1">
                {cols.map((c) => (
                  <th key={c} className="border-b border px-2 py-1">
                    <input
                      value={filters[c] ?? ""}
                      onChange={(e) => onFilter(c, e.target.value)}
                      placeholder="filter…"
                      className="w-full min-w-[80px] rounded border border bg-surface-1 px-1.5 py-0.5 text-micro font-normal text-fg placeholder:text-fg-muted focus:border-primary focus:outline-none"
                    />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {(data?.rows ?? []).map((row, i) => (
                <tr key={i} className="hover:bg-surface-2">
                  {cols.map((c) => {
                    const s = fmtCell(row[c]);
                    return (
                      <td
                        key={c}
                        title={s}
                        className="max-w-[240px] truncate whitespace-nowrap px-3 py-1.5 font-mono text-fg-secondary tnum"
                      >
                        {s}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>

          {query.isError && (
            <div className="px-3 py-6 text-center text-xs text-danger-fg">Could not load rows.</div>
          )}
          {!query.isError && data && data.rows.length === 0 && (
            <div className="px-3 py-8 text-center text-xs text-fg-muted">
              No rows match {anyFilter ? "these filters" : "this feed"}.
            </div>
          )}
          {!data && query.isLoading && (
            <div className="px-3 py-8 text-center text-xs text-fg-muted">Loading…</div>
          )}
        </div>

        {/* pagination footer */}
        <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-fg-secondary">
          <div className="tnum">
            {first.toLocaleString()}–{last.toLocaleString()} of {total.toLocaleString()}
            {anyFilter && " (filtered)"}
          </div>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-1.5">
              <span>rows/page</span>
              <select
                value={pageSize}
                onChange={(e) => {
                  setPageSize(Number(e.target.value));
                  setPage(1);
                }}
                className="rounded border border bg-surface-1 px-1.5 py-0.5 text-fg"
              >
                {PAGE_SIZES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </label>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="rounded border border px-2 py-0.5 disabled:opacity-40 enabled:hover:bg-surface-0"
              >
                ‹ Prev
              </button>
              <span className="tnum px-1">
                Page {page.toLocaleString()} of {nPages.toLocaleString()}
              </span>
              <button
                type="button"
                onClick={() => setPage((p) => Math.min(nPages, p + 1))}
                disabled={page >= nPages}
                className="rounded border border px-2 py-0.5 disabled:opacity-40 enabled:hover:bg-surface-0"
              >
                Next ›
              </button>
            </div>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}
