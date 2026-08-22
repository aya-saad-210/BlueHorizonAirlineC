import Sidebar from "@/components/Sidebar";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen bg-bg">
      <Sidebar />
      <div className="flex-1">
        <header className="border-b border-border px-8 py-4 flex items-center justify-between">
          <div>
            <p className="label-eyebrow">Admin console</p>
            <h1 className="text-lg font-semibold">irops_assistant</h1>
          </div>
          <div className="flex items-center gap-2 text-[11px] font-mono text-good">
            <span className="w-2 h-2 rounded-full bg-good pulse-dot" />
            LIVE
          </div>
        </header>
        <main className="px-8 py-6">{children}</main>
      </div>
    </div>
  );
}
