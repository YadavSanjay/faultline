import { PageHeader } from "@/components/shared/PageHeader";
import { Card, CardBody } from "@/components/ui/Card";

// Stub for subsystem areas not yet built. Names the build-plan phase so the nav shape is
// honest and discoverable.
export function Placeholder({ title, phase, blurb }: {
  title: string;
  phase: string;
  blurb: string;
}) {
  return (
    <div>
      <PageHeader title={title} subtitle={blurb} />
      <Card>
        <CardBody className="py-10 text-center">
          <div className="text-sm text-fg-secondary">
            This area arrives in <span className="font-semibold text-fg">{phase}</span> of the
            build plan.
          </div>
          <div className="mt-1 text-xs text-fg-muted">See ui/docs/BUILD_PLAN.md</div>
        </CardBody>
      </Card>
    </div>
  );
}

export function NotFound() {
  return (
    <div>
      <PageHeader title="Not found" subtitle="No such page." />
    </div>
  );
}
