"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Badge, Button, Card, Dialog, ErrorBox, Field, inputClass, Spinner, StatusBadge } from "@/components/ui";
import {
  activeTeachers, uploadScripts, useAdminMutation, useBundleDetail, useExam, useUsers,
  type BundleSummary, type ExamDetail, type UploadResult, type UserOut,
} from "@/lib/admin";
import { api } from "@/lib/api";
import { failureText } from "@/lib/teacher";

function UploadBox({ bundleId }: { bundleId: number }) {
  const [over, setOver] = useState(false);
  const [result, setResult] = useState<UploadResult | null>(null);
  const upload = useAdminMutation((files: File[]) => uploadScripts(bundleId, files));

  function send(list: FileList | null) {
    const files = Array.from(list ?? []);
    if (files.length) upload.mutate(files, { onSuccess: setResult });
  }

  return (
    <div className="space-y-2">
      <label
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); send(e.dataTransfer.files); }}
        className={`block cursor-pointer rounded-md border-2 border-dashed p-4 text-center text-sm ${
          over ? "border-blue-600 bg-blue-50" : "border-gray-300 text-gray-600"}`}>
        {upload.isPending ? "Uploading…" : "Drop student PDFs here (one PDF per student), or click to choose"}
        <input type="file" accept="application/pdf" multiple className="sr-only" aria-label={`Upload PDFs for bundle ${bundleId}`}
          onChange={(e) => { send(e.target.files); e.target.value = ""; }} />
      </label>
      <ErrorBox error={upload.error} />
      {result && (
        <div className="text-sm">
          <p className="text-green-800">{result.accepted.length} uploaded; the AI is grading them.</p>
          {result.rejected.map((r) => (
            <p key={r.filename} className="text-red-700">{r.filename}: {r.reason}</p>
          ))}
        </div>
      )}
    </div>
  );
}

