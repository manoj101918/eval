"use client";

import Link from "next/link";
import { Card, ErrorBox, Spinner, StatusBadge } from "@/components/ui";
import { useExams } from "@/lib/admin";

export default function ExamsPage() {
  const exams = useExams();
  if (exams.isPending) return <Spinner />;
  if (exams.error) return <ErrorBox error={exams.error} />;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Exams</h1>
        <Link href="/admin/exams/new"
          className="rounded-md bg-blue-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-800">
          New exam
        </Link>
      </div>
      <Card className="overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-gray-600">
            <tr>
              <th className="px-4 py-2">Exam</th><th className="px-4 py-2">Class</th>
              <th className="px-4 py-2">Max marks</th><th className="px-4 py-2">Bundles</th>
              <th className="px-4 py-2">Scripts</th><th className="px-4 py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {exams.data.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-6 text-center text-gray-500">
                No exams yet. Create one with its marking scheme.
              </td></tr>
            )}
            {exams.data.map((e) => (
              <tr key={e.id} className="border-t border-gray-100">
                <td className="px-4 py-2">
                  <Link href={`/admin/exams/${e.id}`} className="font-medium text-blue-800 hover:underline">
                    {e.subject} · {e.name}
                  </Link>
                </td>
                <td className="px-4 py-2">{e.class_section}</td>
                <td className="px-4 py-2">{e.max_marks}</td>
                <td className="px-4 py-2">{e.bundles}</td>
                <td className="px-4 py-2">{e.scripts}</td>
                <td className="px-4 py-2"><StatusBadge status={e.status} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
