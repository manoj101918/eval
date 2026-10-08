"use client";

import { forwardRef, useState } from "react";
import { fmtMarks, marksProblem, parseMarks, stepMarks } from "@/lib/marks";
import type { QuestionOut } from "@/lib/teacher";
import { Badge, Button, inputClass } from "../ui";

export type QuestionEdit = { final_marks?: number; teacher_comment?: string; confirmed?: boolean };

type Props = {
  q: QuestionOut;
  editable: boolean;
  active: boolean;
  saving: boolean;
  onSave: (edit: QuestionEdit) => void;
  onShowPage: (page: number) => void;
  onFocus: () => void;
};

const CONFIDENCE_TONE = { high: "green", medium: "amber", low: "red" } as const;

// The parent keys each card by the saved marks and comment, so local edits reset when the
// server's values change.
export const QuestionCard = forwardRef<HTMLDivElement, Props>(function QuestionCard(
  { q, editable, active, saving, onSave, onShowPage, onFocus }, ref,
) {
  const [marksText, setMarksText] = useState(q.final_marks === null ? "" : String(q.final_marks));
  const [comment, setComment] = useState(q.teacher_comment);

  const problem = marksText === "" && q.final_marks === null ? null : marksProblem(marksText, q.max_marks);
  const toCheck = q.needs_review && !q.confirmed && q.counted;

  function saveMarks(text: string) {
    const value = parseMarks(text);
    if (value === null || marksProblem(text, q.max_marks) || value === q.final_marks) return;
    onSave({ final_marks: value });
  }

  function step(delta: number) {
    const next = stepMarks(parseMarks(marksText), delta, q.max_marks);
    setMarksText(String(next));
    onSave({ final_marks: next });
  }

  return (
    <div ref={ref} tabIndex={-1} onFocus={onFocus} data-question={q.question}
      aria-label={`Question ${q.question}`}
      className={`rounded-lg border bg-white p-4 shadow-sm outline-none ${
        toCheck ? "border-amber-400" : "border-gray-200"} ${active ? "ring-2 ring-blue-500" : ""} ${
        q.counted ? "" : "opacity-60"}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-base font-semibold">Q{q.question}</h3>
          <span className="text-sm text-gray-500">max {fmtMarks(q.max_marks)}</span>
          {q.qtype === "mcq" && <Badge>MCQ</Badge>}
          {!q.counted && <Badge>OR alternative not counted</Badge>}
          {toCheck && <Badge tone="amber">Check this</Badge>}
          {q.confirmed && <Badge tone="green">Checked</Badge>}
          {q.changed && <Badge tone="blue">Changed from AI ({fmtMarks(q.ai_marks)})</Badge>}
        </div>
        <div className="flex flex-wrap gap-1">
          {q.pages.map((p) => (
            <button key={p} type="button" onClick={() => onShowPage(p)}
              className="rounded border border-gray-300 px-1.5 text-xs text-gray-700 hover:bg-gray-100">
              p.{p}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-2 text-sm text-gray-700">
        <p>
          <span className="font-medium">AI: {fmtMarks(q.ai_marks)}/{fmtMarks(q.max_marks)}</span>
          {q.ai_confidence && (
            <> · <Badge tone={CONFIDENCE_TONE[q.ai_confidence as keyof typeof CONFIDENCE_TONE] ?? "gray"}>
              {q.ai_confidence} confidence</Badge></>
          )}
          {q.ai_status === "failed" && <> · <Badge tone="red">AI could not grade: enter the marks</Badge></>}
          {q.ai_status === "not_attempted" && <> · <Badge>No answer found</Badge></>}
        </p>
        {q.ai_reason && <p className="mt-1">{q.ai_reason}</p>}
        {q.points_met.length > 0 && <p className="mt-1 text-green-800">✓ {q.points_met.join("; ")}</p>}
        {q.points_missing.length > 0 && <p className="mt-1 text-red-800">✗ {q.points_missing.join("; ")}</p>}
        {q.review_notes.length > 0 && (
          <ul className="mt-1 list-disc pl-5 text-amber-800">
            {q.review_notes.map((n) => <li key={n}>{n}</li>)}
          </ul>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-xs font-medium text-gray-600" htmlFor={`marks-${q.id}`}>Marks</label>
          <div className="mt-1 flex items-center gap-1">
            <Button variant="secondary" disabled={!editable || saving} aria-label="Half a mark less"
              onClick={() => step(-0.5)}>−</Button>
            <input id={`marks-${q.id}`} className={`${inputClass} w-20 text-center`} inputMode="decimal"
              value={marksText} disabled={!editable} aria-invalid={problem ? true : undefined}
              onChange={(e) => setMarksText(e.target.value)} onBlur={(e) => saveMarks(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") saveMarks(marksText); }} />
            <Button variant="secondary" disabled={!editable || saving} aria-label="Half a mark more"
              onClick={() => step(0.5)}>+</Button>
            <span className="text-sm text-gray-500">/ {fmtMarks(q.max_marks)}</span>
          </div>
          {problem && <p className="mt-1 text-xs text-red-700">{problem}</p>}
        </div>
        {toCheck && (
          <Button disabled={!editable || saving} onClick={() => onSave({ confirmed: true })}>Looks right</Button>
        )}
      </div>

      <div className="mt-3">
        <label className="block text-xs font-medium text-gray-600" htmlFor={`comment-${q.id}`}>Comment (optional)</label>
        <textarea id={`comment-${q.id}`} className={`${inputClass} mt-1`} rows={1} value={comment}
          disabled={!editable} onChange={(e) => setComment(e.target.value)}
          onBlur={() => { if (comment !== q.teacher_comment) onSave({ teacher_comment: comment }); }} />
      </div>
    </div>
  );
});
