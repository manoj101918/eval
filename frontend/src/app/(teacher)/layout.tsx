"use client";

import { AuthGate } from "@/components/AuthGate";

export default function TeacherLayout({ children }: { children: React.ReactNode }) {
  return <AuthGate role="teacher">{children}</AuthGate>;
}
