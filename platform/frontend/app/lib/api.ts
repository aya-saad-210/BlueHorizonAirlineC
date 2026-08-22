const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail || `Request to ${path} failed (${res.status})`);
  }
  return res.json();
}

export const api = {
  agents: () => request<Array<{ agent_name: string; live_tools: string[] }>>("/api/agents"),
  toolsCatalog: () => request<Array<{ tool_name: string; registered: boolean }>>("/api/tools/catalog"),
  registerTool: (tool_name: string, performed_by: string) =>
    request("/api/tools/register", { method: "POST", body: JSON.stringify({ tool_name, performed_by }) }),
  deregisterTool: (tool_name: string, performed_by: string) =>
    request("/api/tools/deregister", { method: "POST", body: JSON.stringify({ tool_name, performed_by }) }),

  ragDocuments: () => request<Array<any>>("/api/rag/documents"),
  addRagDocument: (filename: string, doc_type: string, added_by: string) =>
    request("/api/rag/documents", { method: "POST", body: JSON.stringify({ filename, doc_type, added_by }) }),

  // HITL: shared schema (Person A). Fields are run_id/node_name/reason/
  // condition_type/graph_name, not the old task_id/question/options shape.
  hitlInbox: (graph_name?: string) => request<Array<any>>(`/api/hitl${graph_name ? `?graph_name=${graph_name}` : ""}`),
  resolveHitl: (task_id: number, approved: boolean, decided_by: string, note = "") =>
    request(`/api/hitl/${task_id}/resolve`, {
      method: "POST",
      body: JSON.stringify({ approved, decided_by, note }),
    }),

  // Tickets: NOT available yet -- Person C's system. No client calls until
  // that API exists; the Tickets page shows a static placeholder instead.

  claims: () => request<Array<any>>("/api/claims"),
  claimCheckpoints: (claim_id: number) => request<Array<{ node_name: string; status: string; step_number: number }>>(
    `/api/claims/${claim_id}/checkpoints`
  ),
  submitClaim: (body: {
    passenger_email: string;
    flight_number: string;
    amount: number;
    currency: string;
    reason: string;
    submitted_by: string;
  }) => request("/api/claims", { method: "POST", body: JSON.stringify(body) }),
  submitAppeal: (claim_id: number, appeal_reason: string) =>
    request(`/api/claims/${claim_id}/appeal`, { method: "POST", body: JSON.stringify({ appeal_reason }) }),
};
