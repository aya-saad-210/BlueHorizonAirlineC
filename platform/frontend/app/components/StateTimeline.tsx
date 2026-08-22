type Checkpoint = { node_name: string; status: string; step_number: number };

const DOT_TONE: Record<string, string> = {
  running: "bg-cyan",
  waiting_hitl: "bg-amber",
  waiting_external: "bg-amber",
  failed: "bg-bad",
  completed: "bg-good",
};

/**
 * Renders a claim's actual graph_checkpoints history (via Person A's
 * checkpointer.MySQLCheckpointer) as connected dots -- the proof surface
 * for "checkpointing as a first-class citizen", not decoration.
 */
export default function StateTimeline({ checkpoints }: { checkpoints: Checkpoint[] }) {
  if (checkpoints.length === 0) {
    return <p className="text-muted text-sm font-mono">No checkpoints yet.</p>;
  }

  const last = checkpoints[checkpoints.length - 1];

  return (
    <div className="flex items-center overflow-x-auto py-2">
      {checkpoints.map((cp, i) => {
        const isLast = i === checkpoints.length - 1;
        const tone = DOT_TONE[cp.status] || "bg-muted";
        return (
          <div key={`${cp.node_name}-${cp.step_number}`} className="flex items-center flex-shrink-0">
            <div className="flex flex-col items-center gap-1.5 min-w-[92px]">
              <div
                className={`w-3 h-3 rounded-full ${tone} ${isLast && cp.status !== "completed" ? "pulse-dot shadow-glow" : ""}`}
              />
              <span className="font-mono text-[10px] text-muted text-center leading-tight">{cp.node_name}</span>
            </div>
            {i < checkpoints.length - 1 && <div className="h-px w-8 bg-border flex-shrink-0" />}
          </div>
        );
      })}
      <span className="ml-3 text-[11px] text-muted font-mono flex-shrink-0">step {last.step_number}</span>
    </div>
  );
}
