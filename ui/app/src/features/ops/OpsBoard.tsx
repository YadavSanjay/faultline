import { ControlRoom } from "@/features/governance/ControlRoom";
import { RunHistory } from "@/features/system/RunHistory";

// The Technical-lens default landing (decided): a thin "is everything OK today?" board —
// the Control Room posture over the cross-module Run History. Both run on data that
// already exists (CROSSCUTTING_UI §4 build-plan early win). Rendered under SystemLayout.

export function OpsBoard() {
  return (
    <div className="space-y-6">
      <ControlRoom embedded />
      <RunHistory embedded />
    </div>
  );
}
