"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { ApiError } from "@/lib/api";
import { homeFor, useLogout, useMe } from "@/lib/session";
import { Button, Spinner } from "./ui";

/** Renders children only for a logged-in user of the given role; otherwise redirects. */
export function AuthGate({ role, children }: { role: "admin" | "teacher"; children: React.ReactNode }) {
  const me = useMe();
  const router = useRouter();
  const logout = useLogout();

  const target = me.data ? (me.data.must_change_password || me.data.role !== role ? homeFor(me.data) : null)
    : me.error instanceof ApiError && me.error.status === 401 ? "/login" : null;

  useEffect(() => {
    if (target) router.replace(target);
  }, [target, router]);

  if (me.isPending || target) return <Spinner />;
  if (!me.data) return <Spinner label="Could not reach the server. Retrying…" />;

  const home = role === "admin" ? "/admin" : "/bundles";
  return (
    <div className="flex min-h-full flex-col">
      <header className="border-b border-gray-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-2">
          <nav className="flex items-center gap-4 text-sm">
            <Link href={home} className="font-semibold text-blue-800">Answer Script Evaluator</Link>
            {role === "admin" && (
              <>
                <Link href="/admin" className="text-gray-700 hover:underline">Exams</Link>
                <Link href="/admin/teachers" className="text-gray-700 hover:underline">Teachers</Link>
              </>
            )}
          </nav>
          <div className="flex items-center gap-3 text-sm text-gray-700">
            <span>{me.data.name} <span className="text-gray-400">({me.data.employee_id})</span></span>
            <Button variant="ghost" onClick={logout}>Log out</Button>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6">{children}</main>
    </div>
  );
}
