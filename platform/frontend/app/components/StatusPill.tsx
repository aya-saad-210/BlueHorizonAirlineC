const TONE: Record<string, string> = {
  running: "text-cyan border-cyan/40 bg-cyan/10",
  waiting_hitl: "text-amber border-amber/40 bg-amber/10",
  waiting_external: "text-amber border-amber/40 bg-amber/10",
  failed: "text-bad border-bad/40 bg-bad/10",
  completed: "text-good border-good/40 bg-good/10",
  pending: "text-amber border-amber/40 bg-amber/10",
  approved: "text-good border-good/40 bg-good/10",
  rejected: "text-bad border-bad/40 bg-bad/10",
  resolved_appeal_approved: "text-good border-good/40 bg-good/10",
  resolved_appeal_rejected: "text-bad border-bad/40 bg-bad/10",
};

export default function StatusPill({ status }: { status: string | null }) {
  const label = status || "running";
  const tone = TONE[label] || "text-muted border-border bg-raised";
  return (
    <span className={`font-mono text-[11px] uppercase tracking-wide px-2 py-0.5 rounded border ${tone}`}>
      {label.replace(/_/g, " ")}
    </span>
  );
}
