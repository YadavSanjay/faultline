import { describe, it, expect } from "vitest";
import {
  compareValues,
  sortRows,
  filterRows,
  paginate,
  applyTableModel,
  selectAll,
  csvEscape,
  toCsv,
  type Accessors,
  type TableState,
} from "./tableModel";

interface Row { id: string; name: string; pv: number | null; ac: string }

const ROWS: Row[] = [
  { id: "T1", name: "NVDA stock", pv: 300, ac: "equity" },
  { id: "T2", name: "OIS swap", pv: -50, ac: "rates" },
  { id: "T3", name: "META bond", pv: 1200, ac: "bonds" },
  { id: "T4", name: "AMZN stock", pv: null, ac: "equity" },
];

const ACC: Accessors<Row> = {
  id: (r) => r.id,
  name: (r) => r.name,
  pv: (r) => r.pv,
  ac: (r) => r.ac,
};

describe("compareValues", () => {
  it("orders numbers numerically", () => {
    expect(compareValues(2, 10)).toBeLessThan(0);
  });
  it("orders strings naturally (numeric-aware)", () => {
    expect(compareValues("T2", "T10")).toBeLessThan(0);
  });
  it("sends null/blank to the end either way", () => {
    expect(compareValues(null, 5)).toBeGreaterThan(0);
    expect(compareValues(5, null)).toBeLessThan(0);
  });
});

describe("sortRows", () => {
  it("sorts ascending by number with nulls last", () => {
    const out = sortRows(ROWS, ACC.pv, "asc").map((r) => r.id);
    expect(out).toEqual(["T2", "T1", "T3", "T4"]); // -50, 300, 1200, null-last
  });
  it("sorts descending with nulls still last", () => {
    const out = sortRows(ROWS, ACC.pv, "desc").map((r) => r.id);
    expect(out).toEqual(["T3", "T1", "T2", "T4"]);
  });
  it("does not mutate the input", () => {
    const before = ROWS.map((r) => r.id);
    sortRows(ROWS, ACC.pv, "asc");
    expect(ROWS.map((r) => r.id)).toEqual(before);
  });
});

describe("filterRows", () => {
  it("filters case-insensitively by substring", () => {
    expect(filterRows(ROWS, ACC, { name: "stock" }).map((r) => r.id)).toEqual(["T1", "T4"]);
  });
  it("ANDs multiple column filters", () => {
    expect(filterRows(ROWS, ACC, { name: "stock", ac: "equity" }).length).toBe(2);
  });
  it("ignores blank filters", () => {
    expect(filterRows(ROWS, ACC, { name: "  " }).length).toBe(4);
  });
  it("matches numeric values by their string form", () => {
    expect(filterRows(ROWS, ACC, { pv: "1200" }).map((r) => r.id)).toEqual(["T3"]);
  });
});

describe("paginate", () => {
  it("slices a page and reports totals", () => {
    const p = paginate(ROWS, 2, 2);
    expect(p.rows.map((r) => r.id)).toEqual(["T3", "T4"]);
    expect(p.total).toBe(4);
    expect(p.nPages).toBe(2);
  });
  it("clamps an out-of-range page to the last page", () => {
    expect(paginate(ROWS, 99, 2).page).toBe(2);
  });
});

describe("applyTableModel", () => {
  const state: TableState = { sortKey: "pv", sortDir: "desc", filters: { ac: "equity" }, page: 1, pageSize: 10 };
  it("filters then sorts then paginates", () => {
    const p = applyTableModel(ROWS, ACC, state);
    expect(p.total).toBe(2); // only equity
    expect(p.rows.map((r) => r.id)).toEqual(["T1", "T4"]); // 300 desc, then null
  });
  it("selectAll returns filtered+sorted without pagination", () => {
    const all = selectAll(ROWS, ACC, { ...state, filters: {}, pageSize: 2 });
    expect(all.length).toBe(4);
  });
});

describe("toCsv", () => {
  it("escapes commas, quotes and newlines", () => {
    expect(csvEscape("a,b")).toBe('"a,b"');
    expect(csvEscape('he said "hi"')).toBe('"he said ""hi"""');
    expect(csvEscape("plain")).toBe("plain");
  });
  it("builds a header + rows CSV", () => {
    const csv = toCsv(["id", "pv"], [["T1", 300], ["T2", -50]]);
    expect(csv).toBe("id,pv\nT1,300\nT2,-50");
  });
});
