"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import StatusPill from "@/components/StatusPill";

export default function HitlPage() {
  const [tasks, setTasks] = useState<any[]>([]);
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const decidedBy = "admin_demo";

  async function load() {
    try {
      setTasks(await api.hitlInbox());
    } catch (e: any) {
      setError(e.message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function act(taskId: number, approved: boolean) {
    setBusy(taskId);
    setError(null);
    try {
      await api.resolveHitl(taskId, approved, decidedBy, notes[taskId] || "");
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <p className="label-eyebrow">HITL inbox</p>
        <p className="text-sm text-muted mt-1">
          Expected pauses across every graph on this MCP server — crew reassignment, passenger
          claims, and whatever else gets added. Resolving here resumes the exact run from this node.
        </p>
      </div>

      {error && <div className="card border-bad/40 text-bad text-sm">{error}</div>}

      {tasks.length === 0 && <div className="card text-sm text-muted">No pending approvals.</div>}

      {tasks.map((t) => (
        <div key={t.task_id} className="card flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <p className="font-mono text-sm">
              #{t.task_id} · <span className="text-muted">{t.graph_name}</span> · node{" "}
              <span className="text-cyan">{t.node_name}</span>
            </p>
            <StatusPill status={t.status} />
          </div>
          <p className="text-[11px] font-mono text-muted uppercase">{t.condition_type}</p>
          <p className="text-sm">{t.reason}</p>
          <input
            placeholder="Optional note"
            className="bg-raised border border-border rounded px-3 py-2 text-sm"
            value={notes[t.task_id] || ""}
            onChange={(e) => setNotes({ ...notes, [t.task_id]: e.target.value })}
          />
          <div className="flex gap-2 justify-end">
            <button className="btn-bad" disabled={busy === t.task_id} onClick={() => act(t.task_id, false)}>
              Reject
            </button>
            <button className="btn-good" disabled={busy === t.task_id} onClick={() => act(t.task_id, true)}>
              Approve
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
