import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Blue Horizon Ops Platform",
  description: "Agent, tool, and claims-graph control surface for Blue Horizon IROPS.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans">{children}</body>
    </html>
  );
}
