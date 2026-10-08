"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button, Card, ErrorBox, Field, inputClass } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { homeFor, type Me } from "@/lib/session";

export default function LoginPage() {
  const router = useRouter();
  const client = useQueryClient();
  const [employeeId, setEmployeeId] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const me = await api<Me>("/auth/login", { json: { employee_id: employeeId.trim(), password } });
      client.setQueryData(["me"], me);
      router.replace(homeFor(me));
    } catch (err) {
      setError(err instanceof ApiError && err.status === 423
        ? new Error("Too many failed attempts. Wait 15 minutes, or ask the exam cell to reset your password.")
        : err);
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-1 items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <h1 className="text-xl font-semibold">Answer Script Evaluator</h1>
        <p className="mb-4 text-sm text-gray-600">Sign in with your employee ID.</p>
        <form onSubmit={submit} className="space-y-3">
          <Field label="Employee ID">
            <input className={inputClass} value={employeeId} autoComplete="username" required
              onChange={(e) => setEmployeeId(e.target.value)} autoFocus />
          </Field>
          <Field label="Password">
            <input className={inputClass} type="password" value={password} autoComplete="current-password"
              required onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <ErrorBox error={error} />
          <Button type="submit" className="w-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</Button>
        </form>
      </Card>
    </div>
  );
}
