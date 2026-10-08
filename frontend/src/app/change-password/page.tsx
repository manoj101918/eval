"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Button, Card, ErrorBox, Field, inputClass, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { homeFor, useMe } from "@/lib/session";

export default function ChangePasswordPage() {
  const me = useMe();
  const router = useRouter();
  const client = useQueryClient();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (me.isError) router.replace("/login");
  }, [me.isError, router]);
  if (!me.data) return <Spinner />;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (next !== repeat) {
      setError(new Error("The new passwords do not match."));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api("/auth/change-password", { json: { current_password: current, new_password: next } });
      const updated = { ...me.data!, must_change_password: false };
      client.setQueryData(["me"], updated);
      router.replace(homeFor(updated));
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-1 items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <h1 className="text-xl font-semibold">Choose a new password</h1>
        <p className="mb-4 text-sm text-gray-600">
          {me.data.must_change_password
            ? "You signed in with a one-time password. Choose your own to continue."
            : "Change your password."}
        </p>
        <form onSubmit={submit} className="space-y-3">
          <Field label="Current (one-time) password">
            <input className={inputClass} type="password" value={current} required autoComplete="current-password"
              onChange={(e) => setCurrent(e.target.value)} autoFocus />
          </Field>
          <Field label="New password" hint="At least 8 characters, not your employee ID.">
            <input className={inputClass} type="password" value={next} required minLength={8}
              autoComplete="new-password" onChange={(e) => setNext(e.target.value)} />
          </Field>
          <Field label="Repeat new password">
            <input className={inputClass} type="password" value={repeat} required minLength={8}
              autoComplete="new-password" onChange={(e) => setRepeat(e.target.value)} />
          </Field>
          <ErrorBox error={error} />
          <Button type="submit" className="w-full" disabled={busy}>{busy ? "Saving…" : "Save password"}</Button>
        </form>
      </Card>
    </div>
  );
}
