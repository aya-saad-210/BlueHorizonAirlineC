import { api } from "@/lib/api";

export default async function OverviewPage() {
  const [agents, hitl, claims] = await Promise.all([
    api.agents().catch(() => []),
    api.hitlInbox().catch(() => []),
    api.claims().catch(() => []),
  ]);

  const liveToolCount = agents[0]?.live_tools.length ?? 0;

  const stats = [
    { label: "Live tools", value: liveToolCount },
    { label: "Pending HITL (all graphs)", value: hitl.length },
    { label: "Claims tracked", value: claims.length },
  ];

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-3 gap-4">
        {stats.map((s) => (
          <div key={s.label} className="card">
            <p className="label-eyebrow">{s.label}</p>
            <p className="text-3xl font-mono mt-2">{s.value}</p>
          </div>
        ))}
      </div>

      <div className="card">
        <p className="label-eyebrow mb-3">What needs attention right now</p>
        {hitl.length === 0 ? (
          <p className="text-sm text-muted">Nothing pending across any graph.</p>
        ) : (
          <ul className="flex flex-col gap-2 text-sm">
            {hitl.slice(0, 8).map((h: any) => (
              <li key={h.task_id} className="text-amber">
                HITL #{h.task_id} · {h.graph_name} · {h.reason.slice(0, 90)}
              </li>
            ))}
          </ul>
        )}
        <p className="text-[11px] text-muted mt-4 font-mono">
          Ticket counts will appear here once Person C&apos;s ticket system is wired in.
        </p>
      </div>
    </div>
  );
}
