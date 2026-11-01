import { Outlet } from "react-router-dom";
import { TopBar } from "./TopBar";
import { SideNav } from "./SideNav";

// App layout: fixed top bar, left nav, scrollable main canvas (the drill-down ladder).
export function AppShell() {
  return (
    <div className="flex h-screen flex-col">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <SideNav />
        <main className="min-w-0 flex-1 overflow-auto bg-surface-0 p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
