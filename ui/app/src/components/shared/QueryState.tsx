import type { ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { WarningBanner } from "./WarningBanner";

// Uniform loading / error / empty handling for a React Query result.
export function QueryState<T>({ query, empty, children }: {
  query: UseQueryResult<T>;
  empty?: (data: T) => boolean;
  children: (data: T) => ReactNode;
}) {
  if (query.isLoading) {
    return (
      <div className="animate-pulse rounded-lg border border bg-surface-1 p-6 text-sm text-fg-muted">
        Loading…
      </div>
    );
  }
  if (query.isError || query.data === undefined) {
    return (
      <WarningBanner tone="error" title="Couldn't load data">
        {(query.error as Error)?.message ?? "Unknown error."} — is the BFF running on :8088?
      </WarningBanner>
    );
  }
  if (empty?.(query.data)) {
    return (
      <div className="rounded-lg border border-dashed border-strong bg-surface-1 p-6 text-sm text-fg-secondary">
        No data yet.
      </div>
    );
  }
  return <>{children(query.data)}</>;
}
