"use client";

import { AuthGate } from "@/components/AuthGate";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return <AuthGate role="admin">{children}</AuthGate>;
}
