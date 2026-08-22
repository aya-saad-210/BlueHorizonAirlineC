"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type ToolRow = { tool_name: string; registered: boolean };

export default function ToolsPage() {
  const [tools, setTools] = useState<ToolRow[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const performedBy = "admin_demo"; // replace with the logged-in admin's id once auth exists

  async function load() {
    setError(null);
    try {
      setTools(await api.toolsCatalog());
    } catch (e: any) {
      setError(e.message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function toggle(tool: ToolRow) {
    setBusy(tool.tool_name);
    setError(null);
    try {
      if (tool.registered) {
        await api.deregisterTool(tool.tool_name, performedBy);
      } else {
        await api.registerTool(tool.tool_name, performedBy);
      }
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
        <p className="label-eyebrow">Agents &amp; tools</p>
        <p className="text-sm text-muted mt-1">
          Toggling here calls the live MCP server directly — a change here is a change to what the
          connected agent can actually call, not a preview.
        </p>
      </div>

      {error && <div className="card border-bad/40 text-bad text-sm">{error}</div>}

      <div className="card p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-muted font-mono text-[11px] uppercase border-b border-border">
              <th className="px-4 py-3">Tool</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3 text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {tools.map((t) => (
              <tr key={t.tool_name} className="border-b border-border last:border-0">
                <td className="px-4 py-3 font-mono">{t.tool_name}</td>
                <td className="px-4 py-3">
                  <span className={t.registered ? "text-good" : "text-muted"}>
                    {t.registered ? "registered" : "not registered"}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">
                  <button
                    onClick={() => toggle(t)}
                    disabled={busy === t.tool_name}
                    className={t.registered ? "btn-bad" : "btn-good"}
                  >
                    {busy === t.tool_name ? "…" : t.registered ? "Remove" : "Add"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
