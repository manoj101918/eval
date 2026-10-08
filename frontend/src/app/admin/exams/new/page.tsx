"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button, Card, ErrorBox, Field, inputClass } from "@/components/ui";
import { useAdminMutation, type ExamDetail } from "@/lib/admin";
import { api } from "@/lib/api";

export default function NewExamPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [subject, setSubject] = useState("");
  const [classSection, setClassSection] = useState("");
  const [scheme, setScheme] = useState<File | null>(null);
  const create = useAdminMutation((form: FormData) => api<ExamDetail>("/admin/exams", { form }));

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!scheme) return;
    const form = new FormData();
    form.append("name", name.trim());
    form.append("subject", subject.trim());
    form.append("class_section", classSection.trim());
    form.append("scheme", scheme);
    create.mutate(form, { onSuccess: (exam) => router.push(`/admin/exams/${exam.id}`) });
  }

  return (
    <div className="max-w-xl space-y-4">
      <Link href="/admin" className="text-sm text-blue-800 hover:underline">← Exams</Link>
      <h1 className="text-2xl font-semibold">New exam</h1>
      <Card>
        <form onSubmit={submit} className="space-y-3">
          <Field label="Subject"><input className={inputClass} value={subject} required
            onChange={(e) => setSubject(e.target.value)} placeholder="Physics" /></Field>
          <Field label="Exam"><input className={inputClass} value={name} required
            onChange={(e) => setName(e.target.value)} placeholder="Mid-term 1" /></Field>
          <Field label="Class / section"><input className={inputClass} value={classSection} required
            onChange={(e) => setClassSection(e.target.value)} placeholder="II CSE-A" /></Field>
          <Field label="Marking scheme (.xlsx)"
            hint="One row per question or sub-part: max marks, type, correct option or model answer.">
            {/* No "required": the Create button stays disabled until a file is chosen. */}
            <input type="file" accept=".xlsx" aria-label="Marking scheme file"
              onChange={(e) => setScheme(e.target.files?.[0] ?? null)} className="text-sm" />
          </Field>
          <a href="/api/admin/scheme-template" className="block text-sm text-blue-800 hover:underline">
            Download a blank marking-scheme template
          </a>
          <ErrorBox error={create.error} />
          <Button type="submit" disabled={create.isPending || !scheme}>
            {create.isPending ? "Checking the scheme…" : "Create exam"}
          </Button>
        </form>
      </Card>
    </div>
  );
}
