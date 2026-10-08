"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import { submitSummary, useSubmitBundle, type BundleScripts, type SubmitResult } from "@/lib/teacher";
import { Button, Dialog, ErrorBox, Spinner } from "./ui";

function fmt(n: number) {
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

/** Stays mounted after the bundle is submitted (only the button hides), so the result stays on screen. */
export function SubmitBundleButton({ bundleId, code, canSubmit, submitted }: {
  bundleId: number; code: string; canSubmit: boolean; submitted: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<SubmitResult | null>(null);
  const submit = useSubmitBundle(bundleId);
  const detail = useQuery({
    queryKey: ["my", "bundle", bundleId],
    queryFn: () => api<BundleScripts>(`/my/bundles/${bundleId}`),
    enabled: open,
  });
  const summary = detail.data ? submitSummary(detail.data.scripts) : null;

  return (
    <>
      {!submitted && (
        <Button disabled={!canSubmit} onClick={() => { setResult(null); setOpen(true); }}
          title={canSubmit ? undefined : "Approve every script first"}>
          Submit bundle
        </Button>
      )}
      <Dialog open={open} title={`Submit bundle ${code}?`} onClose={() => setOpen(false)}>
        {result ? (
          <div className="space-y-3 text-sm">
            <p className="font-medium text-green-800">Bundle {code} is submitted.</p>
            <p>
              {result.exported
                ? "All bundles of this exam are in — the marks have been written to the Excel sheet."
                : result.exam_completed
                  ? "All bundles are in, but the exam cell has to resolve an issue before the marks go to Excel."
                  : "The marks go to Excel once every bundle of this exam is submitted."}
            </p>
            <Button onClick={() => setOpen(false)}>Done</Button>
          </div>
        ) : !summary ? (
          <Spinner />
        ) : (
          <div className="space-y-3 text-sm">
            <ul className="space-y-1">
              <li>{summary.approved} of {summary.scripts} scripts approved</li>
              <li>Total marks: {fmt(summary.finalTotal)} (AI proposed {fmt(summary.aiTotal)})</li>
              <li>You changed the AI&apos;s marks on {summary.changedScripts} script(s)</li>
            </ul>
            <p className="text-gray-600">After submitting you cannot change these marks. The exam cell can reopen
              the bundle if something must be corrected.</p>
            <ErrorBox error={submit.error} />
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
              <Button disabled={submit.isPending}
                onClick={() => submit.mutate(undefined, { onSuccess: setResult })}>
                {submit.isPending ? "Submitting…" : "Submit"}
              </Button>
            </div>
          </div>
        )}
      </Dialog>
    </>
  );
}
