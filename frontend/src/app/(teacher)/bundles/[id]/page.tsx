"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { SubmitBundleButton } from "@/components/SubmitBundle";
import { Badge, Button, Card, ErrorBox, Spinner, StatusBadge } from "@/components/ui";
import { failureText, useBundleScripts, type ScriptItem } from "@/lib/teacher";

function needsAttention(s: ScriptItem): boolean {
  return s.status !== "approved";
}

export default function BundlePage() {
  const { id } = useParams<{ id: string }>();
  const bundle = useBundleScripts(Number(id));
  const [onlyOpen, setOnlyOpen] = useState(true);

  if (bundle.isPending) return <Spinner />;
  if (bundle.error) return <ErrorBox error={bundle.error} />;
  const b = bundle.data;
  const rows = onlyOpen ? b.scripts.filter(needsAttention) : b.scripts;

  return (
    <div className="space-y-4">
      <Link href="/bundles" className="text-sm text-blue-800 hover:underline">← My bundles</Link>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{b.exam.subject} · {b.exam.class_section} · bundle {b.code}</h1>
          <p className="text-sm text-gray-600">
            {b.exam.name} · {b.approved}/{b.total} approved
            {b.pending > 0 && ` · AI is still grading ${b.pending} script(s)…`}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge status={b.status} />
          <SubmitBundleButton bundleId={b.id} code={b.code} canSubmit={b.can_submit}
            submitted={b.status === "submitted"} />
        </div>
      </div>

      <div className="flex gap-2 text-sm">
        <Button variant={onlyOpen ? "primary" : "secondary"} onClick={() => setOnlyOpen(true)}>
          Needs attention ({b.scripts.filter(needsAttention).length})
        </Button>
        <Button variant={onlyOpen ? "secondary" : "primary"} onClick={() => setOnlyOpen(false)}>
          All ({b.scripts.length})
        </Button>
      </div>

      <Card className="overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-gray-600">
            <tr>
              <th className="px-4 py-2">#</th>
              <th className="px-4 py-2">Roll number</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2">AI total</th>
              <th className="px-4 py-2">Final total</th>
              <th className="px-4 py-2">To check</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={7} className="px-4 py-6 text-center text-gray-500">
                {b.scripts.length ? "Every script is approved." : "No scripts uploaded yet."}
              </td></tr>
            )}
            {rows.map((s) => (
              <tr key={s.id} className="border-t border-gray-100">
                <td className="px-4 py-2 text-gray-500">{b.scripts.indexOf(s) + 1}</td>
                <td className="px-4 py-2 font-mono">{s.roll_number ?? <span className="text-amber-700">missing</span>}</td>
                <td className="px-4 py-2">
                  <StatusBadge status={s.status} />
                  {s.status === "failed" && <p className="mt-1 text-xs text-red-700">{failureText(s.error)}</p>}
                </td>
                <td className="px-4 py-2">{s.ai_total ?? "—"}</td>
                <td className="px-4 py-2">{s.final_total ?? "—"}</td>
                <td className="px-4 py-2">{s.to_check > 0 ? <Badge tone="amber">{s.to_check}</Badge> : "—"}</td>
                <td className="px-4 py-2 text-right">
                  {(s.status === "graded" || s.status === "approved") && (
                    <Link href={`/scripts/${s.id}`} className="font-medium text-blue-800 hover:underline">
                      {s.status === "approved" ? "View" : "Review"}
                    </Link>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
