import { Sidebar } from "@/components/nav/sidebar";
import { Topbar } from "@/components/nav/topbar";
import { SetupGate } from "@/components/setup-gate";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex">
      {/* First-run gate. Renders nothing; checks /admin/setup/status
          on mount and redirects to /dashboard/setup if API keys
          haven't been configured. See components/setup-gate.tsx. */}
      <SetupGate />
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <Topbar />
        <main className="flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}
