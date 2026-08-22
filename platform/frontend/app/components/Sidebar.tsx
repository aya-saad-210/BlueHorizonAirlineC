"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/admin", label: "Overview" },
  { href: "/admin/tools", label: "Agents & Tools" },
  { href: "/admin/rag", label: "RAG Documents" },
  { href: "/admin/hitl", label: "HITL Inbox" },
  { href: "/admin/tickets", label: "Tickets" },
  { href: "/admin/claims", label: "Claims" },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-56 flex-shrink-0 border-r border-border bg-surface min-h-screen px-4 py-6">
      <div className="mb-8">
        <p className="font-mono text-sm text-cyan tracking-wide">BLUE HORIZON</p>
        <p className="text-[11px] text-muted mt-0.5">Ops control surface</p>
      </div>
      <nav className="flex flex-col gap-1">
        {LINKS.map((link) => {
          const active = pathname === link.href;
          return (
            <Link
              key={link.href}
              href={link.href}
              className={`text-sm px-3 py-2 rounded transition-colors ${
                active ? "bg-raised text-ink border border-border" : "text-muted hover:text-ink"
              }`}
            >
              {link.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
