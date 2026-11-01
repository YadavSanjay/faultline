// Pure, framework-agnostic table logic — sort / filter / paginate / CSV — shared by the
// DataTable component and unit-tested in isolation (no React, no DOM).

export type SortDir = "asc" | "desc";

export type Accessor<T> = (row: T) => unknown;
export type Accessors<T> = Record<string, Accessor<T>>;

function isBlank(v: unknown): boolean {
  return v == null || v === "";
}

/** Directional comparison of two non-blank values: numbers numerically, else natural strings. */
function baseCompare(a: unknown, b: unknown): number {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}

/** Compare two cell values (ascending): numbers numerically, else natural strings; blanks last. */
export function compareValues(a: unknown, b: unknown): number {
  const an = isBlank(a);
  const bn = isBlank(b);
  if (an && bn) return 0;
  if (an) return 1;
  if (bn) return -1;
  return baseCompare(a, b);
}

export function sortRows<T>(rows: T[], get: Accessor<T>, dir: SortDir): T[] {
  const m = dir === "desc" ? -1 : 1;
  // blanks always sort to the end, regardless of direction; direction applies to real values
  return [...rows].sort((x, y) => {
    const a = get(x);
    const b = get(y);
    const an = isBlank(a);
    const bn = isBlank(b);
    if (an && bn) return 0;
    if (an) return 1;
    if (bn) return -1;
    return m * baseCompare(a, b);
  });
}

/** Case-insensitive substring match on the string form of each filtered column's value. */
export function filterRows<T>(rows: T[], accessors: Accessors<T>, filters: Record<string, string>): T[] {
  const active = Object.entries(filters).filter(([, v]) => v != null && String(v).trim() !== "");
  if (active.length === 0) return rows;
  return rows.filter((row) =>
    active.every(([key, needle]) => {
      const acc = accessors[key];
      if (!acc) return true;
      const val = acc(row);
      return String(val ?? "").toLowerCase().includes(String(needle).trim().toLowerCase());
    }),
  );
}

export interface Page<T> {
  rows: T[];
  total: number;
  nPages: number;
  page: number;
}

export function paginate<T>(rows: T[], page: number, pageSize: number): Page<T> {
  const total = rows.length;
  const size = Math.max(1, pageSize);
  const nPages = Math.max(1, Math.ceil(total / size));
  const p = Math.min(Math.max(1, page), nPages);
  const start = (p - 1) * size;
  return { rows: rows.slice(start, start + size), total, nPages, page: p };
}

export interface TableState {
  sortKey: string | null;
  sortDir: SortDir;
  filters: Record<string, string>;
  page: number;
  pageSize: number;
}

/** filter → sort → paginate, in that order. Returns the visible page plus totals. */
export function applyTableModel<T>(rows: T[], accessors: Accessors<T>, state: TableState): Page<T> {
  const filtered = filterRows(rows, accessors, state.filters);
  const sorted =
    state.sortKey && accessors[state.sortKey]
      ? sortRows(filtered, accessors[state.sortKey], state.sortDir)
      : filtered;
  return paginate(sorted, state.page, state.pageSize);
}

/** Rows that survive filter + sort but WITHOUT pagination — used for "export everything". */
export function selectAll<T>(rows: T[], accessors: Accessors<T>, state: TableState): T[] {
  const filtered = filterRows(rows, accessors, state.filters);
  return state.sortKey && accessors[state.sortKey]
    ? sortRows(filtered, accessors[state.sortKey], state.sortDir)
    : filtered;
}

export function csvEscape(v: string): string {
  return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
}

export function toCsv(headers: string[], rows: (string | number)[][]): string {
  const lines = [headers.map((h) => csvEscape(String(h))).join(",")];
  for (const r of rows) lines.push(r.map((c) => csvEscape(String(c ?? ""))).join(","));
  return lines.join("\n");
}
