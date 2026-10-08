"use client";

import Link from "next/link";
import { SubmitBundleButton } from "@/components/SubmitBundle";
import { Card, ErrorBox, ProgressBar, Spinner, StatusBadge } from "@/components/ui";
import { useMyBundles } from "@/lib/teacher";

export default function MyBundlesPage() {
  const bundles = useMyBundles();
  if (bundles.isPending) return <Spinner />;
  if (bundles.error) return <ErrorBox error={bundles.error} />;

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">My bundles</h1>
      {bundles.data.length === 0 && (
        <Card><p className="text-sm text-gray-600">No bundles are assigned to you yet.</p></Card>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        {bundles.data.map((b) => (
          <Card key={b.id} className="space-y-3">
            <div className="flex items-start justify-between gap-2">
              <div>
                <Link href={`/bundles/${b.id}`} className="text-lg font-semibold text-blue-800 hover:underline">
                  {b.exam.subject} · {b.exam.class_section}
                </Link>
                <p className="text-sm text-gray-600">{b.exam.name} · bundle {b.code} · max {b.exam.max_marks} marks</p>
              </div>
              <StatusBadge status={b.status} />
            </div>
            <ProgressBar label="Graded by AI" value={b.total - b.pending} total={b.total} />
            <ProgressBar label="Approved by you" value={b.approved} total={b.total} />
            {b.failed > 0 && (
              <p className="text-sm text-red-700">{b.failed} script(s) could not be graded by the AI.</p>
            )}
            <div className="flex items-center justify-between">
              <Link href={`/bundles/${b.id}`} className="text-sm font-medium text-blue-800 hover:underline">
                {b.status === "submitted" ? "View scripts" : "Review scripts →"}
              </Link>
              <SubmitBundleButton bundleId={b.id} code={b.code} canSubmit={b.can_submit}
                submitted={b.status === "submitted"} />
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
