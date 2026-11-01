import { type ReactNode, useMemo, useState } from "react";
import {
  applyTableModel,
  selectAll,
  toCsv,
  type Accessors,
  type SortDir,
  type TableState,
} from "./tableModel";
import { cn } from "@/lib/cn";

// Shared data grid: client-side sort / per-column filter / pagination / CSV export over a
// row set, built on the pure tableModel. Columns declare a display `render` and an optional
// `get` (the sort/filter/export value; defaults to row[key]).

export interface DataColumn<T> {
  key: string;
  header: string;
  get?: (row: T) => unknown; // sort/filter/export value
  render?: (row: T) => ReactNode; // display cell (defaults to the get value)
  csv?: (row: T) => string | number; // export value (defaults to the get value)
  align?: "left" | "right";
  sortable?: boolean; // default true
  filterable?: boolean; // default = table `filterable`
  headerClassName?: string;
  cellClassName?: string;
}

interface DataTableProps<T> {
  columns: DataColumn<T>[];
  rows: T[];
  getRowKey: (row: T) => string;
  initialSort?: { key: string; dir: SortDir };
  pageSize?: number;
  filterable?: boolean;
  exportName?: string; // enables the CSV export button
  onRowClick?: (row: T) => void;
  rowClassName?: (row: T) => string | undefined; // row-level styling (e.g. highlight failures)
  emptyText?: string;
  dense?: boolean;
}

function defaultGet<T>(col: DataColumn<T>): (row: T) => unknown {
  return col.get ?? ((row: T) => (row as Record<string, unknown>)[col.key]);
}

export function DataTable<T>({
  columns,
  rows,
  getRowKey,
  initialSort,
  pageSize = 25,
  filterable = false,
  exportName,
  onRowClick,
  rowClassName,
  emptyText = "No rows.",
  dense = false,
}: DataTableProps<T>) {
  const [state, setState] = useState<TableState>({
    sortKey: initialSort?.key ?? null,
    sortDir: initialSort?.dir ?? "asc",
    filters: {},
    page: 1,
    pageSize,
  });

  const accessors: Accessors<T> = useMemo(
    () => Object.fromEntries(columns.map((c) => [c.key, defaultGet(c)])),
    [columns],
  );

  const page = useMemo(() => applyTableModel(rows, accessors, state), [rows, accessors, state]);

  const toggleSort = (key: string) =>
    setState((s) => ({
      ...s,
      page: 1,
      sortKey: key,
      sortDir: s.sortKey === key && s.sortDir === "asc" ? "desc" : "asc",
    }));

  const setFilter = (key: string, val: string) =>
    setState((s) => ({ ...s, page: 1, filters: { ...s.filters, [key]: val } }));

  const setPage = (p: number) => setState((s) => ({ ...s, page: p }));

  const onExport = () => {
    const all = selectAll(rows, accessors, state);
    const headers = columns.map((c) => c.header);
    const csvRows = all.map((row) =>
      columns.map((c) => {
        if (c.csv) return c.csv(row);
        const v = defaultGet(c)(row);
        return v == null ? "" : (v as string | number);
      }),
    );
    const blob = new Blob([toCsv(headers, csvRows)], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${exportName}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const pad = dense ? "px-3 py-1" : "px-4 py-2";
  const from = page.total === 0 ? 0 : (page.page - 1) * state.pageSize + 1;
  const to = Math.min(page.page * state.pageSize, page.total);
  const anyFilterable = filterable || columns.some((c) => c.filterable);

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
            <tr>
              {columns.map((c) => {
                const sortable = c.sortable !== false;
                const active = state.sortKey === c.key;
                return (
                  <th
                    key={c.key}
                    className={cn(pad, "font-medium", c.align === "right" && "text-right", c.headerClassName)}
                  >
                    <button
                      type="button"
                      disabled={!sortable}
                      onClick={() => sortable && toggleSort(c.key)}
                      className={cn(
                        "inline-flex items-center gap-1",
                        sortable && "cursor-pointer hover:text-fg",
                        c.align === "right" && "flex-row-reverse",
                      )}
                    >
                      {c.header}
                      {sortable && (
                        <span className={cn("text-nano", active ? "text-fg" : "text-fg-muted")}>
                          {active ? (state.sortDir === "asc" ? "▲" : "▼") : "▲▼"}
                        </span>
                      )}
                    </button>
                  </th>
                );
              })}
            </tr>
            {anyFilterable && (
              <tr>
                {columns.map((c) => {
                  const canFilter = c.filterable ?? filterable;
                  return (
                    <th key={c.key} className={cn("px-2 pb-1.5 pt-0")}>
                      {canFilter && (
                        <input
                          value={state.filters[c.key] ?? ""}
                          onChange={(e) => setFilter(c.key, e.target.value)}
                          placeholder="filter…"
                          className="w-full rounded border border px-1.5 py-0.5 text-micro font-normal"
                        />
                      )}
                    </th>
                  );
                })}
              </tr>
            )}
          </thead>
          <tbody className="divide-y divide-border-subtle">
            {page.rows.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className={cn(pad, "text-fg-muted")}>
                  {emptyText}
                </td>
              </tr>
            ) : (
              page.rows.map((row) => (
                <tr
                  key={getRowKey(row)}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  className={cn("hover:bg-surface-2", onRowClick && "cursor-pointer", rowClassName?.(row))}
                >
                  {columns.map((c) => {
                    const content = c.render ? c.render(row) : (defaultGet(c)(row) as ReactNode);
                    return (
                      <td key={c.key} className={cn(pad, c.align === "right" && "text-right tnum", c.cellClassName)}>
                        {content}
                      </td>
                    );
                  })}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* footer: range + export + pager */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-subtle px-4 py-2 text-xs text-fg-secondary">
        <div className="flex items-center gap-3">
          <span>
            {from}–{to} of {page.total}
          </span>
          {exportName && page.total > 0 && (
            <button type="button" onClick={onExport} className="rounded border border-strong px-2 py-0.5 font-medium text-fg-secondary hover:bg-surface-2">
              ↓ CSV
            </button>
          )}
        </div>
        {page.nPages > 1 && (
          <div className="flex items-center gap-1">
            <button
              type="button"
              disabled={page.page <= 1}
              onClick={() => setPage(page.page - 1)}
              className={cn("rounded px-2 py-0.5", page.page <= 1 ? "text-fg-muted" : "hover:bg-surface-0")}
            >
              ‹ prev
            </button>
            <span className="tabular-nums">
              {page.page} / {page.nPages}
            </span>
            <button
              type="button"
              disabled={page.page >= page.nPages}
              onClick={() => setPage(page.page + 1)}
              className={cn("rounded px-2 py-0.5", page.page >= page.nPages ? "text-fg-muted" : "hover:bg-surface-0")}
            >
              next ›
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