function BundleScripts({ bundleId }: { bundleId: number }) {
  const detail = useBundleDetail(bundleId, true);
  if (detail.isPending) return <Spinner />;
  if (detail.error) return <ErrorBox error={detail.error} />;
  if (detail.data.scripts.length === 0) return <p className="text-sm text-gray-500">No scripts yet.</p>;
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-gray-600">
        <tr><th className="py-1">File</th><th>Roll number</th><th>Status</th></tr>
      </thead>
      <tbody>
        {detail.data.scripts.map((s) => (
          <tr key={s.id} className="border-t border-gray-100">
            <td className="py-1">{s.filename}</td>
            <td className="font-mono">{s.roll_number ?? "—"}</td>
            <td><StatusBadge status={s.status} />
              {s.status === "failed" && <span className="ml-2 text-xs text-red-700">{failureText(s.error)}</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function BundleRow({ bundle, teachers }: { bundle: BundleSummary; teachers: UserOut[] }) {
  const [open, setOpen] = useState(false);
  const [reopenOpen, setReopenOpen] = useState(false);
  const [reason, setReason] = useState("");
  const assign = useAdminMutation((employeeId: string) =>
    api(`/admin/bundles/${bundle.id}/teacher`, { method: "PUT", json: { teacher_employee_id: employeeId } }));
  const retry = useAdminMutation(() => api(`/admin/bundles/${bundle.id}/retry-failed`, { method: "POST" }));
  const reopen = useAdminMutation((why: string) =>
    api(`/admin/bundles/${bundle.id}/reopen`, { json: { reason: why } }));
  const submitted = bundle.status === "submitted";
  const c = bundle.counts;

  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h3 className="text-lg font-semibold">Bundle {bundle.code}</h3>
          <StatusBadge status={bundle.status} />
        </div>
        <label className="flex items-center gap-2 text-sm">
          Teacher
          <select className={`${inputClass} w-56`} disabled={submitted || assign.isPending}
            aria-label={`Teacher for bundle ${bundle.code}`}
            value={bundle.teacher?.employee_id ?? ""} onChange={(e) => assign.mutate(e.target.value)}>
            <option value="" disabled>Choose a teacher…</option>
            {teachers.map((t) => <option key={t.id} value={t.employee_id}>{t.name} ({t.employee_id})</option>)}
          </select>
        </label>
      </div>
      <p className="flex flex-wrap gap-2 text-sm">
        <span>{bundle.total} scripts:</span>
        {c.queued + c.processing > 0 && <Badge tone="blue">{c.queued + c.processing} AI grading</Badge>}
        {c.graded > 0 && <Badge tone="amber">{c.graded} to review</Badge>}
        {c.approved > 0 && <Badge tone="green">{c.approved} approved</Badge>}
        {c.failed > 0 && <Badge tone="red">{c.failed} failed</Badge>}
      </p>
      <ErrorBox error={assign.error ?? retry.error ?? reopen.error} />
      {!submitted && <UploadBox bundleId={bundle.id} />}
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" onClick={() => setOpen(!open)}>{open ? "Hide scripts" : "Show scripts"}</Button>
        {c.failed > 0 && !submitted && (
          <Button variant="secondary" disabled={retry.isPending} onClick={() => retry.mutate(undefined)}>
            Retry {c.failed} failed
          </Button>
        )}
        {submitted && <Button variant="secondary" onClick={() => setReopenOpen(true)}>Reopen bundle</Button>}
      </div>
      {open && <BundleScripts bundleId={bundle.id} />}
      <Dialog open={reopenOpen} title={`Reopen bundle ${bundle.code}?`} onClose={() => setReopenOpen(false)}>
        <p className="mb-2 text-sm text-gray-700">The teacher can change marks again. If the marks were already
          exported, the exam must be exported again after resubmission.</p>
        <Field label="Reason (recorded)">
          <input className={inputClass} value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <div className="mt-3 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setReopenOpen(false)}>Cancel</Button>
          <Button disabled={reason.trim().length < 3 || reopen.isPending}
            onClick={() => reopen.mutate(reason.trim(), { onSuccess: () => setReopenOpen(false) })}>
            Reopen
          </Button>
        </div>
      </Dialog>
    </Card>
  );
}

function NewBundle({ examId, teachers }: { examId: number; teachers: UserOut[] }) {
  const [code, setCode] = useState("");
  const [teacher, setTeacher] = useState("");
  const create = useAdminMutation(() => api(`/admin/exams/${examId}/bundles`, {
    json: { code: code.trim(), teacher_employee_id: teacher || null },
  }));
  return (
    <Card>
      <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => {
        e.preventDefault();
        create.mutate(undefined, { onSuccess: () => { setCode(""); setTeacher(""); } });
      }}>
        <Field label="New bundle code"><input className={inputClass} value={code} required
          onChange={(e) => setCode(e.target.value)} placeholder="B1" /></Field>
        <Field label="Teacher">
          <select className={inputClass} value={teacher} onChange={(e) => setTeacher(e.target.value)}
            aria-label="Teacher for the new bundle">
            <option value="">Assign later</option>
            {teachers.map((t) => <option key={t.id} value={t.employee_id}>{t.name} ({t.employee_id})</option>)}
          </select>
        </Field>
        <Button type="submit" disabled={create.isPending}>Add bundle</Button>
      </form>
      <div className="mt-2"><ErrorBox error={create.error} /></div>
    </Card>
  );
}

function ExportPanel({ exam }: { exam: ExamDetail }) {
  const exportMarks = useAdminMutation(() => api<ExamDetail>(`/admin/exams/${exam.id}/export`, { method: "POST" }));
  return (
    <Card className="space-y-2">
      <h2 className="text-lg font-semibold">Mark sheet</h2>
      {exam.exported_at ? (
        <p className="text-sm text-green-800">Exported {new Date(exam.exported_at).toLocaleString()}.</p>
      ) : (
        <p className="text-sm text-gray-600">Marks are written to Excel automatically when the last bundle is
          submitted. Only approved marks are ever exported.</p>
      )}
      {exam.export_error && <ErrorBox error={new Error(exam.export_error)} />}
      <ErrorBox error={exportMarks.error} />
      <div className="flex gap-2">
        <Button variant="secondary" disabled={exportMarks.isPending} onClick={() => exportMarks.mutate(undefined)}>
          {exam.exported_at ? "Export again" : "Export marks"}
        </Button>
        {exam.exported_at && (
          <a href={`/api/admin/exams/${exam.id}/export/file`}
            className="rounded-md bg-blue-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-800">
            Download mark sheet
          </a>
        )}
      </div>
    </Card>
  );
}

export default function ExamPage() {
  const { id } = useParams<{ id: string }>();
  const exam = useExam(Number(id));
  const users = useUsers();
  if (exam.isPending) return <Spinner />;
  if (exam.error) return <ErrorBox error={exam.error} />;
  const e = exam.data;
  const teachers = activeTeachers(users.data);

  return (
    <div className="space-y-4">
      <Link href="/admin" className="text-sm text-blue-800 hover:underline">← Exams</Link>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{e.subject} · {e.name}</h1>
          <p className="text-sm text-gray-600">{e.class_section} · max {e.max_marks} marks · {e.scripts} scripts</p>
        </div>
        <StatusBadge status={e.status} />
      </div>
      <details className="rounded-lg border border-gray-200 bg-white p-4 text-sm shadow-sm">
        <summary className="cursor-pointer font-medium">Marking scheme ({e.questions.length} questions)</summary>
        <ul className="mt-2 grid gap-1 sm:grid-cols-3">
          {e.questions.map((q) => (
            <li key={q.question}>Q{q.question}: {q.max_marks} marks · {q.qtype}
              {q.choice_group && <span className="text-gray-500"> · OR group {q.choice_group}</span>}</li>
          ))}
        </ul>
      </details>
      <ExportPanel exam={e} />
      {e.bundle_list.map((b) => <BundleRow key={b.id} bundle={b} teachers={teachers} />)}
      <NewBundle examId={e.id} teachers={teachers} />
    </div>
  );
}
