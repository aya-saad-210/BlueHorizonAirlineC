export default function TicketsPage() {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <p className="label-eyebrow">Tickets</p>
        <p className="text-sm text-muted mt-1">
          Ticket system for failure &amp; recovery is Person C&apos;s piece and isn&apos;t wired up
          yet. This page intentionally shows nothing fake — no mock rows, no static counts — until
          that API exists.
        </p>
      </div>
      <div className="card border-amber/30 text-sm text-amber">
        Waiting on Person C&apos;s tickets table + resolve endpoint. Every failure in this team&apos;s
        state graphs already routes to a real hand-off point (see graph_engine.py&apos;s
        <code className="font-mono mx-1">_open_ticket</code> stub) — this page will list those rows
        the moment that table and endpoint land.
      </div>
    </div>
  );
}
