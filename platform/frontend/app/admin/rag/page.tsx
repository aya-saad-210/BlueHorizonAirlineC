"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function RagPage() {
  const [docs, setDocs] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pendingNote, setPendingNote] = useState<string | null>(null);

  async function load() {
    try {
      setDocs(await api.ragDocuments());
    } catch (e: any) {
      setError(e.message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function tryAdd() {
    try {
      await api.addRagDocument("example.pdf", "compensation_policy", "admin_demo");
    } catch (e: any) {
      setPendingNote(e.message);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <p className="label-eyebrow">RAG documents</p>
        <p className="text-sm text-muted mt-1">
          Listing is live from the registry table. Add/remove is intentionally disabled until the
          vector store wiring lands — see the note below instead of a fake success message.
        </p>
      </div>

      {error && <div className="card border-bad/40 text-bad text-sm">{error}</div>}

      <div className="card p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-muted font-mono text-[11px] uppercase border-b border-border">
              <th className="px-4 py-3">Filename</th>
              <th className="px-4 py-3">Type</th>
              <th className="px-4 py-3">Chunks</th>
              <th className="px-4 py-3">Added</th>
            </tr>
          </thead>
          <tbody>
            {docs.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-muted">
                  No documents in the registry yet.
                </td>
              </tr>
            )}
            {docs.map((d) => (
              <tr key={d.doc_id} className="border-b border-border last:border-0">
                <td className="px-4 py-3 font-mono">{d.filename}</td>
                <td className="px-4 py-3">{d.doc_type}</td>
                <td className="px-4 py-3">{d.chunk_count}</td>
                <td className="px-4 py-3 text-muted">{new Date(d.added_at).toLocaleDateString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card border-amber/30">
        <button onClick={tryAdd} className="btn-ghost">
          Add a document
        </button>
        {pendingNote && <p className="text-amber text-sm mt-3 font-mono">{pendingNote}</p>}
      </div>
    </div>
  );
}
