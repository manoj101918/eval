"use client";

import { useState } from "react";
import { Badge, Button, Card, Dialog, ErrorBox, Field, inputClass, Spinner } from "@/components/ui";
import { useAdminMutation, useUsers, type NewUserOut, type TempPassword } from "@/lib/admin";
import { api } from "@/lib/api";

function OneTimePassword({ who, password, onClose }: { who: string; password: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <Dialog open title={`One-time password for ${who}`} onClose={onClose}>
      <p className="text-sm text-gray-700">Give this to the teacher. It is shown only once; they must choose
        their own password at first login.</p>
      <p className="my-3 rounded bg-gray-100 p-3 text-center font-mono text-lg" data-testid="one-time-password">
        {password}
      </p>
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={async () => {
          await navigator.clipboard?.writeText(password);
          setCopied(true);
        }}>{copied ? "Copied" : "Copy"}</Button>
        <Button onClick={onClose}>Done</Button>
      </div>
    </Dialog>
  );
}

export default function TeachersPage() {
  const users = useUsers();
  const [employeeId, setEmployeeId] = useState("");
  const [name, setName] = useState("");
  const [shown, setShown] = useState<{ who: string; password: string } | null>(null);

  const create = useAdminMutation((body: { employee_id: string; name: string }) =>
    api<NewUserOut>("/admin/users", { json: body }));
  const reset = useAdminMutation((id: number) =>
    api<TempPassword>(`/admin/users/${id}/reset-password`, { method: "POST" }));
  const setActive = useAdminMutation(({ id, active }: { id: number; active: boolean }) =>
    api(`/admin/users/${id}/active?active=${active}`, { method: "POST" }));

  if (users.isPending) return <Spinner />;
  if (users.error) return <ErrorBox error={users.error} />;

  function add(event: React.FormEvent) {
    event.preventDefault();
    create.mutate({ employee_id: employeeId.trim(), name: name.trim() }, {
      onSuccess: (out) => {
        setShown({ who: out.user.employee_id, password: out.temporary_password });
        setEmployeeId("");
        setName("");
      },
    });
  }

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Teachers</h1>
      <Card>
        <form onSubmit={add} className="flex flex-wrap items-end gap-3">
          <Field label="Employee ID"><input className={inputClass} value={employeeId} required
            onChange={(e) => setEmployeeId(e.target.value)} /></Field>
          <Field label="Name"><input className={inputClass} value={name} required
            onChange={(e) => setName(e.target.value)} /></Field>
          <Button type="submit" disabled={create.isPending}>Add teacher</Button>
        </form>
        <div className="mt-2"><ErrorBox error={create.error ?? reset.error ?? setActive.error} /></div>
      </Card>

      <Card className="overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-gray-600">
            <tr><th className="px-4 py-2">Employee ID</th><th className="px-4 py-2">Name</th>
              <th className="px-4 py-2">Role</th><th className="px-4 py-2">Status</th><th className="px-4 py-2" /></tr>
          </thead>
          <tbody>
            {users.data.map((u) => (
              <tr key={u.id} className="border-t border-gray-100">
                <td className="px-4 py-2 font-mono">{u.employee_id}</td>
                <td className="px-4 py-2">{u.name}</td>
                <td className="px-4 py-2">{u.role}</td>
                <td className="px-4 py-2 space-x-1">
                  {u.active ? <Badge tone="green">Active</Badge> : <Badge>Deactivated</Badge>}
                  {u.must_change_password && <Badge tone="amber">Has one-time password</Badge>}
                </td>
                <td className="space-x-2 px-4 py-2 text-right">
                  <Button variant="secondary" disabled={reset.isPending} onClick={() => reset.mutate(u.id, {
                    onSuccess: (out) => setShown({ who: u.employee_id, password: out.temporary_password }),
                  })}>Reset password</Button>
                  {u.role === "teacher" && (
                    <Button variant={u.active ? "danger" : "secondary"}
                      onClick={() => setActive.mutate({ id: u.id, active: !u.active })}>
                      {u.active ? "Deactivate" : "Activate"}
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {shown && <OneTimePassword who={shown.who} password={shown.password} onClose={() => setShown(null)} />}
    </div>
  );
}
