"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import StatusPill from "@/components/StatusPill";
import StateTimeline from "@/components/StateTimeline";

export default function ClaimsPage() {
  const [claims, setClaims] = useState<any[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [checkpoints, setCheckpoints] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setClaims(await api.claims());
    } catch (e: any) {
      setError(e.message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function expand(claimId: number) {
    if (expanded === claimId) {
      setExpanded(null);
      return;
    }
    setExpanded(claimId);
    try {
      setCheckpoints(await api.claimCheckpoints(claimId));
    } catch (e: any) {
      setError(e.message);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <p className="label-eyebrow">Claims — state graph #2</p>
        <p className="text-sm text-muted mt-1">
          Each row is one run of the claims &amp; appeal graph. Expand a claim to see its actual
          checkpoint history, read straight from Person A&apos;s shared checkpointer.
        </p>
      </div>

      {error && <div className="card border-bad/40 text-bad text-sm">{error}</div>}

      <div className="flex flex-col gap-2">
        {claims.map((c) => (
          <div key={c.claim_id} className="card">
            <button className="w-full text-left flex items-center justify-between" onClick={() => expand(c.claim_id)}>
              <div className="flex items-center gap-4">
                <span className="font-mono text-sm text-muted">#{c.claim_id}</span>
                <span className="text-sm">{c.passenger_name}</span>
                <span className="font-mono text-xs text-muted">{c.flight_number}</span>
                <span className="font-mono text-sm">
                  {c.amount} {c.currency}
                </span>
              </div>
              <StatusPill status={c.final_status} />
            </button>
            {expanded === c.claim_id && (
              <div className="mt-4 border-t border-border pt-3">
                <StateTimeline checkpoints={checkpoints} />
              </div>
            )}
          </div>
        ))}
        {claims.length === 0 && <div className="card text-sm text-muted">No claims submitted yet.</div>}
      </div>
    </div>
  );
}
