"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PageViewer } from "@/components/review/PageViewer";
import { QuestionCard, type QuestionEdit } from "@/components/review/QuestionCard";
import { Badge, Button, Card, ErrorBox, inputClass, Spinner, StatusBadge } from "@/components/ui";
import { fmtMarks } from "@/lib/marks";
import {
  isToCheck, reviewOrder, ROLL_PATTERN, useApprove, useEditQuestion, useEditRoll, useReopen, useScript,
} from "@/lib/review";

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
}

export default function ReviewScriptPage() {
  const { id } = useParams<{ id: string }>();
  return <ScriptReview key={id} scriptId={Number(id)} />;
}

function RollNumber({ initial, editable, source, saving, onSave }: {
  initial: string; editable: boolean; source: string | null; saving: boolean;
  onSave: (roll: string) => void;
}) {
  const [roll, setRoll] = useState(initial);
  const valid = ROLL_PATTERN.test(roll.trim());
  return (
    <>
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-sm">
          <span className="block text-xs font-medium text-gray-600">Roll number</span>
          <input className={`${inputClass} w-44 font-mono uppercase`} value={roll}
            disabled={!editable} onChange={(e) => setRoll(e.target.value)} />
        </label>
        {editable && roll.trim().toUpperCase() !== initial && (
          <Button variant="secondary" disabled={!valid || saving} onClick={() => onSave(roll.trim())}>
            Save roll number
          </Button>
        )}
        <span className="text-xs text-gray-500">
          {source === "teacher" ? "entered by you"
            : source ? `read by ${source.toUpperCase()}` : "not found on the cover"}
        </span>
      </div>
      {roll && !valid && <p className="text-xs text-red-700">Letters, digits, / and - only.</p>}
    </>
  );
}

function ScriptReview({ scriptId }: { scriptId: number }) {
  const router = useRouter();
  const script = useScript(scriptId);
  const editQuestion = useEditQuestion(scriptId);
  const editRoll = useEditRoll(scriptId);
  const approve = useApprove(scriptId);
  const reopen = useReopen(scriptId);

  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(0);
  const [active, setActive] = useState(0);
  const cards = useRef<(HTMLDivElement | null)[]>([]);

  const data = script.data;
  const ordered = useMemo(() => (data ? reviewOrder(data.questions) : []), [data]);

  const goNext = useCallback(() => {
    if (!data) return;
    router.push(data.next_id ? `/scripts/${data.next_id}` : `/bundles/${data.bundle_id}`);
  }, [data, router]);

  const focusCard = useCallback((index: number) => {
    const clamped = Math.max(0, Math.min(ordered.length - 1, index));
    setActive(clamped);
    cards.current[clamped]?.focus();
    cards.current[clamped]?.scrollIntoView?.({ block: "nearest" });
  }, [ordered.length]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (isTyping(event.target) || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key === "j") focusCard(active + 1);
      else if (event.key === "k") focusCard(active - 1);
      else if (event.key === "n") goNext();
      else if (event.key === "Enter" && data?.editable) {
        const q = ordered[active];
        if (q && isToCheck(q)) editQuestion.mutate({ qid: q.id, edit: { confirmed: true } });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, data?.editable, editQuestion, focusCard, goNext, ordered]);

  if (script.isPending) return <Spinner />;
  if (script.error || !data) return <ErrorBox error={script.error} />;

  const toCheck = data.questions.filter(isToCheck).length;
  const mutationError = editQuestion.error ?? editRoll.error ?? approve.error ?? reopen.error;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link href={`/bundles/${data.bundle_id}`} className="text-sm text-blue-800 hover:underline">
            ← Bundle
          </Link>
          <h1 className="text-xl font-semibold">
            {data.exam.subject} · {data.exam.class_section} · script {data.roll_number ?? "(no roll number)"}
          </h1>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <StatusBadge status={data.status} />
          <span>AI {fmtMarks(data.ai_total)} → <strong>{fmtMarks(data.final_total)}</strong> / {fmtMarks(data.max_marks)}</span>
          {toCheck > 0 && <Badge tone="amber">{toCheck} to check</Badge>}
        </div>
      </div>

      {!data.editable && (
        <Card className="flex flex-wrap items-center justify-between gap-2 bg-gray-50 text-sm">
          <span>
            {data.bundle_submitted
              ? "This bundle is submitted, so the marks are locked. The exam cell can reopen it."
              : data.status === "approved"
                ? "You approved this script. Reopen it to make changes."
                : "This script is not ready for review."}
          </span>
          {data.status === "approved" && !data.bundle_submitted && (
            <Button variant="secondary" disabled={reopen.isPending} onClick={() => reopen.mutate(undefined)}>
              Reopen script
            </Button>
          )}
        </Card>
      )}
      <ErrorBox error={mutationError} />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <div className="h-[80vh] lg:sticky lg:top-2">
          <PageViewer scriptId={data.id} pageCount={data.page_count} page={page} onPage={setPage}
            zoom={zoom} onZoom={setZoom} />
        </div>

        <div className="space-y-3">
          <Card className="space-y-2">
            <RollNumber key={data.roll_number ?? ""} initial={data.roll_number ?? ""}
              editable={data.editable} source={data.roll_number_source} saving={editRoll.isPending}
              onSave={(roll) => editRoll.mutate(roll)} />
            {data.failed_pages.length > 0 && (
              <p className="text-sm text-red-700">
                Pages {data.failed_pages.join(", ")} could not be read by the AI: check them by eye.
              </p>
            )}
          </Card>

          {ordered.map((q, i) => (
            <QuestionCard key={`${q.id}:${q.final_marks}:${q.teacher_comment}`} q={q} editable={data.editable} active={i === active}
              ref={(el) => { cards.current[i] = el; }}
              saving={editQuestion.isPending}
              onFocus={() => setActive(i)}
              onShowPage={(p) => setPage(p)}
              onSave={(edit: QuestionEdit) => editQuestion.mutate({ qid: q.id, edit })} />
          ))}

          {data.leftovers.length > 0 && (
            <details className="rounded-lg border border-gray-200 bg-white p-4 text-sm shadow-sm">
              <summary className="cursor-pointer font-medium">
                Text not matched to any question ({data.leftovers.length})
              </summary>
              <p className="mt-2 text-gray-600">An answer may be hidden here if the question number was misread.</p>
              <ul className="mt-2 space-y-2">
                {data.leftovers.map((l, i) => (
                  <li key={i} className="rounded bg-gray-50 p-2">
                    <span className="text-xs text-gray-500">
                      {l.label ? `labelled "${l.label}"` : "no label"} · page {l.pages.join(", ")}
                    </span>
                    <p className="whitespace-pre-wrap">{l.text}</p>
                  </li>
                ))}
              </ul>
            </details>
          )}

          {data.editable && (
            <Card className="space-y-2">
              {data.problems.length > 0 ? (
                <ul className="list-disc pl-5 text-sm text-amber-800">
                  {data.problems.map((p) => <li key={p}>{p}</li>)}
                </ul>
              ) : <p className="text-sm text-green-800">Ready to approve.</p>}
              <div className="flex justify-between">
                <span className="text-xs text-gray-500">Keys: j/k question · Enter looks right · n next script</span>
                <Button disabled={data.problems.length > 0 || approve.isPending}
                  onClick={() => approve.mutate(undefined, { onSuccess: goNext })}>
                  Approve &amp; next script
                </Button>
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
