import { useState } from "react";
import { useTradeDocs, type TradeDocsResponse } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { Markdown } from "@/components/shared/Markdown";
import { cn } from "@/lib/cn";

// "How it's priced" — surfaces the pricing write-up (docs/pricing/*) directly on the trade
// page: tab 1 is the per-trade page, the next tabs are the instrument methodology + the SOFR
// curve where relevant, and the last is the shared Foundations. Progressive disclosure: the
// plain-English explanation is up top in each doc, the quantitative detail in boxes below.

function Panel({ docs }: { docs: TradeDocsResponse }) {
  const [active, setActive] = useState(0);
  const idx = Math.min(active, docs.sections.length - 1);
  const section = docs.sections[idx];

  return (
    <Card>
      <CardHeader
        title="How it's priced"
        subtitle="Plain-English first, quantitative detail in the boxes — what data it needs, what we use, and the method."
      />
      <div className="flex flex-wrap items-center gap-1 border-b border px-4">
        {docs.sections.map((s, i) => (
          <button
            key={s.id}
            type="button"
            onClick={() => setActive(i)}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-sm font-medium",
              i === idx
                ? "border-primary text-primary"
                : "border-transparent text-fg-secondary hover:text-fg",
            )}
          >
            {s.label}
          </button>
        ))}
      </div>
      <CardBody>
        <Markdown source={section.markdown} />
      </CardBody>
    </Card>
  );
}

export function PricingMethodology({ tradeId }: { tradeId: string }) {
  const query = useTradeDocs(tradeId);
  return (
    <QueryState query={query} empty={(d) => !d.available || d.sections.length === 0}>
      {(docs) => <Panel docs={docs} />}
    </QueryState>
  );
}
